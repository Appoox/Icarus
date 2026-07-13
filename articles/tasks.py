# articles/tasks.py
import os
import re
import html
import logging
from django.conf import settings
from django.core.files.base import ContentFile
from django.utils.html import strip_tags
from google.api_core import exceptions as gax_exceptions
from google.cloud import texttospeech
from wagtail.documents import get_document_model
from wagtail.signals import page_published
from Icarus.settings.base import GOOGLE_CREDENTIALS_JSON

logger = logging.getLogger(__name__)

# Chirp3-HD voices reject requests containing any individual sentence that is
# "too long" — a per-sentence limit enforced separately from the 5000-byte
# request limit. The threshold is undocumented, so stay conservative. 200
# Malayalam chars ≈ 600 bytes UTF-8, comfortably inside anything Chirp accepts.
MAX_SENTENCE_CHARS = 200
SENTENCE_ENDINGS = ('.', '!', '?', '।', '॥')

# ── Chirp3-HD pause control ──────────────────────────────────────────────
# Chirp3-HD largely ignores punctuation-based pauses in the `text` input
# field. Real pause control uses [pause short] / [pause] / [pause long]
# tags, which work ONLY in the `markup` input field. The model may
# occasionally disregard a tag, and adjacent tags are known to cause
# brief garbled audio — so tags are inserted between speech units, never
# back to back, and stripped from chunk edges.
PAUSE_SHORT = '[pause short]'
PAUSE = '[pause]'
PAUSE_LONG = '[pause long]'
_PAUSE_TAGS = {PAUSE_SHORT, PAUSE, PAUSE_LONG}
_PAUSE_TAG_RE = re.compile(r'\s*\[pause(?: short| long)?\]\s*')

# Insert [pause short] after mid-sentence commas. Flip to False if a voice
# handles a particular article badly (the model occasionally trips on tags).
COMMA_PAUSES = True

HEADING_BLOCK_TYPES = ('heading', 'colored_heading')

_MALAYALAM_RE = re.compile(r'[\u0d00-\u0d7f]')


def _clean_for_speech(text, is_malayalam):
    """
    Rewrite visual print conventions into listenable text:

    • Unescape HTML entities left behind by strip_tags (&amp; → &) and
      normalize non-breaking spaces.
    • Brackets in Malayalam text containing NO Malayalam script are
      glosses of the word they follow — 'കോർഡിനേറ്റുകൾ (coordinates)' —
      and are dropped, since reading both says the same word twice.
    • Brackets with Malayalam content — '(ജി പി എസ്സ്)' — carry meaning
      and become comma appositives, earning a natural pause.
    • Slashes between words are alternatives — 'വീടുകൾ/കെട്ടിടങ്ങൾ' —
      and become comma lists.
    """
    text = html.unescape(text).replace('\xa0', ' ')

    def _bracket(match):
        inner = match.group(1).strip()
        if not inner:
            return ' '
        if is_malayalam and not _MALAYALAM_RE.search(inner):
            return ' '                # Latin-script gloss — silent in audio
        return f', {inner}, '         # meaningful parenthetical — appositive

    text = re.sub(r'[(\[]([^()\[\]]*)[)\]]', _bracket, text)
    text = re.sub(r'(?<=[^\W\d])\s*/\s*(?=[^\W\d])', ', ', text)
    text = re.sub(r',(\s*,)+', ',', text)          # collapse comma runs
    text = re.sub(r'\s+([,.!?;])', r'\1', text)    # no space before punctuation
    text = re.sub(r'[ \t]{2,}', ' ', text)
    return text.strip(' ,;')


def _with_comma_pauses(sentence):
    """Insert [pause short] after mid-sentence commas."""
    return re.sub(r',\s+', f', {PAUSE_SHORT} ', sentence)


def _synthesize_chunk(client, chunk, voice_params, audio_config):
    """
    Synthesize via the `markup` field so pause tags take effect. Falls back
    to plain `text` input (tags stripped) if the installed client library
    predates the markup field (ValueError at construction) or the locale
    rejects pause tags (InvalidArgument from the API).
    """
    try:
        return client.synthesize_speech(
            input=texttospeech.SynthesisInput(markup=chunk),
            voice=voice_params,
            audio_config=audio_config,
        )
    except (ValueError, gax_exceptions.InvalidArgument) as exc:
        logger.warning(
            "Markup synthesis unavailable (%s); retrying chunk as plain "
            "text without pause tags.", exc,
        )
        plain = _PAUSE_TAG_RE.sub(' ', chunk)
        plain = re.sub(r'[ \t]{2,}', ' ', plain).strip()
        return client.synthesize_speech(
            input=texttospeech.SynthesisInput(text=plain),
            voice=voice_params,
            audio_config=audio_config,
        )


