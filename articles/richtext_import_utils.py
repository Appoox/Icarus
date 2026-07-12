"""
richtext_import_utils.py
------------------------
Convert pasted HTML (from Word / Google Docs / a web page) or plain text into a
list of raw StreamField block dicts matching Icarus's STREAM_BLOCKS.

Why the old version could not separate paragraphs
=================================================
The import block renders a plain <textarea>. A bare textarea keeps only the
clipboard's ``text/plain`` payload on paste, so all HTML structure (headings,
paragraphs, images, bold/italic) is gone *before* this module runs, and the
remaining text usually has a single '\\n' between paragraphs. The companion
admin widget (``RichTextImportTextarea`` + ``rich_text_import_paste.js``) fixes
that at the source: it grabs ``text/html`` from the clipboard on paste, so in
normal operation this module receives real structured HTML and the walker below
can emit proper heading / paragraph / image / list / quote blocks.

Output shape (StreamField raw JSON)::

    {"type": <block_type>, "value": <value>, "id": <uuid4>}

Rich-text values (paragraph / heading / list / blockquote) are sanitised down to
the feature set Wagtail's default RichTextBlock accepts (b, i, a, br, sub, sup,
ul/ol/li, h2-h4) so Draftail does not silently drop content when the expanded
blocks are opened for editing. Anything that cannot be represented as a real
block falls back to ``raw_html`` rather than being lost.

Dependencies:  pip install beautifulsoup4 requests
"""

import os
import re
import uuid
import logging
import hashlib
from html import escape
from urllib.parse import urlparse, urljoin

logger = logging.getLogger(__name__)

try:
    from bs4 import BeautifulSoup, NavigableString, Tag, Comment
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

IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.svg', '.bmp', '.tiff', '.avif'}
AUDIO_EXTENSIONS = {'.mp3', '.wav', '.ogg', '.flac', '.aac', '.m4a', '.opus'}
VIDEO_EXTENSIONS = {'.mp4', '.webm', '.ogv', '.mov', '.avi', '.mkv'}

DOWNLOAD_TIMEOUT = 15
MAX_IMAGE_BYTES  = 20 * 1024 * 1024   # 20 MB guard

# The block type used for a horizontal rule. 'raw_html' works with the current
# STREAM_BLOCKS out of the box; switch to 'divider' if you add the DividerBlock.
HR_BLOCK_TYPE = 'raw_html'

# ── Heading recovery (opt-in) ───────────────────────────────────────────────
# Many source documents (Word/Docs exports pasted in) carry NO heading styles —
# section titles are just short standalone paragraphs. When this is on, a short
# paragraph that sits *between two long body paragraphs* is promoted to an <h2>.
# It is deliberately conservative: it only fires when both neighbours are long
# body paragraphs, so bylines, datelines and trailing contact lines (which are
# not flanked that way) are left alone. Off by default so clean pastes that
# already carry real <h2>/<h3> are never second-guessed; turn it on if your
# authors submit style-less documents.
HEADING_DETECTION    = True   # set True to recover headings from short lines
HEADING_MAX_LEN      = 80      # a heading candidate is at most this many chars
HEADING_BODY_MIN_LEN = 120     # a neighbour counts as "body" past this length
HEADING_LEVEL        = 'h2'    # tag emitted for a recovered heading

# ── Tables ──────────────────────────────────────────────────────────────────
# When on, a pasted <table> becomes a native, editable Wagtail TableBlock.
# Tables with merged cells (colspan/rowspan) can't be represented by the contrib
# TableBlock, so those fall back to a raw_html block that preserves the merges.
TABLE_AS_NATIVE_BLOCK = True

# Tags that are block-level boundaries when walking the tree.
HEADING_TAGS = {'h1', 'h2', 'h3', 'h4', 'h5', 'h6'}
WRAPPER_TAGS = {'div', 'section', 'article', 'main', 'header', 'footer',
                'aside', 'body', 'html', 'center', 'span'}

# Inline tags we keep inside rich-text values. Everything else is unwrapped
# (its text kept) so Draftail does not choke on unknown markup.
INLINE_KEEP = {'b', 'i', 'a', 'br', 'sub', 'sup'}

