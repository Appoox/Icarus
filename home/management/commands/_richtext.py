"""
Rich-text media references — shared by export_content and import_content.

Wagtail stores media referenced from inside rich text as tags carrying the
object's primary key, not as model FKs:

    <embed embedtype="image" id="42" format="fullwidth" alt="…"/>
    <a linktype="document" id="7">the PDF</a>

Those PKs are invisible to a field-level scan, so an exporter that only walks
model fields silently drops every image an editor inserted into a paragraph,
and an importer that only remaps FK columns leaves them pointing at whatever
happens to own that PK on the destination database.

Both problems are handled here:

    iter_richtext_refs(html)                 → yields ('image'|'document', pk)
    rewrite_richtext_refs(html, imgs, docs)  → same HTML with PKs remapped

`<embed embedtype="media" …>` is an external oEmbed URL, not a stored file, so
it is deliberately ignored.  `<a linktype="page" …>` is left untouched — page
identity is carried by slug path elsewhere in the archive, not by PK.
"""

import re

# Matches the opening tag of <embed …> and <a …>, capturing the attribute blob.
_TAG_RE = re.compile(r'<(embed|a)\b([^>]*)>', re.IGNORECASE)

# name="value" or name='value'
_ATTR_RE = re.compile(r'([\w:-]+)\s*=\s*(?:"([^"]*)"|\'([^\']*)\')')

_ID_RE = re.compile(r'(\bid\s*=\s*)(["\'])([^"\']*)\2', re.IGNORECASE)


def _attrs(blob: str) -> dict:
    out = {}
    for m in _ATTR_RE.finditer(blob):
        value = m.group(2) if m.group(2) is not None else m.group(3)
        out[m.group(1).lower()] = value
    return out


def _kind_of(tag: str, attrs: dict):
    """Return 'image' / 'document' for a media-bearing tag, else None."""
    tag = tag.lower()
    if tag == 'embed' and attrs.get('embedtype', '').lower() == 'image':
        return 'image'
    if tag == 'a' and attrs.get('linktype', '').lower() == 'document':
        return 'document'
    return None


def iter_richtext_refs(html):
    """Yield ('image'|'document', pk) for every media reference in `html`."""
    if not html or not isinstance(html, str) or '<' not in html:
        return
    for m in _TAG_RE.finditer(html):
        attrs = _attrs(m.group(2))
        kind = _kind_of(m.group(1), attrs)
        if not kind:
            continue
        try:
            yield kind, int(attrs.get('id'))
        except (TypeError, ValueError):
            continue


def rewrite_richtext_refs(html, image_map, doc_map):
    """
    Return `html` with every image/document PK replaced via the remap tables.
    References with no mapping are left untouched.
    """
    if not html or not isinstance(html, str) or '<' not in html:
        return html

    def replace(match):
        tag, blob = match.group(1), match.group(2)
        attrs = _attrs(blob)
        kind = _kind_of(tag, attrs)
        if not kind:
            return match.group(0)
        try:
            old_pk = int(attrs.get('id'))
        except (TypeError, ValueError):
            return match.group(0)
        new_pk = (image_map if kind == 'image' else doc_map).get(old_pk)
        if new_pk is None or new_pk == old_pk:
            return match.group(0)
        new_blob = _ID_RE.sub(
            lambda m: f'{m.group(1)}{m.group(2)}{new_pk}{m.group(2)}',
            blob, count=1,
        )
        return f'<{tag}{new_blob}>'

    return _TAG_RE.sub(replace, html)