def _normalize_sentences(text):
    """
    Split text into sentences and force-break any exceeding
    MAX_SENTENCE_CHARS, preferring comma/semicolon boundaries, then spaces,
    so Chirp3-HD never sees an over-long sentence. Every returned sentence
    is terminated with sentence-ending punctuation.
    """
    out = []
    for sent in re.split(r'(?<=[.!?।॥])\s+', text):
        sent = sent.strip()
        while len(sent) > MAX_SENTENCE_CHARS:
            window = sent[:MAX_SENTENCE_CHARS]
            cut = max(window.rfind(','), window.rfind(';'))
            if cut < MAX_SENTENCE_CHARS // 4:      # no useful punctuation near the cap
                cut = window.rfind(' ')
            if cut <= 0:                           # unbroken run — hard cut
                cut = MAX_SENTENCE_CHARS
            fragment = sent[:cut].strip().rstrip(',;')
            if fragment:
                out.append(fragment + '.')
            sent = sent[cut:].lstrip(' ,;')
        if sent:
            if not sent.endswith(SENTENCE_ENDINGS):
                sent += '.'
            out.append(sent)
    return out


def generate_article_audio_task(article_id):
    from .models import Article
    from .signals import on_article_published  # Import the signal receiver to temporarily disconnect

    try:
        article = Article.objects.get(pk=article_id)
    except Article.DoesNotExist:
        logger.warning(f"Article with ID {article_id} does not exist.")
        return

    # 1. Extract raw text per block, keeping block boundaries and types so
    #    pauses can be inserted between them later. Every block is
    #    terminated with sentence punctuation so titles and headings don't
    #    fuse with the following paragraph into one giant "sentence".
    blocks = []  # (block_type, text)
    title_text = (article.title or '').strip()
    if title_text:
        blocks.append(('title', title_text))
    if article.body:
        for block in article.body:
            if block.block_type in ['paragraph', 'text', 'rich_text', 'heading', 'colored_heading', 'blockquote']:
                if block.block_type == 'blockquote':
                    block_text = str(block.value.get('text', ''))
                    attrib = block.value.get('attribute_name', '')
                    if attrib:
                        block_text += f" - {attrib}"
                else:
                    block_text = str(block.value)

                cleaned = strip_tags(block_text).strip()
                if cleaned:
                    blocks.append((block.block_type, cleaned))

    full_text = " ".join(text for _, text in blocks).strip()
    if not full_text:
        logger.info(f"Article {article.id} has no synthesizable text.")
        return

    # 2. Select appropriate Chirp 3 voice.
    has_malayalam = bool(_MALAYALAM_RE.search(full_text))
    if has_malayalam:
        lang_code = "ml-IN"
        voice_name = "ml-IN-Chirp3-HD-Charon"  # Premium Generative Chirp3 voice
    else:
        lang_code = "en-US"
        voice_name = "en-US-Chirp3-HD-Charon"

    # 2b. Build the flat list of speech units: cleaned, length-capped
    #     sentences, with pause tags between blocks. Titles and headings
    #     get a long pause after them; paragraph boundaries a normal one.
    #     Tags are separate units so the chunk packer can keep them off
    #     chunk edges and never emit two in a row.
    units = []
    for block_type, block_text in blocks:
        cleaned = _clean_for_speech(block_text, has_malayalam)
        if not cleaned:
            continue
        if not cleaned.endswith(SENTENCE_ENDINGS):
            cleaned += '.'
        for sent in _normalize_sentences(cleaned):
            units.append(_with_comma_pauses(sent) if COMMA_PAUSES else sent)
        pause = PAUSE_LONG if block_type in ('title',) + HEADING_BLOCK_TYPES else PAUSE
        units.append(pause)

    while units and units[-1] in _PAUSE_TAGS:
        units.pop()  # no trailing pause at end of article

    if not units:
        logger.info(f"Article {article.id} has no synthesizable text after cleaning.")
        return

    try:
        credentials_path = GOOGLE_CREDENTIALS_JSON

        if credentials_path and os.path.exists(credentials_path):
            client = texttospeech.TextToSpeechClient.from_service_account_json(credentials_path)
        else:
            client = texttospeech.TextToSpeechClient()

        # 3. Pack whole speech units into byte-safe chunks (targeting 4500
        #    bytes to leave a buffer under GCP's 5000-byte request limit).
        #    No chunk ever splits mid-sentence, no sentence exceeds
        #    Chirp3-HD's per-sentence limit, and pause tags never sit at a
        #    chunk edge — a chunk boundary is already a pause, and edge/
        #    adjacent tags are the known garbled-audio trigger.
        chunks = []
        current_chunk = []
        current_byte_len = 0

        def _flush():
            nonlocal current_chunk, current_byte_len
            while current_chunk and current_chunk[-1] in _PAUSE_TAGS:
                current_chunk.pop()  # drop trailing pause at chunk edge
            if current_chunk:
                chunks.append("\n".join(current_chunk))
            current_chunk = []
            current_byte_len = 0

        for unit in units:
            if unit in _PAUSE_TAGS and not current_chunk:
                continue  # never start a chunk with a pause tag

            unit_byte_len = len(unit.encode('utf-8'))
            joiner_byte_len = 1 if current_chunk else 0

            if current_byte_len + unit_byte_len + joiner_byte_len > 4500:
                _flush()
                if unit in _PAUSE_TAGS:
                    continue
                current_chunk = [unit]
                current_byte_len = unit_byte_len
            else:
                current_chunk.append(unit)
                current_byte_len += unit_byte_len + joiner_byte_len

        _flush()

        combined_audio_bytes = bytearray()

        # 4. Request speech synthesis for each byte-safe chunk. Uses the
        #    markup input field so pause tags take effect, with automatic
        #    fallback to plain text if the library or locale rejects it.
        voice_params = texttospeech.VoiceSelectionParams(
            language_code=lang_code,
            name=voice_name
        )
        audio_config = texttospeech.AudioConfig(
            audio_encoding=texttospeech.AudioEncoding.MP3
        )

        for chunk in chunks:
            response = _synthesize_chunk(client, chunk, voice_params, audio_config)
            combined_audio_bytes.extend(response.audio_content)

        if not combined_audio_bytes:
            logger.warning(f"No audio content returned from Google TTS API for article {article_id}.")
            return

        # 5. Save the generated audio as a Wagtail Document
        Document = get_document_model()

        if article.audio_file:
            old_doc = article.audio_file
            if old_doc.title.startswith("TTS Audio - "):
                old_doc.delete()

        audio_file_content = ContentFile(bytes(combined_audio_bytes), name=f"tts_article_{article.id}.mp3")
        doc = Document(
            title=f"TTS Audio - {article.title}",
            file=audio_file_content
        )
        doc.save()

        # 6. Bind document and publish changes securely
        article.audio_file = doc
        article.save()

        page_published.disconnect(on_article_published, sender=Article)
        try:
            revision = article.save_revision()
            revision.publish()
        finally:
            page_published.connect(on_article_published, sender=Article)

        logger.info(f"Successfully generated and published audio for Article {article.id}.")

    except Exception as e:
        logger.error(f"Failed to generate TTS audio for Article {article.id}: {str(e)}")