_DOUBLE_BR   = re.compile(r'(?:<br\s*/?>\s*){2,}', re.I)
_EDGE_BR     = re.compile(r'^(?:\s*<br\s*/?>\s*)+|(?:\s*<br\s*/?>\s*)+$', re.I)
_WS          = re.compile(r'\s+')
_FRAGMENT_RE = re.compile(r'<!--\s*StartFragment\s*-->(.*?)<!--\s*EndFragment\s*-->',
                          re.I | re.S)


# ── Low-level helpers ──────────────────────────────────────────────────────

def _new_id():
    return str(uuid.uuid4())


def _block(block_type, value):
    return {'type': block_type, 'value': value, 'id': _new_id()}


def _ext(url):
    return os.path.splitext(urlparse(url).path)[1].lower()


def _filename_from_url(url, fallback='imported_image'):
    name = os.path.basename(urlparse(url).path) or fallback
    return name.split('?')[0] or fallback


def _valid_link(href):
    if not href:
        return False
    href = href.strip()
    if href.startswith(('http://', 'https://', 'mailto:', 'tel:', '/', '#')):
        return True
    return False


# ── Inline sanitisation ────────────────────────────────────────────────────

def _promote_styles(soup):
    """
    Turn Word / Google-Docs style markup into semantic tags:
      * <b|strong style="font-weight:normal|400"> ... </>  → unwrap (GDocs wraps
        the *entire* pasted body in a normal-weight <b>; keeping it would make
        everything bold).
      * <span|font style="font-weight:700|bold">          → <b>
      * <span|font style="font-style:italic">             → <i>
    """
    for tag in soup.find_all(['b', 'strong']):
        style = (tag.get('style') or '').lower().replace(' ', '')
        if 'font-weight:normal' in style or 'font-weight:400' in style:
            tag.unwrap()

    for tag in soup.find_all(['i', 'em']):
        style = (tag.get('style') or '').lower().replace(' ', '')
        if 'font-style:normal' in style:
            tag.unwrap()

    for tag in soup.find_all(['span', 'font']):
        style = (tag.get('style') or '').lower().replace(' ', '')
        bold = any(w in style for w in
                   ('font-weight:600', 'font-weight:700', 'font-weight:800',
                    'font-weight:900', 'font-weight:bold', 'font-weight:bolder'))
        italic = 'font-style:italic' in style
        tag.attrs = {}
        if bold or italic:
            outer = soup.new_tag('b') if bold else soup.new_tag('i')
            target = outer
            if bold and italic:
                inner = soup.new_tag('i')
                outer.append(inner)
                target = inner
            for child in list(tag.contents):
                target.append(child.extract())
            tag.append(outer)
        tag.unwrap()


def _clean_inline(html, base_url=None):
    """Sanitise a fragment of inline HTML down to Wagtail's rich-text features."""
    if not html:
        return ''
    soup = BeautifulSoup(html, 'html.parser')
    _promote_styles(soup)

    for img in soup.find_all('img'):
        img.decompose()
    for tag in soup.find_all('strong'):
        tag.name = 'b'
    for tag in soup.find_all('em'):
        tag.name = 'i'

    for a in soup.find_all('a'):
        href = (a.get('href') or '').strip()
        if base_url and href and not href.startswith(('http://', 'https://', 'mailto:', 'tel:', '#')):
            href = urljoin(base_url, href)
        a.attrs = {}
        if _valid_link(href):
            a['href'] = href
        else:
            a.unwrap()

    for tag in soup.find_all(True):
        name = tag.name.lower()
        if name in INLINE_KEEP:
            if name != 'a':
                tag.attrs = {}
        else:
            tag.unwrap()

    return _WS.sub(' ', str(soup)).strip()


def _has_text(fragment):
    return bool(BeautifulSoup(fragment, 'html.parser').get_text(strip=True))


