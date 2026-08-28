"""
home/layout_views.py
─────────────────────────────────────────────────────────────────────────
The Homepage Layout canvas.

Three endpoints, mounted under /admin/ by home/wagtail_hooks.py:

    homepage-layout/          the canvas itself
    homepage-layout/save/     JSON in, revision out
    homepage-layout/search/   type-ahead for the chooser fields

── Why a standalone screen rather than a panel in the page editor ───────
The canvas has to know which sections exist in order to draw a card per
section.  Inside the page editor that means reading unsaved StreamField
state out of Wagtail's React/Telepath widget — private internals that
change between releases.  On its own screen there is no StreamField widget
at all: the server knows the blocks, the canvas POSTs one JSON document
back, and Wagtail's own block definitions validate it.  Nothing here
depends on how Wagtail renders its editor.

── Why the inspector is schema-driven ───────────────────────────────────
field_schema() walks a block's child_blocks and emits a description of
each editable field.  The JavaScript renders whatever it is given, so
adding a section type to home/blocks.py makes its settings editable in the
canvas with no JS change.  Field kinds the inspector cannot render safely
(rich text, embeds, raw HTML) are marked 'external' and the inspector
offers a deep-link into the normal page editor for them instead of
pretending to handle them.
"""
import datetime
import decimal
import json

from django.core.exceptions import ValidationError
from django.http import JsonResponse, HttpResponseForbidden
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET, require_POST

from wagtail import blocks
from wagtail.images import get_image_model
from wagtail.images.blocks import ImageChooserBlock
from wagtail.snippets.blocks import SnippetChooserBlock

from articles.wagtail_widgets import ColorPickerBlock

from home import layout as home_layout
from home.blocks import HOMEPAGE_BLOCKS
from home.models import HomePage


# ── Access ──────────────────────────────────────────────────────────────

def _get_page_or_403(request):
    """
    Resolve the homepage and check the user may edit it.

    Deliberately uses Wagtail's own page permissions rather than the
    ad-hoc `is_staff or groups__name__in=[…]` checks used by the audit-log
    and analytics views in this app.  Layout editing is page editing, so it
    should follow the same rules — and be configurable from Wagtail's
    Groups UI like everything else, instead of from a hardcoded list of
    group names.
    """
    page = HomePage.objects.live().first() or HomePage.objects.first()
    if page is None:
        return None, None
    perms = page.permissions_for_user(request.user)
    if not perms.can_edit():
        return None, None
    return page, perms


# ── Block field introspection ───────────────────────────────────────────

# Most specific first — ChoiceBlock and ColorPickerBlock both subclass
# FieldBlock, and CharBlock subclasses FieldBlock too.
_SIMPLE_KINDS = [
    (blocks.ChoiceBlock, 'choice'),
    (ColorPickerBlock, 'colour'),
    (blocks.BooleanBlock, 'boolean'),
    (blocks.IntegerBlock, 'number'),
    (blocks.TextBlock, 'textarea'),
    (blocks.CharBlock, 'text'),
]


def _json_safe(value):
    """
    Coerce a block value into something json_script can render.

    Block values are not always JSON primitives.  A RichTextBlock's default
    is a `RichText` object, an EmbedBlock's is an `EmbedValue`, and a chooser
    hands back a model instance — none of which survive json.dumps, and any
    one of them takes the whole canvas page down with a TypeError before it
    renders.

    The conversions are chosen so the value still round-trips: RichText
    becomes its source HTML and EmbedValue its URL, which are exactly what
    the corresponding to_python() expects back on save.  Coercing these to
    None instead would render fine and then silently erase the editor's
    content the next time they hit Save.
    """
    if value is None or isinstance(value, (str, bool, int, float)):
        return value

    # RichText -> source HTML; EmbedValue -> URL.  Duck-typed rather than
    # imported so this keeps working if either class moves.
    for attr in ('source', 'url'):
        if hasattr(value, attr) and not callable(getattr(value, attr)):
            return _json_safe(getattr(value, attr))

    if hasattr(value, 'pk'):            # image / page / snippet / document
        return value.pk

    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}

    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]

    if isinstance(value, (datetime.date, datetime.datetime, datetime.time)):
        return value.isoformat()

    if isinstance(value, decimal.Decimal):
        return float(value)

    return str(value)


