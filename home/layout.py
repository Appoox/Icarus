"""
home/layout.py
─────────────────────────────────────────────────────────────────────────
The placement schema for the homepage grid.

HomePage.body says *what* each section is; HomePage.layout says *where* it
sits.  Keeping placement in its own JSONField — keyed by each StreamField
block's own UUID — rather than inside the blocks themselves buys two
things:

  • The canvas can rearrange the page without rewriting block content, and
    the ordinary Wagtail page editor can edit block content without knowing
    the grid exists.
  • A block that has never been placed (someone added it in the page
    editor) is not an error.  It simply gets appended full-width at the
    bottom by normalise(), so the two editors can never desynchronise.

Shape:

    {
      "version": 1,
      "desktop": { "<block-uuid>": {placement}, … },
      "tablet":  { … },     # empty = inherit desktop, reflowed to 6 columns
      "mobile":  { … }      # empty = single column, in desktop reading order
    }

    placement = {
      "col": 1-12,  "span": 1-12,       # grid-column start / span
      "row": 1+,    "row_span": 1+,     # grid-row start / span
      "visible": bool,
      "style": plain|tint|bordered,
      "pad": none|sm|md|lg
    }

Row heights are a *contract*, not pixels: real section height comes from
content, so row_span drives a min-height in --sg-row units and list-type
sections scroll inside their cell.  See home.css.
"""
from django.utils.html import format_html

LAYOUT_VERSION = 1

BREAKPOINTS = ('desktop', 'tablet', 'mobile')

GRID_COLUMNS = {'desktop': 12, 'tablet': 6, 'mobile': 1}

STYLE_CHOICES = ('plain', 'tint', 'bordered')
PAD_CHOICES = ('none', 'sm', 'md', 'lg')

MAX_ROW = 200
MAX_ROW_SPAN = 24

# CSS custom-property prefixes per breakpoint.  Desktop is always emitted;
# tablet and mobile are emitted only when explicitly placed, so the
# stylesheet's var() fallbacks can express "inherit and reflow".
_VAR_PREFIX = {'desktop': '', 'tablet': 't', 'mobile': 'm'}


def empty_layout():
    return {'version': LAYOUT_VERSION, 'desktop': {}, 'tablet': {}, 'mobile': {}}


def _clamp(value, low, high, default):
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, n))


def clean_placement(raw, breakpoint='desktop'):
    """Coerce one placement dict into something safe to render."""
    raw = raw if isinstance(raw, dict) else {}
    columns = GRID_COLUMNS[breakpoint]

    span = _clamp(raw.get('span'), 1, columns, columns)
    col = _clamp(raw.get('col'), 1, columns, 1)
    # Keep the cell on the grid: a 6-wide block cannot start at column 10.
    col = min(col, columns - span + 1)

    style = raw.get('style')
    pad = raw.get('pad')

    return {
        'col': col,
        'span': span,
        'row': _clamp(raw.get('row'), 1, MAX_ROW, 1),
        'row_span': _clamp(raw.get('row_span'), 1, MAX_ROW_SPAN, 1),
        'visible': bool(raw.get('visible', True)),
        'style': style if style in STYLE_CHOICES else 'plain',
        'pad': pad if pad in PAD_CHOICES else 'md',
    }


def normalise(layout, block_ids):
    """
    Reconcile a stored layout against the blocks that actually exist.

    Drops placements whose block is gone, coerces the rest, and appends any
    block that has no desktop placement to the bottom of the grid at full
    width.  This is the function that makes "add a block in the page editor,
    then open the canvas" work without a repair step.

    `block_ids` must be in StreamField order — it decides the order unplaced
    blocks are appended in.
    """
    layout = layout if isinstance(layout, dict) else {}
    known = list(block_ids)
    known_set = set(known)
    out = empty_layout()

    for breakpoint in BREAKPOINTS:
        stored = layout.get(breakpoint)
        stored = stored if isinstance(stored, dict) else {}
        out[breakpoint] = {
            block_id: clean_placement(placement, breakpoint)
            for block_id, placement in stored.items()
            if block_id in known_set
        }

    desktop = out['desktop']
    if desktop:
        next_row = max(p['row'] + p['row_span'] for p in desktop.values())
    else:
        next_row = 1

    for block_id in known:
        if block_id not in desktop:
            desktop[block_id] = clean_placement(
                {'col': 1, 'span': GRID_COLUMNS['desktop'],
                 'row': next_row, 'row_span': 1},
            )
            next_row += 1

    return out


class Cell:
    """One placed block, ready to render."""

    __slots__ = ('block', 'block_id', 'placement', 'style_attr', 'css_class')

    def __init__(self, block, block_id, placement, style_attr, css_class):
        self.block = block
        self.block_id = block_id
        self.placement = placement
        self.style_attr = style_attr
        self.css_class = css_class


