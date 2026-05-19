"""
richtext_import_utils.py
------------------------
Converts pasted HTML (or plain text) into a list of raw StreamField block dicts
that match Icarus's STREAM_BLOCKS definition.

Supported source → target mappings:
  <h1>-<h6>                → heading  (RichTextBlock)
  <p>                      → paragraph (RichTextBlock)
  <blockquote>             → blockquote (BlockQuoteBlock StructBlock)
  <ul> / <ol>              → paragraph with list HTML preserved as rich text
  <hr>                     → static (divider)
  <figure> + <img>         → image (ImageBlock) — downloads & imports into Wagtail
  bare <img>               → image (ImageBlock) — downloads & imports into Wagtail
  <figure> + <iframe/video>→ video (VideoBlock) with embed URL
  <iframe>                 → video or audio block depending on URL
  <audio>                  → audio (AudioBlock)
  <video src="...">        → video (VideoBlock)
  <pre> / <code>           → raw_html
  plain text lines         → paragraph blocks (split on blank lines)
  everything else          → paragraph (wrapped in <p>)

Image import behaviour:
  • External http/https URLs are downloaded and saved into the Wagtail image
    library (default collection). Duplicate detection is done by title.
  • data: URIs and relative paths fall back to raw_html.
  • If the download fails for any reason the <img> is kept as raw_html instead
    of breaking the whole import.

Dependencies:
  pip install beautifulsoup4 requests
"""

import os
import re
import uuid
import logging
from urllib.parse import urlparse, urljoin

logger = logging.getLogger(__name__)

try:
    from bs4 import BeautifulSoup, NavigableString, Tag
    BS4_AVAILABLE = True
except ImportError:
    BS4_AVAILABLE = False

try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False


# ── Constants ──────────────────────────────────────────────────────────────

YOUTUBE_RE    = re.compile(r'(youtube\.com/watch|youtu\.be/|youtube\.com/embed/)', re.I)
VIMEO_RE      = re.compile(r'vimeo\.com', re.I)
SOUNDCLOUD_RE = re.compile(r'soundcloud\.com', re.I)
SPOTIFY_RE    = re.compile(r'open\.spotify\.com', re.I)

AUDIO_EXTENSIONS = {'.mp3', '.wav', '.ogg', '.flac', '.aac', '.m4a', '.opus'}
VIDEO_EXTENSIONS = {'.mp4', '.webm', '.ogv', '.mov', '.avi', '.mkv'}

DOWNLOAD_TIMEOUT = 15       # seconds per image request
MAX_IMAGE_BYTES  = 20 * 1024 * 1024   # 20 MB guard


# ── Low-level helpers ──────────────────────────────────────────────────────

def _new_id():
    return str(uuid.uuid4())


def _is_blank(text):
    return not text.strip()


def _block(block_type, value):
    return {'type': block_type, 'value': value, 'id': _new_id()}


def _wrap_richtext(inner_html):
    return inner_html.strip()


def _heading_richtext(tag):
    level = tag.name
    if level == 'h1':
        level = 'h2'
    elif level in ('h5', 'h6'):
        level = 'h4'
    inner = tag.decode_contents().strip()
    return f'<{level}>{inner}</{level}>'


def _ext(url):
    path = urlparse(url).path
    _, ext = os.path.splitext(path)
    return ext.lower()


def _filename_from_url(url, fallback='imported_image'):
    path = urlparse(url).path
    name = os.path.basename(path) or fallback
    return name.split('?')[0] or fallback


# ── Image importing ────────────────────────────────────────────────────────

def _import_image_from_url(src, alt='', base_url=None):
    """
    Download an external image and save it as a Wagtail Image object.
    Returns the Wagtail Image pk on success, or None on any failure.
    """
    if not REQUESTS_AVAILABLE:
        logger.warning('richtext_import: requests not installed; skipping image download')
        return None

    if not src or src.startswith('data:'):
        return None

    # Resolve relative URLs
    if base_url and not src.startswith(('http://', 'https://')):
        src = urljoin(base_url, src)

    if not src.startswith(('http://', 'https://')):
        return None

    try:
        from django.core.files.base import ContentFile
        from wagtail.images import get_image_model

        WagtailImage = get_image_model()
        title = alt.strip() or _filename_from_url(src)

        # Deduplicate by title — reuse if already imported
        existing = WagtailImage.objects.filter(title=title).first()
        if existing:
            return existing.pk

        response = requests.get(src, timeout=DOWNLOAD_TIMEOUT, stream=True)
        response.raise_for_status()

        # Size guard
        content_length = int(response.headers.get('Content-Length', 0))
        if content_length > MAX_IMAGE_BYTES:
            logger.warning('richtext_import: image too large (%d bytes), skipping %s', content_length, src)
            return None

        raw = response.content
        if len(raw) > MAX_IMAGE_BYTES:
            logger.warning('richtext_import: downloaded image too large, skipping %s', src)
            return None

        # Infer extension from Content-Type if missing from URL
        filename = _filename_from_url(src)
        if not os.path.splitext(filename)[1]:
            content_type = response.headers.get('Content-Type', '')
            ext_map = {
                'image/jpeg': '.jpg', 'image/png': '.png',
                'image/gif':  '.gif', 'image/webp': '.webp',
                'image/svg+xml': '.svg',
            }
            filename += ext_map.get(content_type.split(';')[0].strip(), '.jpg')

        image = WagtailImage(title=title)
        image.file.save(filename, ContentFile(raw), save=True)
        logger.info('richtext_import: imported image "%s" (pk=%d)', title, image.pk)
        return image.pk

    except Exception as exc:
        logger.warning('richtext_import: failed to import image from %s — %s', src, exc)
        return None