def _chooser_kind(block):
    if isinstance(block, ImageChooserBlock):
        return 'image', 'image'
    if isinstance(block, SnippetChooserBlock):
        model = block.target_model
        return 'snippet', f'{model._meta.app_label}.{model._meta.model_name}'
    if isinstance(block, blocks.PageChooserBlock):
        targets = [
            f'{m._meta.app_label}.{m._meta.model_name}'
            for m in (block.target_models or [])
        ]
        return 'page', ','.join(targets)
    return None, None


def field_schema(block, name='', value=None):
    """
    Describe one block as an inspector field.

    Returns a dict the JavaScript can render blindly.  `value` is the
    block's current StreamField JSON value, so the schema doubles as the
    payload — the canvas never has to fetch settings separately.
    """
    label = str(getattr(block.meta, 'label', '') or name.replace('_', ' ').title())
    help_text = str(getattr(block.meta, 'help_text', '') or '')
    base = {'name': name, 'label': label, 'help': help_text}

    # A blank field starts at the block's own default, not at null.  This is
    # what makes a section added from the palette valid the moment it lands
    # on the grid: 'count' arrives as 6, 'layout' as 'grid', and Save draft
    # succeeds before the editor has touched a single control.
    if value is None and not isinstance(block, (blocks.StructBlock, blocks.ListBlock)):
        try:
            default = block.get_default()
        except Exception:
            default = None
        if default not in (None, ''):
            value = default

    # Applied to every field, not just defaults: the payload is embedded in
    # the page with json_script, so one unserialisable value anywhere is a
    # 500 on the whole canvas rather than a degraded control.
    value = _json_safe(value)

    chooser, target = _chooser_kind(block)
    if chooser:
        return {**base, 'kind': chooser, 'target': target,
                'value': value, 'display': _display_for(chooser, target, value)}

    for cls, kind in _SIMPLE_KINDS:
        if isinstance(block, cls):
            field = {**base, 'kind': kind, 'value': value}
            if kind == 'choice':
                field['choices'] = [
                    {'value': str(v), 'label': str(l)}
                    for v, l in block.field.choices if v != ''
                ]
            if kind == 'number':
                field['min'] = getattr(block.field, 'min_value', None)
                field['max'] = getattr(block.field, 'max_value', None)
            return field

    if isinstance(block, blocks.StructBlock):
        value = value if isinstance(value, dict) else {}
        return {**base, 'kind': 'struct', 'fields': [
            field_schema(child, child_name, value.get(child_name))
            for child_name, child in block.child_blocks.items()
        ]}

    if isinstance(block, blocks.ListBlock):
        items = []
        for item in (value or []):
            # ListBlock JSON is either a bare value (legacy) or
            # {"type": …, "value": …, "id": …} (current).
            item_value = item.get('value') if isinstance(item, dict) and 'value' in item else item
            items.append(field_schema(block.child_block, '', item_value))
        return {**base, 'kind': 'list',
                'child': field_schema(block.child_block, ''),
                'items': items}

    # RichTextBlock, EmbedBlock, RawHTMLBlock, StaticBlock, anything else:
    # editable in the page editor, not here.  Saying so beats rendering a
    # textarea that would silently mangle the value.
    return {**base, 'kind': 'external', 'value': value}


def _display_for(kind, target, value):
    """Human-readable label for a chooser's current value."""
    if not value:
        return None
    try:
        if kind == 'image':
            image = get_image_model().objects.filter(pk=value).first()
            return {'id': value, 'title': str(image.title)} if image else None
        if kind == 'page':
            from wagtail.models import Page
            page = Page.objects.filter(pk=value).first()
            return {'id': value, 'title': str(page.title)} if page else None
        if kind == 'snippet':
            from django.apps import apps
            model = apps.get_model(target)
            obj = model.objects.filter(pk=value).first()
            return {'id': value, 'title': str(obj)} if obj else None
    except Exception:
        return None
    return None


