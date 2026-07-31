"""
export_content  —  Icarus content export command
=================================================
Serialises pages, snippets, images, and documents to a portable ZIP archive
that can be imported on a fresh database with `import_content`.

Usage
-----
    # Everything — all pages + snippets + media  [default]
    python manage.py export_content

    # Custom output path (a directory gets an auto-timestamped filename)
    python manage.py export_content --output /backups/prod_20260701.zip
    python manage.py export_content --output /backups/

    # Scope options
    python manage.py export_content --scope full         # every page + snippets + media  [default]
    python manage.py export_content --scope structural   # HomePage / index pages only
    python manage.py export_content --scope snippets     # snippets + media only

    # Skip embedding media files (manifest only — import will skip missing files)
    python manage.py export_content --no-media

    # Output control
    python manage.py export_content -v 2        # one line per record
    python manage.py export_content --no-progress
    python manage.py export_content -v 0        # silent (errors only)

ZIP contents
------------
    manifest.json               – all serialised data (pages, snippets, metadata)
    media/images/<pk>/<file>    – original image files referenced by content
    media/documents/<pk>/<file> – document files (PDFs, MP3s, etc.)

Media lives under a per-PK directory so two files that happen to share a
basename cannot collide inside the archive.

Ref format
----------
FK pointers that must survive PK remapping on import are stored as dicts:
    Image    → {"__ref": "image",    "pk": <original pk>}
    Document → {"__ref": "document", "pk": <original pk>}
    Page     → {"__ref": "page",     "slug_path": "home/issues"}

StreamField JSON is stored parsed (list), not as a string.
"""

import json
import os
import time
import zipfile
from datetime import datetime, date
from pathlib import Path

import wagtail
from django.core.management.base import BaseCommand
from django.db import models as dj_models
from wagtail.fields import RichTextField, StreamField
from wagtail.images import get_image_model
from wagtail.documents import get_document_model
from wagtail.models import Page, Site

from ._console import (
    Console, PRECOMPRESSED_SUFFIXES, human_bytes, human_duration,
)
from ._richtext import iter_richtext_refs

# ---------------------------------------------------------------------------
# Page model sets
# ---------------------------------------------------------------------------

STRUCTURAL_PAGE_MODELS = {
    'home.homepage',
    'articles.articleindexpage',
    'issue.issueindexpage',
    'literati.authorindexpage',
}

#: `--scope full` deliberately does NOT use a model allow-list: it exports every
#: page type in the tree, so a page model added later cannot silently fall out
#: of a "full" export.

# Field names that live on wagtailcore_page — skipped when serialising the
# MTI child table so we don't double-export them.
_BASE_PAGE_NAMES: frozenset = frozenset(
    f.name for f in Page._meta.get_fields()
) | {'id', 'page_ptr', 'page_ptr_id'}