def _image_block_from_url(src, alt='', caption='', base_url=None):
    """
    Try to import the image into Wagtail and return an ImageBlock dict.
    Falls back to raw_html if download/import fails so nothing is silently lost.
    """
    pk = _import_image_from_url(src, alt=alt, base_url=base_url)
    if pk:
        return _block('image', {
            'image':     pk,
            'caption':   f'<p>{caption}</p>' if caption else '',
            'alignment': 'full',
            'size':      'medium',
        })
    # Fallback — keep the original tag so the editor can see it
    alt_attr = f' alt="{alt}"' if alt else ''
    return _block('raw_html', f'<img src="{src}"{alt_attr}>')


# ── Embed / media helpers ──────────────────────────────────────────────────

def _url_from_iframe(el):
    return el.get('src', '').strip()


def _embed_block_for_url(url, caption=''):
    """Route a URL to the right block type based on extension or domain."""
    if not url:
        return None

    ext = _ext(url)

    if ext in VIDEO_EXTENSIONS or YOUTUBE_RE.search(url) or VIMEO_RE.search(url):
        return _block('video', {
            'video_file':      None,
            'video_embed_url': url,
            'caption':         caption,
        })

    if ext in AUDIO_EXTENSIONS or SOUNDCLOUD_RE.search(url) or SPOTIFY_RE.search(url):
        return _block('audio', {
            'audio_file':      None,
            'audio_embed_url': url,
            'caption':         caption,
        })

    # Generic Wagtail embed (handles oEmbed providers like YouTube, Vimeo automatically)
    return _block('embed', url)


# ── <figure> handler ──────────────────────────────────────────────────────

def _handle_figure(el, base_url=None):
    """
    <figure> wraps an <img>, <video>, <audio>, or <iframe>.
    Extracts <figcaption> as caption.
    """
    figcaption = el.find('figcaption')
    caption = figcaption.get_text(strip=True) if figcaption else ''
    if figcaption:
        figcaption.extract()

    img    = el.find('img')
    iframe = el.find('iframe')
    video  = el.find('video')
    audio  = el.find('audio')

    if img:
        src = img.get('src', '').strip()
        alt = img.get('alt', '').strip()
        return _image_block_from_url(src, alt=alt, caption=caption, base_url=base_url)

    if iframe:
        url = _url_from_iframe(iframe)
        return _embed_block_for_url(url, caption=caption)

    if video:
        src = video.get('src', '') or (video.find('source') or {}).get('src', '')
        src = src.strip()
        if src:
            return _block('video', {'video_file': None, 'video_embed_url': src, 'caption': caption})

    if audio:
        src = audio.get('src', '') or (audio.find('source') or {}).get('src', '')
        src = src.strip()
        if src:
            return _block('audio', {'audio_file': None, 'audio_embed_url': src, 'caption': caption})

    inner = el.decode_contents().strip()
    return _block('raw_html', f'<figure>{inner}</figure>') if inner else None


# ── <p> handler: may contain a bare <img> ─────────────────────────────────

def _handle_paragraph(el, base_url=None):
    """
    A <p> that contains only an <img> → ImageBlock.
    A <p> that mixes text and images → interleaved paragraph + image blocks.
    A <p> with no images → single paragraph block.
    """
    imgs = el.find_all('img')
    if not imgs:
        inner = el.decode_contents().strip()
        return [_block('paragraph', _wrap_richtext(f'<p>{inner}</p>'))] if inner else []

    result       = []
    pending_html = ''

    for child in el.children:
        if isinstance(child, NavigableString):
            pending_html += str(child)
        elif isinstance(child, Tag) and child.name == 'img':
            # Flush pending text
            text = pending_html.strip()
            if text:
                result.append(_block('paragraph', _wrap_richtext(f'<p>{text}</p>')))
                pending_html = ''
            src = child.get('src', '').strip()
            alt = child.get('alt', '').strip()
            result.append(_image_block_from_url(src, alt=alt, base_url=base_url))
        else:
            pending_html += str(child)

    text = pending_html.strip()
    if text:
        result.append(_block('paragraph', _wrap_richtext(f'<p>{text}</p>')))

    return result