def _palette():
    """The 'Add section' menu, grouped exactly as blocks.py declares."""
    groups = {}
    for name, block in HOMEPAGE_BLOCKS:
        group = str(getattr(block.meta, 'group', '') or _('Other'))
        groups.setdefault(group, []).append({
            'type': name,
            'label': str(getattr(block.meta, 'label', '') or name),
            'icon': str(getattr(block.meta, 'icon', 'placeholder')),
            # A blank schema per type: adding a section from the palette gives
            # the inspector its real field list straight away, with no
            # round-trip and no "save before you can edit it" step.
            'schema': field_schema(block, name, None),
        })
    return [{'group': g, 'blocks': b} for g, b in groups.items()]


def _stream_block():
    return HomePage._meta.get_field('body').stream_block


def _cards(page, layout):
    """One payload entry per section, for the canvas to draw."""
    stream_block = _stream_block()
    # raw_data is the block's JSON as stored — chooser fields are still bare
    # pks there, which is exactly what the inspector round-trips.  Reading
    # child.value instead would hand back hydrated model instances that do
    # not survive a JSON POST.
    raw = {item['id']: item for item in (page.body.raw_data or []) if item.get('id')}
    out = []
    for child in page.body:
        block_def = stream_block.child_blocks.get(child.block_type)
        if block_def is None:
            continue  # a block type removed from blocks.py since this was saved
        raw_value = raw.get(child.id, {}).get('value')
        out.append({
            'id': child.id,
            'type': child.block_type,
            'label': str(getattr(block_def.meta, 'label', '') or child.block_type),
            'icon': str(getattr(block_def.meta, 'icon', 'placeholder')),
            'summary': home_layout.summarise(child),
            'schema': field_schema(block_def, child.block_type, raw_value),
            'placements': {
                bp: layout.get(bp, {}).get(child.id)
                for bp in home_layout.BREAKPOINTS
            },
        })
    return out


# ── Views ───────────────────────────────────────────────────────────────

@require_GET
def homepage_layout(request):
    page, _perms = _get_page_or_403(request)
    if page is None:
        return HttpResponseForbidden(
            _('You do not have permission to edit the homepage layout.')
        )

    layout = home_layout.normalise(page.layout, [c.id for c in page.body])

    return render(request, 'wagtailadmin/homepage_layout.html', {
        'page': page,
        # Handed to the template as a dict: json_script does the encoding
        # and the HTML-escaping that makes it safe to embed.
        'canvas_data': {
            'pageId': page.pk,
            'cards': _cards(page, layout),
            'layout': layout,
            'palette': _palette(),
            'columns': home_layout.GRID_COLUMNS,
            'breakpoints': list(home_layout.BREAKPOINTS),
            'canPublish': page.permissions_for_user(request.user).can_publish(),
            'urls': {
                'save': reverse('icarus_homepage_layout_save'),
                'search': reverse('icarus_homepage_layout_search'),
                'edit': reverse('wagtailadmin_pages:edit', args=[page.pk]),
                'preview': reverse('wagtailadmin_pages:view_draft', args=[page.pk]),
                'history': reverse('wagtailadmin_pages:history', args=[page.pk]),
            },
        },
    })


def _describe_stream_errors(exc, body):
    """
    Turn a StreamBlockValidationError into something an editor can act on.

    Wagtail reports block errors positionally, so map each position back to
    the section's type — "Section 3 (Image banner) needs an image" beats
    "some sections have invalid settings" on a page with twenty of them.
    """
    errors = getattr(exc, 'block_errors', None)
    if not isinstance(errors, dict) or not errors:
        return _('Some sections have invalid settings.')

    parts = []
    for index in sorted(errors, key=lambda k: (isinstance(k, str), k)):
        try:
            block_type = body[int(index)].get('type', '?')
            position = int(index) + 1
        except (TypeError, ValueError, IndexError):
            block_type, position = '?', index
        parts.append(f'{position} ({block_type})')
    return _('These sections need attention: %(sections)s') % {
        'sections': ', '.join(parts)
    }