def _inline_to_paragraph_htmls(html, base_url=None):
    """
    Clean an inline HTML run and split it into one or more '<p>…</p>' strings,
    breaking on double <br> (a very common 'paragraph break' in pasted text).
    """
    cleaned = _clean_inline(html, base_url=base_url)
    out = []
    for part in _DOUBLE_BR.split(cleaned):
        part = _EDGE_BR.sub('', part).strip()
        if part and _has_text(part):
            out.append(f'<p>{part}</p>')
    return out


# ── Image importing ────────────────────────────────────────────────────────

def _import_image_from_url(src, alt='', base_url=None, cache=None):
    """
    Download an external image and save it as a Wagtail Image.
    Returns the image pk on success, or None on any failure.

    Deduplication is content-based (Wagtail's SHA-1 ``file_hash``) so two images
    that merely share alt text or a common filename like 'image.jpg' are never
    wrongly merged — the old title-based dedup could silently reuse the wrong
    image. A per-run ``cache`` keyed by URL avoids re-downloading the same image
    twice within a single paste.
    """
    if cache is None:
        cache = {}
    if not REQUESTS_AVAILABLE or not src or src.startswith('data:'):
        return None

    if base_url and not src.startswith(('http://', 'https://')):
        src = urljoin(base_url, src)
    if not src.startswith(('http://', 'https://')):
        return None
    if src in cache:
        return cache[src]

    try:
        from django.core.files.base import ContentFile
        from wagtail.images import get_image_model
        WagtailImage = get_image_model()

        response = requests.get(src, timeout=DOWNLOAD_TIMEOUT, stream=True)
        response.raise_for_status()

        if int(response.headers.get('Content-Length', 0)) > MAX_IMAGE_BYTES:
            logger.warning('richtext_import: image too large, skipping %s', src)
            return None
        raw = response.content
        if len(raw) > MAX_IMAGE_BYTES:
            logger.warning('richtext_import: downloaded image too large, skipping %s', src)
            return None

        # Content-based dedup (Wagtail stores a SHA-1 hex digest in file_hash)
        sha1 = hashlib.sha1(raw).hexdigest()
        if sha1 in cache:
            cache[src] = cache[sha1]
            return cache[sha1]
        try:
            existing = WagtailImage.objects.filter(file_hash=sha1).first()
        except Exception:
            existing = None
        if existing:
            cache[src] = cache[sha1] = existing.pk
            return existing.pk

        filename = _filename_from_url(src)
        if not os.path.splitext(filename)[1]:
            ext_map = {'image/jpeg': '.jpg', 'image/png': '.png', 'image/gif': '.gif',
                       'image/webp': '.webp', 'image/svg+xml': '.svg', 'image/avif': '.avif'}
            ctype = response.headers.get('Content-Type', '').split(';')[0].strip()
            filename += ext_map.get(ctype, '.jpg')

        title = (alt or '').strip() or _filename_from_url(src)
        image = WagtailImage(title=title[:255])
        image.file.save(filename, ContentFile(raw), save=True)
        logger.info('richtext_import: imported image "%s" (pk=%s)', title, image.pk)
        cache[src] = cache[sha1] = image.pk
        return image.pk

    except Exception as exc:
        logger.warning('richtext_import: failed to import image %s — %s', src, exc)
        return None


def _image_block_from_url(src, alt='', caption='', base_url=None, cache=None):
    """ImageBlock if the download succeeds, else a raw_html <img> fallback."""
    pk = _import_image_from_url(src, alt=alt, base_url=base_url, cache=cache)
    if pk:
        return _block('image', {
            'image':     pk,
            'caption':   f'<p>{escape(caption)}</p>' if caption else '',
            'alignment': 'full',
            'size':      'medium',
        })
    alt_attr = f' alt="{escape(alt, quote=True)}"' if alt else ''
    src_attr = escape(src, quote=True)
    return _block('raw_html', f'<img src="{src_attr}"{alt_attr}>')


# ── Media / embed helpers ──────────────────────────────────────────────────

