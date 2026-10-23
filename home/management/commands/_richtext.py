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
it is deliberately ignored. `<a linktype="page" id="N">` carries a page PK
too; the importer remaps it through the archive's page table.
"""

import re

# Matches the opening tag of <embed …> and <a …>, capturing the attribute blob.
_TAG_RE = re.compile(r'<(embed|a)\b([^>]*)>', re.IGNORECASE)

# name="value" or name='value'. Scanned left to right, each match consumes
# a whole quoted value — so text *inside* a value (alt="… id='5' …") is
# never mistaken for an attribute.
_ATTR_RE = re.compile(r'([\w:-]+)\s*=\s*(?:"([^"]*)"|\'([^\']*)\')')

MEDIA_KINDS = ('image', 'document')

#: What an unmappable reference's id becomes. No row has pk 0, so Wagtail
#: renders the tag as it renders any missing target (a bare <a>, an empty
#: <img>). An empty id="" is not safe: Wagtail passes it to in_bulk(), which
#: raises, and the page then fails to save or render.
UNMAPPED_ID = 0


def _attrs(blob: str) -> dict:
    out = {}
    for m in _ATTR_RE.finditer(blob):
        value = m.group(2) if m.group(2) is not None else m.group(3)
        out[m.group(1).lower()] = value
    return out


def _kind_of(tag: str, attrs: dict):
    """Return 'image' / 'document' / 'page' for a reference-bearing tag, else None."""
    tag = tag.lower()
    if tag == 'embed' and attrs.get('embedtype', '').lower() == 'image':
        return 'image'
    if tag == 'a' and attrs.get('linktype', '').lower() == 'document':
        return 'document'
    if tag == 'a' and attrs.get('linktype', '').lower() == 'page':
        return 'page'
    return None


def _set_id(blob: str, new_value) -> str:
    """Replace the value of the `id` attribute itself, and nothing else."""
    for m in _ATTR_RE.finditer(blob):
        if m.group(1).lower() == 'id':
            quote = '"' if m.group(2) is not None else "'"
            start, end = m.span()
            return f'{blob[:start]}{m.group(1)}={quote}{new_value}{quote}{blob[end:]}'
    return blob


def _lookup(mapping, pk):
    if mapping is None:
        return pk  # no table for this kind: leave it alone
    return mapping(pk) if callable(mapping) else mapping.get(pk)


def iter_richtext_refs(html, kinds=MEDIA_KINDS):
    """Yield (kind, pk) for every reference of the given kinds in `html`."""
    if not html or not isinstance(html, str) or '<' not in html:
        return
    for m in _TAG_RE.finditer(html):
        attrs = _attrs(m.group(2))
        kind = _kind_of(m.group(1), attrs)
        if kind not in kinds:
            continue
        try:
            yield kind, int(attrs.get('id'))
        except (TypeError, ValueError):
            continue


def rewrite_richtext_refs(html, image_map, doc_map, page_map=None, drop_unmapped=False):
    """
    Return `html` with every reference's PK replaced via the remap tables
    (dicts, or callables taking the old pk). A table of None leaves that kind
    untouched.

    drop_unmapped: a reference with no mapping gets UNMAPPED_ID instead of
    keeping the source PK — on another database that PK belongs to some
    unrelated row.
    """
    if not html or not isinstance(html, str) or '<' not in html:
        return html
    tables = {'image': image_map, 'document': doc_map, 'page': page_map}

    def replace(match):
        tag, blob = match.group(1), match.group(2)
        attrs = _attrs(blob)
        kind = _kind_of(tag, attrs)
        if not kind or tables[kind] is None:
            return match.group(0)
        try:
            old_pk = int(attrs.get('id'))
        except (TypeError, ValueError):
            return match.group(0)
        new_pk = _lookup(tables[kind], old_pk)
        if new_pk is None:
            if not drop_unmapped:
                return match.group(0)
            new_pk = UNMAPPED_ID
        if new_pk == old_pk:
            return match.group(0)
        return f'<{tag}{_set_id(blob, new_pk)}>'

    return _TAG_RE.sub(replace, html)