@require_POST
def homepage_layout_save(request):
    page, perms = _get_page_or_403(request)
    if page is None:
        return JsonResponse({'ok': False, 'error': _('Permission denied.')}, status=403)

    try:
        payload = json.loads(request.body.decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({'ok': False, 'error': _('Malformed request.')}, status=400)

    action = payload.get('action', 'draft')
    if action == 'publish' and not perms.can_publish():
        return JsonResponse(
            {'ok': False, 'error': _('You do not have permission to publish.')},
            status=403,
        )

    body = payload.get('body')
    if not isinstance(body, list):
        return JsonResponse({'ok': False, 'error': _('Malformed sections.')}, status=400)

    # Validate through the StreamField's own block definitions.  This is the
    # whole reason content lives in a StreamField rather than in the layout
    # JSON: the canvas gets Wagtail's validation for free, so a malformed
    # payload is rejected here rather than blowing up at render time.
    stream_block = _stream_block()
    try:
        stream_value = stream_block.to_python(body)
        stream_block.clean(stream_value)
    except ValidationError as exc:
        return JsonResponse(
            {'ok': False, 'error': _describe_stream_errors(exc, body)},
            status=400,
        )
    except Exception as exc:  # malformed block types, bad value shapes
        return JsonResponse({'ok': False, 'error': str(exc)}, status=400)

    page.body = stream_value
    page.layout = home_layout.normalise(
        payload.get('layout'), [item.get('id') for item in body if item.get('id')]
    )

    revision = page.save_revision(user=request.user, log_action=True)
    if action == 'publish':
        revision.publish(user=request.user)

    return JsonResponse({
        'ok': True,
        'published': action == 'publish',
        'revisionId': revision.pk,
        'layout': page.layout,
    })


# Which models each chooser kind may search.  Restricting this here rather
# than trusting the `target` the client sends keeps the endpoint from
# becoming a general-purpose read API over every model in the project.
_SEARCHABLE = {
    'articles.article': ('articles', 'Article'),
    'issue.issue': ('issue', 'Issue'),
    'literati.literati': ('literati', 'Literati'),
    'issue.topic': ('issue', 'Topic'),
    'issue.volume': ('issue', 'Volume'),
}


def _label_for(obj):
    """
    Display label for a chooser result.

    Guards against `title` being a callable — Topic aliases it to .name via a
    method — which would otherwise put a bound method into the JSON payload.
    """
    title = getattr(obj, 'title', None)
    if callable(title):
        try:
            title = title()
        except Exception:
            title = None
    return str(title) if title else str(obj)


@require_GET
def homepage_layout_search(request):
    """Type-ahead behind the inspector's chooser fields."""
    page, _perms = _get_page_or_403(request)
    if page is None:
        return JsonResponse({'results': []}, status=403)

    kind = request.GET.get('kind', '')
    query = (request.GET.get('q') or '').strip()
    results = []

    if kind == 'image':
        qs = get_image_model().objects.all()
        if query:
            qs = qs.filter(title__icontains=query)
        results = [{'id': i.pk, 'title': i.title} for i in qs.order_by('-created_at')[:25]]

    else:
        from django.apps import apps
        for target in kind.split(','):
            spec = _SEARCHABLE.get(target.strip().lower())
            if spec is None:
                continue
            model = apps.get_model(*spec)
            qs = model.objects.all()
            if hasattr(qs, 'live'):
                qs = qs.live()

            # Resolve the searchable field from _meta, not from hasattr():
            # Topic defines title() as a *method* aliasing .name, so an
            # attribute probe reports a field that does not exist and the
            # lookup blows up.
            field_names = {f.name for f in model._meta.get_fields()}
            search_field = next(
                (f for f in ('title', 'name') if f in field_names), None
            )
            if query and search_field:
                qs = qs.filter(**{f'{search_field}__icontains': query})

            # Newest first, mirroring the chooser ordering the project
            # already applies via construct_page_chooser_queryset.
            order = next(
                (c for c in ('-first_published_at', '-date_of_publishing')
                 if c.lstrip('-') in field_names),
                '-pk',
            )
            results += [
                {'id': o.pk, 'title': _label_for(o), 'type': target}
                for o in qs.order_by(order)[:25]
            ]

    return JsonResponse({'results': results[:50]})