# ── Main entry point ───────────────────────────────────────────────────────

def html_to_stream_blocks(raw_text, base_url=None):
    """
    Convert raw_text (HTML or plain text) to a list of StreamField block dicts.

    base_url: optional base URL string used to resolve relative image paths
              (useful if pasting from a specific website).
    """
    if not raw_text or not raw_text.strip():
        return []

    if not re.search(r'<[a-zA-Z]', raw_text):
        return _plain_text_to_blocks(raw_text)

    if not BS4_AVAILABLE:
        return [_block('raw_html', raw_text)]

    return _html_to_blocks(raw_text, base_url=base_url)


def _plain_text_to_blocks(text):
    blocks = []
    for para in re.split(r'\r?\n(?:\s*\r?\n)+', text.strip()):
        para = para.strip()
        if para:
            html = '<p>' + para.replace('\n', '<br/>') + '</p>'
            blocks.append(_block('paragraph', _wrap_richtext(html)))
    return blocks


def _html_to_blocks(html, base_url=None):
    soup = BeautifulSoup(html, 'html.parser')
    root = soup.find('body') or soup
    result = []

    for el in root.children:
        if isinstance(el, NavigableString):
            text = str(el).strip()
            if text:
                result.append(_block('paragraph', _wrap_richtext(f'<p>{text}</p>')))
            continue

        if not isinstance(el, Tag):
            continue

        tag = el.name.lower()

        # ── Headings ──────────────────────────────────────────────────────
        if tag in ('h1', 'h2', 'h3', 'h4', 'h5', 'h6'):
            if not _is_blank(el.get_text()):
                result.append(_block('heading', _wrap_richtext(_heading_richtext(el))))

        # ── Paragraphs (may contain bare <img>) ───────────────────────────
        elif tag == 'p':
            result.extend(_handle_paragraph(el, base_url=base_url))

        # ── Figures ───────────────────────────────────────────────────────
        elif tag == 'figure':
            b = _handle_figure(el, base_url=base_url)
            if b:
                result.append(b)

        # ── Bare <img> (outside p / figure) ──────────────────────────────
        elif tag == 'img':
            src = el.get('src', '').strip()
            alt = el.get('alt', '').strip()
            result.append(_image_block_from_url(src, alt=alt, base_url=base_url))

        # ── <iframe> ──────────────────────────────────────────────────────
        elif tag == 'iframe':
            url = _url_from_iframe(el)
            b = _embed_block_for_url(url)
            if b:
                result.append(b)

        # ── <video> ───────────────────────────────────────────────────────
        elif tag == 'video':
            src = el.get('src', '').strip()
            if not src:
                source = el.find('source')
                src = source.get('src', '').strip() if source else ''
            if src:
                result.append(_block('video', {
                    'video_file': None, 'video_embed_url': src, 'caption': '',
                }))

        # ── <audio> ───────────────────────────────────────────────────────
        elif tag == 'audio':
            src = el.get('src', '').strip()
            if not src:
                source = el.find('source')
                src = source.get('src', '').strip() if source else ''
            if src:
                result.append(_block('audio', {
                    'audio_file': None, 'audio_embed_url': src, 'caption': '',
                }))

        # ── Blockquotes ───────────────────────────────────────────────────
        elif tag == 'blockquote':
            cite_tag = el.find(['cite', 'footer'])
            attribution = ''
            if cite_tag:
                attribution = cite_tag.get_text(strip=True).lstrip('—–- ')
                cite_tag.extract()
            quote_text = el.get_text(strip=True)
            if quote_text:
                result.append(_block('blockquote', {
                    'text':           f'<p>{quote_text}</p>',
                    'attribute_name': attribution,
                }))

        # ── Lists ─────────────────────────────────────────────────────────
        elif tag in ('ul', 'ol'):
            result.append(_block('paragraph', _wrap_richtext(str(el))))

        # ── Divider ───────────────────────────────────────────────────────
        elif tag == 'hr':
            result.append(_block('static', None))

        # ── Code blocks ───────────────────────────────────────────────────
        elif tag in ('pre', 'code'):
            result.append(_block('raw_html', str(el)))

        # ── Wrappers (div, section, article, main) ────────────────────────
        elif tag in ('div', 'section', 'article', 'main'):
            inner_html = el.decode_contents()
            if inner_html.strip():
                result.extend(_html_to_blocks(inner_html, base_url=base_url))

        # ── Catch-all ─────────────────────────────────────────────────────
        else:
            inner = el.decode_contents().strip()
            if inner:
                result.append(_block('paragraph', _wrap_richtext(f'<p>{inner}</p>')))

    return result