def _embed_block_for_url(url, caption=''):
    if not url:
        return None
    ext = _ext(url)
    if ext in VIDEO_EXTENSIONS or YOUTUBE_RE.search(url) or VIMEO_RE.search(url):
        return _block('video', {'video_file': None, 'video_embed_url': url, 'caption': caption})
    if ext in AUDIO_EXTENSIONS or SOUNDCLOUD_RE.search(url) or SPOTIFY_RE.search(url):
        return _block('audio', {'audio_file': None, 'audio_embed_url': url, 'caption': caption})
    return _block('embed', url)


def _media_block(el, base_url=None):
    name = el.name.lower()
    if name == 'iframe':
        return _embed_block_for_url((el.get('src') or '').strip())
    src = (el.get('src') or '').strip()
    if not src:
        source = el.find('source')
        src = (source.get('src') if source else '' or '').strip()
    if not src:
        return None
    key = 'video' if name == 'video' else 'audio'
    return _block(key, {f'{key}_file': None, f'{key}_embed_url': src, 'caption': ''})


# ── Structured element handlers ────────────────────────────────────────────

def _heading_block(el, base_url=None):
    level = el.name.lower()
    if level in ('h1',):
        level = 'h2'
    elif level in ('h5', 'h6'):
        level = 'h4'
    inner = _clean_inline(el.decode_contents(), base_url=base_url)
    if not _has_text(inner):
        return None
    return _block('heading', f'<{level}>{inner}</{level}>')


def _render_list(el, base_url=None, depth=0):
    tag = el.name.lower()
    if tag not in ('ul', 'ol') or depth > 3:
        return ''
    items = []
    for li in el.find_all('li', recursive=False):
        nested = ''
        for child in li.find_all(['ul', 'ol'], recursive=False):
            nested += _render_list(child, base_url=base_url, depth=depth + 1)
            child.extract()
        inner = _clean_inline(li.decode_contents(), base_url=base_url)
        if inner or nested:
            items.append(f'<li>{inner}{nested}</li>')
    if not items:
        return ''
    return f'<{tag}>{"".join(items)}</{tag}>'


def _list_block(el, base_url=None):
    html = _render_list(el, base_url=base_url)
    return _block('paragraph', html) if html else None


def _blockquote_block(el, base_url=None):
    cite = el.find(['cite', 'footer'])
    attribution = ''
    if cite:
        attribution = cite.get_text(strip=True).lstrip('—–- ')
        cite.extract()
    paras = _inline_to_paragraph_htmls(el.decode_contents(), base_url=base_url)
    if not paras:
        return None
    return _block('blockquote', {'text': ''.join(paras), 'attribute_name': attribution})


def _sanitise_table(el):
    """Strip attributes but keep table structure; return a raw_html block."""
    keep = {'table', 'thead', 'tbody', 'tfoot', 'tr', 'th', 'td', 'caption'}
    soup = BeautifulSoup(str(el), 'html.parser')
    _promote_styles(soup)
    for tag in soup.find_all(True):
        if tag.name.lower() in keep:
            tag.attrs = {}
        elif tag.name.lower() in INLINE_KEEP:
            if tag.name.lower() != 'a':
                tag.attrs = {}
        else:
            tag.unwrap()
    table = soup.find('table')
    return _block('raw_html', str(table)) if table else None


