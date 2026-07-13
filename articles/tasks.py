# articles/tasks.py
import os
import re
import logging
from django.conf import settings
from django.core.files.base import ContentFile
from django.utils.html import strip_tags
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

    # 1. Extract clean text from Title and StreamField text blocks.
    #    Every part is terminated with sentence punctuation so titles and
    #    headings don't fuse with the following paragraph into one giant
    #    "sentence" when joined.
    text_parts = []
    title_text = (article.title or '').strip()
    if title_text:
        if not title_text.endswith(SENTENCE_ENDINGS):
            title_text += '.'
        text_parts.append(title_text)
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
                    if not cleaned.endswith(SENTENCE_ENDINGS):
                        cleaned += '.'
                    text_parts.append(cleaned)

    full_text = " ".join(text_parts).strip()
    if not full_text:
        logger.info(f"Article {article.id} has no synthesizable text.")
        return

    # 2. Select appropriate Chirp 3 voice.
    has_malayalam = any('\u0d00' <= char <= '\u0d7f' for char in full_text)
    if has_malayalam:
        lang_code = "ml-IN"
        voice_name = "ml-IN-Chirp3-HD-Charon"  # Premium Generative Chirp3 voice
    else:
        lang_code = "en-US"
        voice_name = "en-US-Chirp3-HD-Charon"

    try:
        credentials_path = GOOGLE_CREDENTIALS_JSON

        if credentials_path and os.path.exists(credentials_path):
            client = texttospeech.TextToSpeechClient.from_service_account_json(credentials_path)
        else:
            client = texttospeech.TextToSpeechClient()

        # 3. Normalize into length-capped sentences, then pack whole
        #    sentences into byte-safe chunks (targeting 4500 bytes to leave
        #    a buffer under GCP's 5000-byte request limit). Packing whole
        #    sentences means no chunk ever splits mid-sentence, and the
        #    normalizer guarantees no sentence exceeds Chirp3-HD's
        #    per-sentence limit.
        sentences = _normalize_sentences(full_text)

        chunks = []
        current_chunk = []
        current_byte_len = 0

        for sent in sentences:
            sent_byte_len = len(sent.encode('utf-8'))

            # Plus 1 byte for the space used when joining sentences
            space_byte_len = 1 if current_chunk else 0

            if current_byte_len + sent_byte_len + space_byte_len > 4500:
                # Store the full chunk and start a new one
                chunks.append(" ".join(current_chunk))
                current_chunk = [sent]
                current_byte_len = sent_byte_len
            else:
                current_chunk.append(sent)
                current_byte_len += sent_byte_len + space_byte_len

        if current_chunk:
            chunks.append(" ".join(current_chunk))

        combined_audio_bytes = bytearray()

        # 4. Request speech synthesis for each byte-safe chunk
        for chunk in chunks:
            synthesis_input = texttospeech.SynthesisInput(text=chunk)
            voice_params = texttospeech.VoiceSelectionParams(
                language_code=lang_code,
                name=voice_name
            )
            audio_config = texttospeech.AudioConfig(
                audio_encoding=texttospeech.AudioEncoding.MP3
            )

            response = client.synthesize_speech(
                input=synthesis_input,
                voice=voice_params,
                audio_config=audio_config
            )
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