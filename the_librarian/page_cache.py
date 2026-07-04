"""
the_librarian/page_cache.py
─────────────────────────────────────────────────────────────────────────
Server-side PDF page rendering + on-disk cache.

Renders individual PDF pages to WebP images ONCE per source file, then
serves the cached image on every subsequent request. This replaces
asking every visitor's browser to rasterise the whole PDF itself via
pdf.js + <canvas> (the previous approach — see flipbook_viewer.js) with
rendering each page a single time, on the server, and reusing it for
every reader from then on.

Deliberately doesn't know anything about ArchiveDocument / MagazineIssue
— it operates purely on a filesystem path to a PDF, so the same module
covers all three viewer flows (search-result documents, magazine issues,
raw filesystem archive PDFs) without a model-specific integration. Your
views resolve "which PDF" (from a pk, slug, or filesystem path — however
they already do it to produce pdf_url today) and just pass the resulting
path in here.

Requires:
    pip install pymupdf pillow

Cache layout:
    MEDIA_ROOT/page_cache/<source-key>/meta.json
    MEDIA_ROOT/page_cache/<source-key>/0001.webp
    MEDIA_ROOT/page_cache/<source-key>/0002.webp
    ...

<source-key> is derived from the PDF's path + size + mtime — not its
full content, since hashing every byte of a 60-page PDF on every request
would itself be slow — so replacing a PDF at the same path naturally
invalidates its stale cached pages without needing an explicit "version"
field anywhere.

A note on WebP encode speed (measured, not guessed — see chat): pixel
rasterisation itself is fast (roughly 5-15ms/page), but WebP encoding at
method=2 costs ~100-130ms/page once the process has warmed up (libwebp
pays a one-off ~500ms init cost on a process's very first encode — a
non-issue for a long-running server process, but worth knowing if you
ever benchmark a single cold call and the number looks alarming).
method=2 is a deliberate choice: it's roughly 2x faster than Pillow's
default method=4 for a <2% larger file, which is a better trade for a
render-once-serve-forever cache. If you'd rather trade some file size
for materially faster renders (e.g. for eager bulk pre-rendering, see
render_all_pages() below), JPEG_FALLBACK below shows the swap — JPEG
encodes in ~15-70ms/page in the same test, at roughly 2-4x the file size
for comparable visual quality on text-heavy pages.
"""
import hashlib
import json
import os
import threading
from pathlib import Path

import fitz  # PyMuPDF
from django.conf import settings

# Target render width in device pixels. This should stay in step with
# flipbook_viewer.js's RENDER_WIDTH constant — both exist to solve the
# same problem (pages need to be sharp on a 2x/Retina screen up to
# DESIRED_MAX_PAGE_WIDTH CSS pixels wide), just on different sides of
# the wire now. If you change DESIRED_MAX_PAGE_WIDTH client-side, bump
# this to match: DESIRED_MAX_PAGE_WIDTH * 2 (DPR) * ~1.4 (zoom headroom).
RENDER_WIDTH_PX = 1800

WEBP_QUALITY = 85
WEBP_METHOD = 2  # see module docstring for why not the Pillow default (4)

# Set to True to render JPEG instead of WebP (simpler, faster to encode,
# larger files) — a reasonable choice if you're doing eager bulk
# pre-rendering and total wall-clock time across a whole back-catalogue
# matters more than bytes served to readers.
JPEG_FALLBACK = False
JPEG_QUALITY = 90

CACHE_ROOT = Path(settings.MEDIA_ROOT) / 'page_cache'

# One lock per (source, page), so two requests racing to render the same
# never-before-seen page don't both do the work. This is in-process
# memory, so it only protects against a stampede *within* one worker —
# fine for a single machine; if you run several gunicorn/uwsgi workers,
# a duplicate render is still possible in the rare case two workers get
# a simultaneous first request for the same page. The atomic
# write-then-rename in _render_page() means that's a wasted render, not
# a corrupted file, so it's a performance edge case, not a correctness
# one. Upgrade to a file lock (e.g. via `fcntl`) if it ever matters.
_locks = {}
_locks_guard = threading.Lock()


def _lock_for(key):
    with _locks_guard:
        lock = _locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _locks[key] = lock
        return lock


def source_key(pdf_path):
    """Stable cache key for a PDF, derived from its path + size + mtime.

    Deliberately cheap (stat, not a content hash) since this runs on
    every request. Re-uploading a PDF to the same path changes its
    mtime, which naturally invalidates the old cache entry.
    """
    st = os.stat(pdf_path)
    raw = '%s:%d:%d' % (pdf_path, st.st_size, int(st.st_mtime))
    return hashlib.sha1(raw.encode('utf-8')).hexdigest()[:20]


def _cache_dir(key):
    d = CACHE_ROOT / key
    d.mkdir(parents=True, exist_ok=True)
    return d


def _meta_path(key):
    return _cache_dir(key) / 'meta.json'


def _page_path(key, page_number):
    ext = 'jpg' if JPEG_FALLBACK else 'webp'
    return _cache_dir(key) / ('%04d.%s' % (page_number, ext))


def get_page_count(pdf_path):
    """Page count for pdf_path, cached in meta.json so repeat calls don't
    have to reopen the PDF at all.
    """
    key = source_key(pdf_path)
    meta_path = _meta_path(key)
    if meta_path.exists():
        try:
            return json.loads(meta_path.read_text())['page_count']
        except (ValueError, KeyError, OSError):
            pass  # corrupt/partial meta file — fall through and regenerate

    doc = fitz.open(pdf_path)
    try:
        count = doc.page_count
    finally:
        doc.close()

    meta_path.write_text(json.dumps({'page_count': count}))
    return count


def get_page_image_path(pdf_path, page_number):
    """Absolute filesystem path to page_number's rendered image
    (1-indexed), rendering + caching it first if it isn't already there.
    """
    key = source_key(pdf_path)
    out_path = _page_path(key, page_number)
    if out_path.exists():
        return out_path

    with _lock_for('%s:%d' % (key, page_number)):
        # Re-check after acquiring the lock — another thread may have
        # rendered it while we were waiting.
        if out_path.exists():
            return out_path
        _render_page(pdf_path, page_number, out_path)
        return out_path


def render_all_pages(pdf_path):
    """Eagerly render + cache every page of pdf_path. Intended for a
    management command or a post-upload hook, not a request path — for
    a 60-page issue this takes on the order of several seconds to tens
    of seconds (WebP) depending on page complexity, which is fine in the
    background but too slow to run inline in an HTTP request/response.
    """
    count = get_page_count(pdf_path)
    for page_number in range(1, count + 1):
        get_page_image_path(pdf_path, page_number)
    return count


def _render_page(pdf_path, page_number, out_path):
    doc = fitz.open(pdf_path)
    try:
        page = doc.load_page(page_number - 1)  # fitz pages are 0-indexed
        zoom = RENDER_WIDTH_PX / page.rect.width
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))

        # Render to a temp file and rename atomically, so a concurrent
        # request reading out_path never sees a partially-written file.
        tmp_path = out_path.with_suffix(out_path.suffix + '.tmp')
        if JPEG_FALLBACK:
            pix.pil_save(str(tmp_path), format='JPEG', quality=JPEG_QUALITY)
        else:
            pix.pil_save(str(tmp_path), format='WEBP', quality=WEBP_QUALITY, method=WEBP_METHOD)
        os.replace(tmp_path, out_path)
    finally:
        doc.close()