def _table_block(el):
    """
    Build a native Wagtail TableBlock from a <table>. Returns a raw_html block
    instead when the table has merged cells (colspan/rowspan), which the contrib
    TableBlock cannot represent. Cell contents are stored as plain text — that is
    what the table editor round-trips.
    """
    rows_el = el.find_all('tr')
    if not rows_el:
        return None

    # Merged cells → preserve exactly as raw_html rather than mangle them.
    for cell in el.find_all(['td', 'th']):
        try:
            if int(cell.get('colspan', 1)) > 1 or int(cell.get('rowspan', 1)) > 1:
                return _sanitise_table(el)
        except (TypeError, ValueError):
            pass

    data = []
    for tr in rows_el:
        cells = tr.find_all(['td', 'th'], recursive=False) or tr.find_all(['td', 'th'])
        if not cells:
            continue
        data.append([_WS.sub(' ', c.get_text(' ', strip=True)).strip() for c in cells])

    if not data:
        return None

    # Normalise ragged rows so the grid is rectangular.
    width = max(len(r) for r in data)
    for r in data:
        r.extend([''] * (width - len(r)))

    # Header detection: an explicit <thead> single row, or a first row of all <th>.
    first_tr = rows_el[0]
    first_cells = first_tr.find_all(['td', 'th'], recursive=False) or first_tr.find_all(['td', 'th'])
    first_row_is_header = bool(first_cells) and all(c.name == 'th' for c in first_cells)
    if el.find('thead') and el.find('thead').find('tr') is first_tr:
        first_row_is_header = True

    # First-column header: every row starts with a <th>.
    first_col_is_header = all(
        (tr.find(['td', 'th']) is not None and tr.find(['td', 'th']).name == 'th')
        for tr in rows_el
    )

    caption_el = el.find('caption')
    caption = caption_el.get_text(' ', strip=True) if caption_el else ''

    if first_row_is_header and first_col_is_header:
        choice = 'both'
    elif first_row_is_header:
        choice = 'row'
    elif first_col_is_header:
        choice = 'column'
    else:
        choice = 'neither'

    value = {
        'data': data,
        'cell': [],
        'mergeCells': [],
        'first_row_is_table_header': first_row_is_header,
        'first_col_is_header': first_col_is_header,
        'table_header_choice': choice,
        'table_caption': caption,
    }
    return _block('table', value)


def _clean_pre(el):
    code = el.get_text()
    return f'<pre><code>{escape(code)}</code></pre>'


def _handle_paragraph(el, base_url=None, cache=None):
    """
    A <p> may contain a bare <img> (Google Docs does this). Split the paragraph
    around images: text before/after an image becomes its own paragraph block.
    """
    if not el.find('img'):
        return [_block('paragraph', p) for p in
                _inline_to_paragraph_htmls(el.decode_contents(), base_url=base_url)]

    result = []
    pending = ''

    def flush():
        nonlocal pending
        for p in _inline_to_paragraph_htmls(pending, base_url=base_url):
            result.append(_block('paragraph', p))
        pending = ''

    for child in el.children:
        if isinstance(child, Tag) and child.name.lower() == 'img':
            flush()
            result.append(_image_block_from_url(
                (child.get('src') or '').strip(),
                alt=(child.get('alt') or '').strip(),
                base_url=base_url, cache=cache))
        else:
            pending += str(child)
    flush()
    return result


def _handle_figure(el, base_url=None, cache=None):
    figcaption = el.find('figcaption')
    caption = figcaption.get_text(strip=True) if figcaption else ''
    if figcaption:
        figcaption.extract()

    img = el.find('img')
    if img:
        return _image_block_from_url((img.get('src') or '').strip(),
                                     alt=(img.get('alt') or '').strip(),
                                     caption=caption, base_url=base_url, cache=cache)
    for media in ('iframe', 'video', 'audio'):
        node = el.find(media)
        if node:
            block = _media_block(node, base_url=base_url) if media != 'iframe' \
                else _embed_block_for_url((node.get('src') or '').strip(), caption=caption)
            if block and isinstance(block['value'], dict) and 'caption' in block['value']:
                block['value']['caption'] = caption
            return block

    inner = el.decode_contents().strip()
    return _block('raw_html', f'<figure>{inner}</figure>') if inner else None


# ── Tree walker ─────────────────────────────────────────────────────────────