_PAGE_BASE_EXCLUDE = {
    'id', 'path', 'depth', 'numchild',
    'content_type', 'content_type_id',
    'locale', 'locale_id',
    'translation_key', 'alias_of', 'alias_of_id',
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _slug_path(page: Page) -> str:
    """
    Return the '/'-joined slug path from just below the treebeard root to
    `page`.  Example: 'home', 'home/issues', 'home/issues/jan-2026'.
    The root node itself (depth=1) is excluded.
    """
    ancestors = list(page.get_ancestors())[1:]   # skip depth-1 root
    return '/'.join([a.slug for a in ancestors] + [page.slug])


def _compression_for(name) -> int:
    """Deflate text-ish payloads; store already-compressed media verbatim."""
    if Path(name).suffix.lower() in PRECOMPRESSED_SUFFIXES:
        return zipfile.ZIP_STORED
    return zipfile.ZIP_DEFLATED


# ---------------------------------------------------------------------------
# Media registry – tracks every image/doc found during traversal
# ---------------------------------------------------------------------------

class _MediaRegistry:
    def __init__(self, console: Console):
        self._console = console
        self.images: dict = {}     # {orig_pk: image_instance}
        self.documents: dict = {}  # {orig_pk: document_instance}
        # Dangling references — media rows the content points at that no longer
        # exist.  Recorded as {pk: [referrer, …]} so a PK referenced by 30 pages
        # is reported once, not thirty times.
        self.missing_images: dict = {}
        self.missing_documents: dict = {}
        #: label of the record currently being walked, used to name referrers
        self.context: str = ''

    def _note(self, pk, cache, missing, get_model, kind):
        if pk is None or pk in cache:
            return
        if pk in missing:
            missing[pk].append(self.context)   # already reported once
            return
        Model = get_model()
        try:
            cache[pk] = Model.objects.get(pk=pk)
        except Model.DoesNotExist:
            missing[pk] = [self.context]
            self._console.warn(
                f'{kind} pk={pk} does not exist in the database — '
                f'referenced by {self.context or "content"}'
            )

    def note_image(self, pk):
        self._note(pk, self.images, self.missing_images,
                   get_image_model, 'Image')

    def note_document(self, pk):
        self._note(pk, self.documents, self.missing_documents,
                   get_document_model, 'Document')


# ---------------------------------------------------------------------------
# Field serialiser
# ---------------------------------------------------------------------------

class _Serialiser:
    """Converts model field values to JSON-safe dicts, noting media refs."""

    def __init__(self, media: _MediaRegistry, console: Console):
        self._media = media
        self._console = console

    # ── Rich text ───────────────────────────────────────────────────────

    def scan_richtext(self, html):
        """Note images/documents referenced from inside rich-text markup."""
        for kind, pk in iter_richtext_refs(html):
            if kind == 'image':
                self._media.note_image(pk)
            else:
                self._media.note_document(pk)

    # ── StreamField scanning ────────────────────────────────────────────

    def _scan_by_definition(self, block, value):
        """
        Walk a StreamField value alongside its *block definitions*.

        Driving off the definitions rather than block names means a media
        reference is found wherever it lives — a renamed block, a new
        StructBlock, an image nested three ListBlocks deep — with no need to
        teach this scanner about each new block type.
        """
        from wagtail import blocks as wblocks
        from wagtail.images.blocks import ImageChooserBlock
        from wagtail.documents.blocks import DocumentChooserBlock

        if value is None:
            return

        if isinstance(block, ImageChooserBlock):
            try:
                self._media.note_image(int(value))
            except (TypeError, ValueError):
                pass
        elif isinstance(block, DocumentChooserBlock):
            try:
                self._media.note_document(int(value))
            except (TypeError, ValueError):
                pass
        elif isinstance(block, wblocks.RichTextBlock):
            self.scan_richtext(value)
        elif isinstance(block, wblocks.StructBlock):
            if isinstance(value, dict):
                for name, child in block.child_blocks.items():
                    if name in value:
                        self._scan_by_definition(child, value[name])
        elif isinstance(block, wblocks.StreamBlock):
            for item in value or []:
                if not isinstance(item, dict):
                    continue
                child = block.child_blocks.get(item.get('type'))
                if child is not None:
                    self._scan_by_definition(child, item.get('value'))
        elif isinstance(block, wblocks.ListBlock):
            for item in value or []:
                # Wagtail >= 2.16 wraps list items as {'type': 'item', 'value': …}
                inner = (
                    item['value']
                    if isinstance(item, dict) and 'value' in item
                    else item
                )
                self._scan_by_definition(block.child_block, inner)
        elif isinstance(value, str):
            # RawHTMLBlock and friends can still carry rich-text embeds
            self.scan_richtext(value)

    def _scan_generic(self, node):
        """
        Name-based fallback scan.

        Runs in addition to the definition walk so that values whose block
        definition cannot be resolved (legacy JSON, a block removed from the
        model since the content was authored) still give up their media.
        """
        if isinstance(node, list):
            for item in node:
                self._scan_generic(item)
        elif isinstance(node, dict):
            btype = node.get('type', '')
            val = node.get('value')
            if btype == 'image' and isinstance(val, dict):
                self._media.note_image(val.get('image'))
            elif btype == 'document' and isinstance(val, (int, str)):
                try:
                    self._media.note_document(int(val))
                except (TypeError, ValueError):
                    pass
            elif btype in ('audio', 'video') and isinstance(val, dict):
                self._media.note_document(val.get('audio_file'))
                self._media.note_document(val.get('video_file'))
            for v in node.values():
                if isinstance(v, (dict, list)):
                    self._scan_generic(v)
                elif isinstance(v, str):
                    self.scan_richtext(v)
        elif isinstance(node, str):
            self.scan_richtext(node)

    def _scan_stream(self, node, stream_block=None):
        """Note every image/document referenced anywhere in StreamField JSON."""
        if stream_block is not None:
            try:
                self._scan_by_definition(stream_block, node)
            except Exception as e:
                self._console.warn(f'block-definition scan failed: {e}')
        self._scan_generic(node)

    # ── Single field ────────────────────────────────────────────────────

    def field_value(self, instance, field):
        """Return a JSON-safe value for one model field."""
        Im = get_image_model()
        Do = get_document_model()

        # ── StreamField ─────────────────────────────────────────────────
        if isinstance(field, StreamField):
            raw = field.value_to_string(instance)
            try:
                parsed = json.loads(raw) if raw else []
            except (json.JSONDecodeError, TypeError):
                parsed = []
            self._scan_stream(parsed, getattr(field, 'stream_block', None))
            return parsed  # store as Python list, not raw string

        # ── RichTextField ───────────────────────────────────────────────
        # Inline <embed embedtype="image" id="N"> references live only in the
        # markup, so they must be harvested explicitly.
        if isinstance(field, RichTextField):
            html = getattr(instance, field.attname, None)
            self.scan_richtext(html)
            return html

        # ── ForeignKey ──────────────────────────────────────────────────
        if isinstance(field, dj_models.ForeignKey):
            raw_id = getattr(instance, field.attname, None)
            if raw_id is None:
                return None
            rm = field.related_model
            if rm and issubclass(rm, Im):
                self._media.note_image(raw_id)
                return {'__ref': 'image', 'pk': raw_id}
            if rm and issubclass(rm, Do):
                self._media.note_document(raw_id)
                return {'__ref': 'document', 'pk': raw_id}
            if rm and issubclass(rm, Page):
                try:
                    pg = Page.objects.get(pk=raw_id)
                    return {'__ref': 'page', 'slug_path': _slug_path(pg)}
                except Page.DoesNotExist:
                    return None
            return raw_id  # plain integer FK (Volume, Topic, etc.)

        # ── ManyToMany ─── handled by serialize() separately ────────────
        if isinstance(field, dj_models.ManyToManyField):
            return '__m2m__'  # sentinel; caller handles below

        # ── Scalar values ───────────────────────────────────────────────
        attr = field.attname if hasattr(field, 'attname') else field.name
        value = getattr(instance, attr, None)
        if isinstance(value, (datetime, date)):
            return value.isoformat()
        return value

    # ── Whole instance ───────────────────────────────────────────────────

    def serialize(self, instance, exclude=None) -> dict:
        """
        Serialize all concrete fields of `instance`.
        M2M values are stored under key '__m2m' in the returned dict.
        """
        Im = get_image_model()
        Do = get_document_model()
        exclude = set(exclude or [])
        data: dict = {}
        m2m: dict = {}

        for field in instance._meta.get_fields():
            name = field.name
            if name in exclude:
                continue
            if isinstance(field, dj_models.AutoField):
                continue

            if isinstance(field, dj_models.ManyToManyField):
                rm = field.related_model
                qs = getattr(instance, name).all()
                if rm and issubclass(rm, Page):
                    m2m[name] = [
                        {'__ref': 'page', 'slug_path': _slug_path(p)}
                        for p in qs
                    ]
                elif rm and issubclass(rm, Im):
                    for img in qs:
                        self._media.note_image(img.pk)
                    m2m[name] = [{'__ref': 'image', 'pk': i.pk} for i in qs]
                elif rm and issubclass(rm, Do):
                    for doc in qs:
                        self._media.note_document(doc.pk)
                    m2m[name] = [{'__ref': 'document', 'pk': d.pk} for d in qs]
                else:
                    m2m[name] = list(qs.values_list('pk', flat=True))
                continue

            if not getattr(field, 'concrete', False):
                # skip reverse relations, GenericRelation, GenericForeignKey and
                # taggit/other virtual managers — they have no DB column and were
                # otherwise stored as str(<manager>) junk (and could raise).
                continue

            try:
                val = self.field_value(instance, field)
                if val != '__m2m__':
                    data[name] = val
            except Exception as e:
                self._console.warn(f'{type(instance).__name__}.{name}: {e}')

        if m2m:
            data['__m2m'] = m2m
        return data


# ---------------------------------------------------------------------------
# High-level exporter
# ---------------------------------------------------------------------------

class ContentExporter:
    #: snippet models counted up-front so the progress bar knows its total
    SNIPPET_MODEL_PATHS = (
        ('issue.models', 'Volume'),
        ('issue.models', 'Topic'),
        ('home.models', 'SiteContactSettings'),
        ('home.models', 'SiteHeader'),
        ('home.models', 'SiteFooter'),
        ('literati.models', 'EditorialBoard'),
        ('literati.models', 'EditorialBoardMembershipHistory'),
    )

    def __init__(self, console: Console):
        self.console = console
        self._media = _MediaRegistry(console)
        self._s = _Serialiser(self._media, console)
        self.bar = None

    # ── Progress plumbing ─────────────────────────────────────────────────

    def _tick(self, suffix=''):
        if self.bar is not None:
            self.bar.advance(suffix=suffix)

    def snippet_total(self) -> int:
        """Total snippet rows across every exported snippet model."""
        from importlib import import_module
        total = 0
        for module_path, model_name in self.SNIPPET_MODEL_PATHS:
            try:
                model = getattr(import_module(module_path), model_name)
                total += model.objects.count()
            except Exception:
                continue
        return total

    # ── Resilience helper ─────────────────────────────────────────────────

    def _serialize_rows(self, queryset, label, exclude=frozenset({'id'})):
        """
        Serialise every object in `queryset`, isolating failures: a single bad
        record is logged and skipped rather than aborting the whole export.
        """
        rows = []
        for obj in queryset:
            try:
                self._media.context = f'{label} pk={getattr(obj, "pk", "?")}'
                rows.append(self._s.serialize(obj, exclude=exclude))
                self.console.detail(f'    {label}: {obj}')
            except Exception as e:
                self.console.warn(
                    f'{label} pk={getattr(obj, "pk", "?")}: {e} — skipped'
                )
            finally:
                self._tick(label)
        return rows

    # ── Snippets ─────────────────────────────────────────────────────────

    def export_volumes(self):
        from issue.models import Volume
        return self._serialize_rows(Volume.objects.all(), 'Volume')

    def export_topics(self):
        from issue.models import Topic
        return self._serialize_rows(Topic.objects.all(), 'Topic')

    def export_site_contact(self):
        from home.models import SiteContactSettings
        return self._serialize_rows(
            SiteContactSettings.objects.all(), 'SiteContactSettings'
        )

    def export_site_header(self):
        from home.models import SiteHeader
        return self._serialize_rows(SiteHeader.objects.all(), 'SiteHeader')

    def export_site_footer(self):
        """Export SiteFooter with its full Section → Column → Link hierarchy."""
        from home.models import SiteFooter
        result = []
        for footer in SiteFooter.objects.all():
            try:
                fd = self._s.serialize(footer, exclude={'id'})
                fd['_sections'] = []
                for section in footer.sections.order_by('sort_order'):
                    sd = self._s.serialize(section, exclude={'id', 'footer', 'footer_id'})
                    sd['_columns'] = []
                    for column in section.columns.order_by('sort_order'):
                        cd = self._s.serialize(column, exclude={'id', 'section', 'section_id'})
                        cd['_links'] = [
                            self._s.serialize(link, exclude={'id', 'column', 'column_id'})
                            for link in column.column_links.order_by('sort_order')
                        ]
                        sd['_columns'].append(cd)
                    fd['_sections'].append(sd)
                result.append(fd)
                self.console.detail(
                    f'    SiteFooter: {len(fd["_sections"])} section(s)'
                )
            except Exception as e:
                self.console.warn(
                    f'SiteFooter pk={getattr(footer, "pk", "?")}: {e} — skipped'
                )
            finally:
                self._tick('SiteFooter')
        return result

    def export_editorial_boards(self):
        """Export each board with its ordered member list."""
        from literati.models import EditorialBoard
        result = []
        for board in EditorialBoard.objects.all():
            try:
                bd = self._s.serialize(board, exclude={'id'})
                bd['_members'] = [
                    self._s.serialize(m, exclude={'id', 'board', 'board_id'})
                    for m in board.members.order_by('sort_order')
                ]
                result.append(bd)
                self.console.detail(
                    f'    EditorialBoard: {board} ({len(bd["_members"])} member(s))'
                )
            except Exception as e:
                self.console.warn(
                    f'EditorialBoard pk={getattr(board, "pk", "?")}: {e} — skipped'
                )
            finally:
                self._tick('EditorialBoard')
        return result

    def export_membership_history(self):
        from literati.models import EditorialBoardMembershipHistory
        result = []
        for h in EditorialBoardMembershipHistory.objects.all():
            try:
                data = self._s.serialize(h, exclude={'id'})
                # Store the board by its natural key (name) rather than the raw PK
                # so the importer can resolve it on a fresh DB, where the original
                # PK no longer applies. Matches import's name-based board lookup.
                data['board'] = h.board.name if h.board_id else None
                result.append(data)
            except Exception as e:
                self.console.warn(
                    f'MembershipHistory pk={getattr(h, "pk", "?")}: {e} — skipped'
                )
            finally:
                self._tick('MembershipHistory')
        return result

    # ── Page tree ─────────────────────────────────────────────────────────

    def export_pages(self, allowed_models=None) -> list:
        """
        Export all pages below the treebeard root in tree order (parents first).
        `allowed_models` is a set of lowercase 'app.modelname' strings;
        None exports everything.
        """
        root = Page.get_first_root_node()
        result = []
        for page in root.get_descendants(inclusive=False).order_by('path'):
            try:
                specific = page.specific
                label = (
                    f'{type(specific)._meta.app_label}.'
                    f'{type(specific)._meta.model_name}'
                )
                if allowed_models is not None and label not in allowed_models:
                    continue

                self._media.context = f'{label} [{page.slug}]'
                parent = page.get_parent()
                parent_slug_path = (
                    _slug_path(parent)
                    if parent and not parent.is_root()
                    else None
                )

                base_fields = self._s.serialize(page, exclude=_PAGE_BASE_EXCLUDE)

                # MTI-specific fields only
                if type(specific) is Page:
                    specific_fields = {}
                else:
                    specific_fields = self._s.serialize(
                        specific, exclude=_BASE_PAGE_NAMES | {'id'}
                    )

                self.console.detail(f'    page: {label} [{page.slug}]')
                result.append({
                    'model': label,
                    'slug': page.slug,
                    'slug_path': _slug_path(page),
                    'parent_slug_path': parent_slug_path,
                    'base_fields': base_fields,
                    'specific_fields': specific_fields,
                })
            except Exception as e:
                self.console.warn(
                    f'page pk={getattr(page, "pk", "?")} '
                    f'[{getattr(page, "slug", "?")}]: {e} — skipped'
                )
            finally:
                self._tick(getattr(page, 'slug', ''))
        return result

    # ── Dangling reference report ─────────────────────────────────────────

    def missing_media_report(self) -> list:
        """[(kind, pk, [referrer, …]), …] for media rows that no longer exist."""
        rows = []
        for kind, table in (
            ('image', self._media.missing_images),
            ('document', self._media.missing_documents),
        ):
            for pk, referrers in sorted(table.items(), key=lambda kv: str(kv[0])):
                rows.append((kind, pk, referrers))
        return rows

    # ── Media manifest builders ───────────────────────────────────────────

    def images_manifest(self) -> list:
        result = []
        for pk, img in self._media.images.items():
            name = getattr(img.file, 'name', None) if img.file else None
            result.append({
                'pk': pk,
                'title': img.title,
                'file': f'media/images/{pk}/{os.path.basename(name)}' if name else None,
                'focal_point_x': img.focal_point_x,
                'focal_point_y': img.focal_point_y,
                'focal_point_width': img.focal_point_width,
                'focal_point_height': img.focal_point_height,
                'width': getattr(img, 'width', None),
                'height': getattr(img, 'height', None),
            })
        return result

    def documents_manifest(self) -> list:
        result = []
        for pk, doc in self._media.documents.items():
            name = getattr(doc.file, 'name', None) if doc.file else None
            result.append({
                'pk': pk,
                'title': doc.title,
                'file': f'media/documents/{pk}/{os.path.basename(name)}' if name else None,
            })
        return result


# ---------------------------------------------------------------------------
# Management command
# ---------------------------------------------------------------------------

class Command(BaseCommand):
    help = 'Export Icarus pages and snippets to a portable ZIP archive'

    def add_arguments(self, parser):
        parser.add_argument(
            '--output', default='icarus_content.zip',
            help=(
                'Destination ZIP path (default: icarus_content.zip). '
                'Pass a directory to get an auto-timestamped filename inside it.'
            ),
        )
        parser.add_argument(
            '--scope',
            choices=['snippets', 'structural', 'full'],
            default='full',
            help=(
                'full       — every page + snippets + media  [default]\n'
                'structural — snippets + HomePage / index pages only\n'
                'snippets   — snippets + media only'
            ),
        )
        parser.add_argument(
            '--no-media', action='store_true',
            help='Record image/document metadata but do not embed the files',
        )
        parser.add_argument(
            '--no-progress', action='store_true',
            help='Disable progress bars (they are auto-disabled when piped)',
        )

    # ── output path resolution ───────────────────────────────────────────

    @staticmethod
    def _resolve_output(raw: str) -> Path:
        path = Path(raw).expanduser()
        if path.is_dir() or raw.endswith(('/', os.sep)):
            path = path / f'icarus_content_{datetime.now():%Y%m%d_%H%M%S}.zip'
        if not path.suffix:
            path = path.with_suffix('.zip')
        if path.parent and not path.parent.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def handle(self, *args, **options):
        started = time.monotonic()
        scope = options['scope']
        include_media = not options['no_media']
        console = Console(
            self.stdout, self.stderr, self.style,
            verbosity=options.get('verbosity', 1),
            progress=not options['no_progress'],
        )
        output = self._resolve_output(options['output'])

        ex = ContentExporter(console)

        # ── 1. Snippets ──────────────────────────────────────────────────
        console.heading('Exporting snippets…')
        ex.bar = console.bar(ex.snippet_total(), 'snippets')
        snippets = {
            'issue.volume':                             ex.export_volumes(),
            'issue.topic':                              ex.export_topics(),
            'home.sitecontactsettings':                 ex.export_site_contact(),
            'home.siteheader':                          ex.export_site_header(),
            'home.sitefooter':                          ex.export_site_footer(),
            'literati.editorialboard':                  ex.export_editorial_boards(),
            'literati.editorialboardmembershiphistory': ex.export_membership_history(),
        }
        ex.bar.finish()
        ex.bar = None
        for key, rows in snippets.items():
            if rows:
                console.line(f'  {key}: {len(rows)} record(s)')

        # ── 2. Pages ─────────────────────────────────────────────────────
        pages = []
        if scope in ('structural', 'full'):
            console.heading('Exporting pages…')
            # None = no filtering, i.e. every page model in the tree
            allowed = None if scope == 'full' else STRUCTURAL_PAGE_MODELS
            root = Page.get_first_root_node()
            total_pages = root.get_descendants(inclusive=False).count()
            ex.bar = console.bar(total_pages, 'pages')
            pages = ex.export_pages(allowed_models=allowed)
            ex.bar.finish()
            ex.bar = None
            console.line(f'  {len(pages)} page(s) exported')
            if not pages:
                console.warn(
                    f'no pages matched scope "{scope}" — is the page tree empty?'
                )
            # Media is only harvested from content that is actually exported, so
            # an excluded page takes its images and documents with it.
            skipped = total_pages - len(pages)
            if scope != 'full' and skipped > 0:
                console.warn(
                    f'{skipped} page(s) excluded by --scope {scope}; their images '
                    f'and documents are NOT in this archive. '
                    f'Use --scope full to include Issues / Articles / Literati.'
                )

        # ── 3. Site record ───────────────────────────────────────────────
        site = Site.objects.filter(is_default_site=True).first()
        site_data = {
            'hostname': site.hostname,
            'port': site.port,
            'site_name': site.site_name,
        } if site else {}
        if not site:
            console.warn('no default Site record found — site data omitted')

        # ── 4. Build manifest ────────────────────────────────────────────
        manifest = {
            'version': '1.0',
            'created_at': datetime.now().isoformat(),
            'wagtail_version': '.'.join(str(v) for v in wagtail.VERSION[:3]),
            'scope': scope,
            'site': site_data,
            'images': ex.images_manifest(),
            'documents': ex.documents_manifest(),
            'snippets': snippets,
            'pages': pages,
        }

        # ── 5. Write ZIP ─────────────────────────────────────────────────
        console.heading(f'Writing {output}…')
        media_bytes = 0
        media_written = 0
        with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(
                'manifest.json',
                json.dumps(manifest, indent=2, default=str),
            )

            if include_media:
                targets = (
                    [('image', e, ex._media.images.get(e['pk']))
                     for e in manifest['images']]
                    + [('document', e, ex._media.documents.get(e['pk']))
                       for e in manifest['documents']]
                )
                bar = console.bar(len(targets), 'media')
                for kind, entry, obj in targets:
                    try:
                        if obj is None or not obj.file or not entry['file']:
                            continue
                        zf.write(
                            obj.file.path, entry['file'],
                            compress_type=_compression_for(entry['file']),
                        )
                        media_written += 1
                        try:
                            media_bytes += obj.file.size
                        except Exception:
                            pass
                    except (FileNotFoundError, ValueError,
                            NotImplementedError, OSError) as e:
                        console.warn(f'{kind} "{getattr(obj, "title", "?")}": {e}')
                    finally:
                        bar.advance(suffix=os.path.basename(entry.get('file') or ''))
                bar.finish()
            else:
                console.line('  --no-media: file bytes not embedded')

        # ── Summary ──────────────────────────────────────────────────────
        elapsed = time.monotonic() - started
        try:
            archive_size = output.stat().st_size
        except OSError:
            archive_size = 0

        snippet_summary = ' · '.join(
            f'{k.split(".")[-1]} {len(v)}'
            for k, v in snippets.items() if v
        ) or 'none'

        console.success(
            f'\n✓  Export saved → {output}\n'
            f'   archive : {human_bytes(archive_size)} in {human_duration(elapsed)}\n'
            f'   pages   : {len(pages)}\n'
            f'   media   : {media_written}/'
            f'{len(manifest["images"]) + len(manifest["documents"])} file(s) '
            f'embedded ({human_bytes(media_bytes)} on disk)\n'
            f'   snippets: {snippet_summary}'
        )
        missing = ex.missing_media_report()
        if missing:
            self.stdout.write(self.style.WARNING(
                f'\n   {len(missing)} media reference(s) point at rows that no '
                f'longer exist in the database:'
            ))
            for kind, pk, referrers in missing:
                first = referrers[0] if referrers else '?'
                more = (
                    f' and {len(referrers) - 1} other record(s)'
                    if len(referrers) > 1 else ''
                )
                self.stdout.write(self.style.WARNING(
                    f'     {kind} pk={pk} — referenced by {first}{more}'
                ))
            self.stdout.write(self.style.WARNING(
                '     These are pre-existing broken links in the content itself '
                '(media deleted while still referenced).\n'
                '     Everything else exported normally; fix the content or '
                're-upload the media to clear them.'
            ))

        if console.warnings:
            hint = '' if console.verbosity >= 2 else ' — re-run with -v 2 for detail'
            self.stdout.write(self.style.WARNING(
                f'   {console.warnings} warning(s){hint}'
            ))