def lock_old_pages_task(days=None):
    """
    Lock Article and Issue pages whose first_published_at is older than
    `days` days ago.  Locked pages require an explicit unlock in the Wagtail
    admin before they can be edited — this guards published archive content
    against accidental modification.

    Designed to run on a nightly Django-Q2 schedule.  Set up the schedule
    once with:
        python manage.py setup_page_lock_schedule

    Or trigger ad-hoc:
        python manage.py lock_old_pages
        python manage.py lock_old_pages --days 30 --async

    Args:
        days (int | None):
            Lock pages published this many days ago or earlier.
            Defaults to settings.PAGE_LOCK_DAYS (10).

    Returns:
        dict:
            articles_locked  — number of Article pages locked this run
            issues_locked    — number of Issue pages locked this run
            cutoff_date      — ISO date string (YYYY-MM-DD) of the cutoff
            threshold_days   — the effective `days` value used
    """
    from datetime import timedelta
    from django.utils import timezone
    from wagtail.models import Page

    if days is None:
        days = getattr(settings, 'PAGE_LOCK_DAYS', 10)

    cutoff = timezone.now() - timedelta(days=days)
    now = timezone.now()

    # Deferred to avoid circular imports at module load time.
    from articles.models import Article
    from issue.models import Issue

    # ── Gather PKs ──────────────────────────────────────────────────────
    # Filter only live, unlocked pages that have a confirmed publish timestamp.
    # Collecting PKs first (values_list) keeps this cheap — no ORM instance
    # construction until the bulk update.

    article_pks = list(
        Article.objects.live()
        .filter(
            locked=False,
            first_published_at__isnull=False,
            first_published_at__lte=cutoff,
        )
        .values_list('pk', flat=True)
    )

    issue_pks = list(
        Issue.objects.live()
        .filter(
            locked=False,
            first_published_at__isnull=False,
            first_published_at__lte=cutoff,
        )
        .values_list('pk', flat=True)
    )

    # ── Bulk update via the base Page table ─────────────────────────────
    # Updating through Page.objects (not Article/Issue managers) writes only
    # to wagtailcore_page — no MTI child-table writes, one query per type.
    # locked_by_id=None marks these as system locks (no individual user
    # responsible), which Wagtail renders as "Locked" without a username.

    article_count = Page.objects.filter(pk__in=article_pks).update(
        locked=True,
        locked_at=now,
        locked_by_id=None,
    )

    issue_count = Page.objects.filter(pk__in=issue_pks).update(
        locked=True,
        locked_at=now,
        locked_by_id=None,
    )

    logger.info(
        "lock_old_pages_task: locked %d article(s) and %d issue(s) "
        "(cutoff: %s, threshold: %d day(s)).",
        article_count,
        issue_count,
        cutoff.date(),
        days,
    )

    return {
        "articles_locked": article_count,
        "issues_locked": issue_count,
        "cutoff_date": str(cutoff.date()),
        "threshold_days": days,
    }