def _walk(node, out, base_url, cache):
    pending = ''

    def flush():
        nonlocal pending
        for p in _inline_to_paragraph_htmls(pending, base_url=base_url):
            out.append(_block('paragraph', p))
        pending = ''

    for child in node.children:
        if isinstance(child, NavigableString):
            if not isinstance(child, Comment):
                pending += str(child)
            continue
        if not isinstance(child, Tag):
            continue

        name = child.name.lower()

        if name in HEADING_TAGS:
            flush()
            b = _heading_block(child, base_url=base_url)
            if b:
                out.append(b)
        elif name == 'p':
            flush()
            out.extend(_handle_paragraph(child, base_url=base_url, cache=cache))
        elif name in ('ul', 'ol'):
            flush()
            b = _list_block(child, base_url=base_url)
            if b:
                out.append(b)
        elif name == 'blockquote':
            flush()
            b = _blockquote_block(child, base_url=base_url)
            if b:
                out.append(b)
        elif name == 'figure':
            flush()
            b = _handle_figure(child, base_url=base_url, cache=cache)
            if b:
                out.append(b)
        elif name == 'img':
            flush()
            out.append(_image_block_from_url((child.get('src') or '').strip(),
                                             alt=(child.get('alt') or '').strip(),
                                             base_url=base_url, cache=cache))
        elif name == 'table':
            flush()
            b = _table_block(child) if TABLE_AS_NATIVE_BLOCK else _sanitise_table(child)
            if b:
                out.append(b)
        elif name == 'hr':
            flush()
            out.append(_block(HR_BLOCK_TYPE, '<hr>') if HR_BLOCK_TYPE == 'raw_html'
                       else _block(HR_BLOCK_TYPE, None))
        elif name in ('pre',):
            flush()
            out.append(_block('raw_html', _clean_pre(child)))
        elif name in ('video', 'audio', 'iframe'):
            flush()
            b = _media_block(child, base_url=base_url)
            if b:
                out.append(b)
        elif name in WRAPPER_TAGS:
            # Only descend if it actually contains block-level content; otherwise
            # treat it as an inline run so we don't lose plain wrapper text.
            if child.find(lambda t: isinstance(t, Tag) and t.name.lower() in
                          (HEADING_TAGS | {'p', 'ul', 'ol', 'blockquote', 'figure',
                                           'img', 'table', 'hr', 'pre', 'video',
                                           'audio', 'iframe', 'div', 'section',
                                           'article'})):
                flush()
                _walk(child, out, base_url, cache)
            else:
                pending += str(child)
        else:
            pending += str(child)

    flush()


# ── Main entry point ────────────────────────────────────────────────────────

def _block_plain_len(block):
    """Length of a paragraph block's visible text, or None if not a plain para."""
    if block.get('type') != 'paragraph':
        return None
    value = block.get('value')
    if not isinstance(value, str):
        return None
    # Skip anything structural — a heading is a single run of inline text.
    if re.search(r'<(ul|ol|li|img|br|table)\b', value, re.I):
        return None
    text = _WS.sub(' ', re.sub(r'<[^>]+>', '', value)).strip()
    return len(text)


def _detect_headings(blocks):
    """
    Promote short standalone paragraphs to headings when they sit between two
    long body paragraphs. Only runs when HEADING_DETECTION is on. Conservative
    by design: a candidate needs a long body paragraph on *both* sides, so
    bylines (preceded by the title) and trailing contact lines (no following
    body) are never touched.
    """
    if not HEADING_DETECTION or len(blocks) < 3:
        return blocks

    lengths = [_block_plain_len(b) for b in blocks]
    for i in range(1, len(blocks) - 1):
        here, prev, nxt = lengths[i], lengths[i - 1], lengths[i + 1]
        if here is None or not (0 < here <= HEADING_MAX_LEN):
            continue
        if (prev is not None and prev >= HEADING_BODY_MIN_LEN and
                nxt is not None and nxt >= HEADING_BODY_MIN_LEN):
            inner = re.sub(r'^<p>|</p>$', '', blocks[i]['value'].strip(), flags=re.I)
            blocks[i] = _block('heading', f'<{HEADING_LEVEL}>{inner}</{HEADING_LEVEL}>')
    return blocks