def _style_attr(layout, block_id):
    """
    Build the inline custom-property declarations for one cell.

    Desktop always emits --c/--s/--r/--rs.  Tablet and mobile emit their
    prefixed variants only when that breakpoint was explicitly placed, so
    home.css can say `var(--tc, 1)` and get "inherit, reflowed" for free
    rather than needing a second code path.
    """
    parts = []
    for breakpoint in BREAKPOINTS:
        placement = layout.get(breakpoint, {}).get(block_id)
        if placement is None:
            continue
        p = _VAR_PREFIX[breakpoint]
        parts.append(
            f'--{p}c:{placement["col"]};--{p}s:{placement["span"]};'
            f'--{p}r:{placement["row"]};--{p}rs:{placement["row_span"]}'
        )
    return ';'.join(parts)


def _css_class(child, placement):
    """
    Classes for one grid cell.

    `scrolls` is declared by the block itself (see OtherTopicsBlock and
    MostReadBlock in home/blocks.py) rather than hardcoded here, so a new
    list-shaped section opts in by setting one Meta attribute.
    """
    classes = [
        'sg-cell',
        f'sg-cell--{placement["style"]}',
        f'sg-cell--pad-{placement["pad"]}',
    ]
    if getattr(child.block.meta, 'scrolls', False):
        classes.append('sg-cell--scrolls')
    return ' '.join(classes)


def cells(body, layout):
    """
    Return the page's blocks as Cells, in grid reading order.

    Order is (row, col) rather than StreamField order — that is what makes
    the dedup registry in curation.py behave the way a reader experiences
    the page, so "skip items already shown" means "already shown *above*".
    """
    block_ids = [child.id for child in body]
    layout = normalise(layout, block_ids)
    desktop = layout['desktop']

    placed = []
    for child in body:
        placement = desktop.get(child.id)
        if placement is None or not placement['visible']:
            continue
        placed.append((placement['row'], placement['col'], child, placement))

    placed.sort(key=lambda item: (item[0], item[1]))

    return [
        Cell(
            block=child,
            block_id=child.id,
            placement=placement,
            style_attr=_style_attr(layout, child.id),
            css_class=_css_class(child, placement),
        )
        for _row, _col, child, placement in placed
    ]


def summarise(child):
    """
    A one-line description of a block, for its card on the canvas.

    The card already shows the block's type, so this answers the question the
    type does not: *what will this section actually put on the page?*  It is
    deliberately tolerant — it runs against editor-supplied data that may be
    half-filled in, and a summary is never worth raising over.
    """
    try:
        value = child.value
        if not hasattr(value, 'get'):
            return ''
        bits = []

        heading = value.get('heading') or value.get('label_text')
        if heading:
            bits.append(str(heading))

        # Which issue an issue block is built around
        issue_source = value.get('issue_source')
        if issue_source == 'latest':
            bits.append('current issue')
        elif issue_source == 'pick':
            issue = value.get('issue')
            bits.append(str(issue) if issue else 'no issue chosen')

        filter_by = value.get('filter_by')
        if filter_by:
            bits.append(str(filter_by).replace('_', ' '))

        # Featured-article source
        source = value.get('source')
        if isinstance(source, str):
            bits.append({
                'pick': 'hand-picked',
                'issue_featured': "the issue's featured article",
                'most_read': 'most read this week',
            }.get(source, source.replace('_', ' ')))
        elif hasattr(source, 'get'):
            mode = source.get('source_mode')
            picks = len(source.get('picks') or [])
            if mode == 'manual':
                bits.append(f'{picks} hand-picked')
            elif mode == 'pinned_then_auto':
                bits.append(f'{picks} pinned, then auto')
            else:
                bits.append(str(source.get('auto_from') or 'latest').replace('_', ' '))
            if source.get('count'):
                bits.append(f'{source["count"]} items')

        scope = value.get('scope')
        if scope:
            window = value.get('window')
            label = {'7': 'last 7 days', '30': 'last 30 days',
                     '90': 'last 90 days', '0': 'all time'}.get(str(window), '')
            bits.append(f'{scope}{", " + label if label else ""}')

        for key, label in (('max_items', 'max'), ('count', None)):
            if 'source' not in value and value.get(key):
                bits.append(f'{label + " " if label else ""}{value[key]} items')
                break

        cards = value.get('cards')
        if cards is not None:
            bits.append(f'{len(cards)} cards')

        if value.get('hide_for_subscribers'):
            bits.append('hidden from subscribers')

        return ' · '.join(b for b in bits if b)
    except Exception:
        return ''


def grid_style(layout):
    """Inline style for the grid container — how many rows to reserve."""
    desktop = layout.get('desktop') or {}
    if not desktop:
        return ''
    rows = max(p.get('row', 1) + p.get('row_span', 1) - 1 for p in desktop.values())
    return format_html('--sg-rows:{}', rows)
