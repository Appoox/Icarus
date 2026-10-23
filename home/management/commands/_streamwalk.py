"""
One walk over StreamField values, shared by export_content and import_content.

The exporter walks a value to *collect* what it references (images,
documents, pages, snippets); the importer walks it to *remap* those ids onto
the destination database. They used to be two separate walks that drifted
apart: the exporter collected `image_override` images that the importer never
remapped, and a merge that broke one of them went unnoticed.

Both now call map_stream() with a RefMapper. A mapper method receives a
reference and returns the value to store in its place; the exporter's
mapper records the reference and returns it unchanged.

Rich text inside a stream (RichTextBlock, or any string) goes to
RefMapper.richtext().
"""

#: StructBlock children that hold a bare image pk, recognised by name when a
#: value can't be walked by its block definition (a block removed from the
#: model since the content was written).
IMAGE_FIELD_NAMES = frozenset({'image', 'image_override'})
DOCUMENT_FIELD_NAMES = frozenset({'audio_file', 'video_file'})


class RefMapper:
    """Identity mapper: override the reference kinds you care about."""

    def image(self, pk):
        return pk

    def document(self, pk):
        return pk

    def page(self, pk):
        return pk

    def snippet(self, label, pk):
        return pk

    def richtext(self, html):
        return html


def _as_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def map_stream(stream_block, value, mapper, on_error=None):
    """
    Map every reference in a StreamField value.

    Walks by block definition when one is available; any part of the value
    the definitions don't describe falls back to the name-based walk.
    """
    if stream_block is not None:
        try:
            return map_by_definition(stream_block, value, mapper)
        except Exception as e:  # never lose the value over a walk error
            if on_error:
                on_error(e)
    return map_generic(value, mapper)


def map_by_definition(block, value, mapper):
    from wagtail import blocks as wblocks
    from wagtail.documents.blocks import DocumentChooserBlock
    from wagtail.images.blocks import ImageChooserBlock
    from wagtail.snippets.blocks import SnippetChooserBlock

    if value is None:
        return None

    if isinstance(block, ImageChooserBlock):
        pk = _as_int(value)
        return value if pk is None else mapper.image(pk)
    if isinstance(block, DocumentChooserBlock):
        pk = _as_int(value)
        return value if pk is None else mapper.document(pk)
    if isinstance(block, wblocks.PageChooserBlock):
        pk = _as_int(value)
        return value if pk is None else mapper.page(pk)
    if isinstance(block, SnippetChooserBlock):
        pk = _as_int(value)
        if pk is None:
            return value
        return mapper.snippet(block.target_model._meta.label_lower, pk)
    if isinstance(block, wblocks.RichTextBlock):
        return mapper.richtext(value)
    if isinstance(block, wblocks.StructBlock):
        if not isinstance(value, dict):
            return value
        out = dict(value)
        for name, child in block.child_blocks.items():
            if name in out:
                out[name] = map_by_definition(child, out[name], mapper)
        return out
    if isinstance(block, wblocks.StreamBlock):
        out = []
        for item in value or []:
            if not isinstance(item, dict):
                out.append(item)
                continue
            item = dict(item)
            child = block.child_blocks.get(item.get('type'))
            if child is None:
                # A block type no longer defined: map it by name instead of
                # leaving its ids pointing at the wrong rows.
                out.append(map_generic(item, mapper))
                continue
            item['value'] = map_by_definition(child, item.get('value'), mapper)
            out.append(item)
        return out
    if isinstance(block, wblocks.ListBlock):
        out = []
        for item in value or []:
            # Wagtail >= 2.16 wraps list items as {'type': 'item', 'value': …}
            if isinstance(item, dict) and 'value' in item:
                item = dict(item)
                item['value'] = map_by_definition(block.child_block, item['value'], mapper)
                out.append(item)
            else:
                out.append(map_by_definition(block.child_block, item, mapper))
        return out
    if isinstance(value, str):
        # RawHTMLBlock and friends can still carry rich-text embeds
        return mapper.richtext(value)
    return value


def map_generic(node, mapper):
    """Name-based walk for values whose block definitions are unavailable."""
    if isinstance(node, list):
        return [map_generic(item, mapper) for item in node]
    if isinstance(node, str):
        return mapper.richtext(node)
    if not isinstance(node, dict):
        return node

    out = dict(node)
    btype = out.get('type')
    if btype is not None and 'value' in out:
        val = out['value']
        if btype == 'image' and isinstance(val, dict) and _as_int(val.get('image')) is not None:
            val = dict(val)
            val['image'] = mapper.image(_as_int(val['image']))
            out['value'] = map_generic(
                {k: v for k, v in val.items() if k != 'image'}, mapper
            ) | {'image': val['image']}
        elif btype == 'document' and _as_int(val) is not None:
            out['value'] = mapper.document(_as_int(val))
        else:
            out['value'] = map_generic(val, mapper)
        return out

    # A StructBlock value: named media fields, then everything else.
    for key, val in node.items():
        if key in IMAGE_FIELD_NAMES and _as_int(val) is not None and not isinstance(val, bool):
            out[key] = mapper.image(_as_int(val))
        elif key in DOCUMENT_FIELD_NAMES and _as_int(val) is not None and not isinstance(val, bool):
            out[key] = mapper.document(_as_int(val))
        elif isinstance(val, (dict, list, str)):
            out[key] = map_generic(val, mapper)
    return out