def html_to_stream_blocks(raw_text, base_url=None):
    """Convert pasted HTML or plain text to a list of StreamField block dicts."""
    if not raw_text or not raw_text.strip():
        return []

    if not re.search(r'<[a-zA-Z!/]', raw_text):
        return _plain_text_to_blocks(raw_text, base_url=base_url)

    if not BS4_AVAILABLE:
        return [_block('raw_html', raw_text)]

    match = _FRAGMENT_RE.search(raw_text)
    if match:
        raw_text = match.group(1)

    soup = BeautifulSoup(raw_text, 'html.parser')

    for c in soup.find_all(string=lambda t: isinstance(t, Comment)):
        c.extract()
    for tag in soup.find_all(['script', 'style', 'head', 'meta', 'link', 'title']):
        tag.decompose()
    for tag in soup.find_all(lambda t: t.name and ':' in t.name):   # o:p, w:*, v:* …
        tag.unwrap()
    for tag in soup.find_all(True):
        for attr in list(tag.attrs):
            if attr.lower().startswith('on'):
                del tag[attr]
    _promote_styles(soup)

    root = soup.find('body') or soup
    out = []
    _walk(root, out, base_url, {})
    return _detect_headings([b for b in out if b is not None])


# ── Plain-text fallback ─────────────────────────────────────────────────────

_MD_HEADING = re.compile(r'^\s*(#{1,6})\s+(.*\S)\s*$')
_HR_LINE    = re.compile(r'^\s*([-*_])\1{2,}\s*$')
_UL_ITEM    = re.compile(r'^\s*[-*•]\s+(.*\S)\s*$')
_OL_ITEM    = re.compile(r'^\s*\d+[.)]\s+(.*\S)\s*$')
_QUOTE      = re.compile(r'^\s*>\s?(.*)$')
_URL_LINE   = re.compile(r'^\s*(https?://\S+)\s*$', re.I)


def _plain_text_to_blocks(text, base_url=None):
    """
    Real plain text (no HTML in the clipboard). Each non-blank line becomes its
    own paragraph — this is what fixes 'everything lands in one block', because
    pasted prose from Word / Docs uses one line per paragraph, not blank-line
    separation. Also recognises Markdown-ish headings, lists, quotes, rules and
    bare media URLs.
    """
    out = []
    lines = text.replace('\r\n', '\n').replace('\r', '\n').split('\n')
    ul, ol = [], []

    def flush_lists():
        if ul:
            out.append(_block('paragraph', '<ul>' + ''.join(f'<li>{escape(x)}</li>' for x in ul) + '</ul>'))
            ul.clear()
        if ol:
            out.append(_block('paragraph', '<ol>' + ''.join(f'<li>{escape(x)}</li>' for x in ol) + '</ol>'))
            ol.clear()

    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        if not line.strip():
            flush_lists()
            i += 1
            continue

        m = _UL_ITEM.match(line)
        if m:
            if ol:
                flush_lists()
            ul.append(m.group(1).strip())
            i += 1
            continue
        m = _OL_ITEM.match(line)
        if m:
            if ul:
                flush_lists()
            ol.append(m.group(1).strip())
            i += 1
            continue

        flush_lists()

        m = _MD_HEADING.match(line)
        if m:
            level = min(max(len(m.group(1)), 2), 4)
            out.append(_block('heading', f'<h{level}>{escape(m.group(2).strip())}</h{level}>'))
            i += 1
            continue

        if _HR_LINE.match(line):
            out.append(_block(HR_BLOCK_TYPE, '<hr>') if HR_BLOCK_TYPE == 'raw_html'
                       else _block(HR_BLOCK_TYPE, None))
            i += 1
            continue

        m = _QUOTE.match(line)
        if m:
            quote = [m.group(1)]
            while i + 1 < n and _QUOTE.match(lines[i + 1]):
                i += 1
                quote.append(_QUOTE.match(lines[i]).group(1))
            body = ' '.join(s.strip() for s in quote if s.strip())
            if body:
                out.append(_block('blockquote', {'text': f'<p>{escape(body)}</p>', 'attribute_name': ''}))
            i += 1
            continue

        m = _URL_LINE.match(line)
        if m:
            url = m.group(1)
            if _ext(url) in IMAGE_EXTENSIONS:
                out.append(_image_block_from_url(url, base_url=base_url, cache={}))
            else:
                out.append(_embed_block_for_url(url) or _block('paragraph', f'<p>{escape(url)}</p>'))
            i += 1
            continue

        out.append(_block('paragraph', f'<p>{escape(line.strip())}</p>'))
        i += 1

    flush_lists()
    return _detect_headings(out)
