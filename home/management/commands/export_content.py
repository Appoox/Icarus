"""
export_content  —  Icarus content export command
=================================================
Serialises pages, snippets, images, and documents to a portable ZIP archive
that can be imported on a fresh database with `import_content`.

Usage
-----
    # Structural pages (HomePage + index pages) + all snippets + media  [default]
    python manage.py export_content

    # Custom output path
    python manage.py export_content --output /backups/prod_20260701.zip

    # Scope options
    python manage.py export_content --scope snippets     # snippets + media only
    python manage.py export_content --scope structural   # structural pages + snippets + media
    python manage.py export_content --scope full         # everything incl. Issues / Articles / Literati

    # Skip embedding media files (manifest only — import will skip missing files)
    python manage.py export_content --no-media

ZIP contents
------------
    manifest.json          – all serialised data (pages, snippets, image/doc metadata)
    media/images/<file>    – original image files referenced by exported content
    media/documents/<file> – document files (PDFs, MP3s, etc.)

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
import zipfile
from datetime import datetime, date
from pathlib import Path

import wagtail
from django.apps import apps
from django.core.management.base import BaseCommand
from django.db import models as dj_models
from wagtail.fields import StreamField
from wagtail.images import get_image_model
from wagtail.documents import get_document_model
from wagtail.models import Page, Site

# ---------------------------------------------------------------------------
# Page model sets
# ---------------------------------------------------------------------------

STRUCTURAL_PAGE_MODELS = {
    'home.homepage',
    'articles.articleindexpage',
    'issue.issueindexpage',
    'literati.authorindexpage',
}

FULL_PAGE_MODELS = STRUCTURAL_PAGE_MODELS | {
    'issue.issue',
    'articles.article',
    'literati.literati',
}

# Field names that live on wagtailcore_page — skipped when serialising the
# MTI child table so we don't double-export them.
_BASE_PAGE_NAMES: frozenset = frozenset(
    f.name for f in Page._meta.get_fields()
) | {'id', 'page_ptr', 'page_ptr_id'}


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


# ---------------------------------------------------------------------------
# Media registry – tracks every image/doc found during traversal
# ---------------------------------------------------------------------------

class _MediaRegistry:
    def __init__(self, stderr):
        self._err = stderr
        self.images: dict = {}     # {orig_pk: image_instance}
        self.documents: dict = {}  # {orig_pk: document_instance}

    def note_image(self, pk):
        if pk is None or pk in self.images:
            return
        Im = get_image_model()
        try:
            self.images[pk] = Im.objects.get(pk=pk)
        except Im.DoesNotExist:
            self._err.write(f'    [!] Image pk={pk} not found — skipped')

    def note_document(self, pk):
        if pk is None or pk in self.documents:
            return
        Do = get_document_model()
        try:
            self.documents[pk] = Do.objects.get(pk=pk)
        except Do.DoesNotExist:
            self._err.write(f'    [!] Document pk={pk} not found — skipped')


# ---------------------------------------------------------------------------
# Field serialiser
# ---------------------------------------------------------------------------

class _Serialiser:
    """Converts model field values to JSON-safe dicts, noting media refs."""

    def __init__(self, media: _MediaRegistry, stderr):
        self._media = media
        self._err = stderr

    # ── StreamField scanning ────────────────────────────────────────────

    def _scan_stream(self, node):
        """Recursively note every image/document PK inside StreamField JSON."""
        if isinstance(node, list):
            for item in node:
                self._scan_stream(item)
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
                    self._scan_stream(v)

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
            self._scan_stream(parsed)
            return parsed  # store as Python list, not raw string

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

        # ── ManyToMany ─── handled by serialize_instance separately ─────
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

            if not hasattr(field, 'column'):
                continue   # skip reverse relations, GenericRelation, etc.

            try:
                val = self.field_value(instance, field)
                if val != '__m2m__':
                    data[name] = val
            except Exception as e:
                self._err.write(f'    [!] {type(instance).__name__}.{name}: {e}')

        if m2m:
            data['__m2m'] = m2m
        return data


# ---------------------------------------------------------------------------
# High-level exporter
# ---------------------------------------------------------------------------

class ContentExporter:
    def __init__(self, stdout, stderr):
        self.stdout = stdout
        self.stderr = stderr
        self._media = _MediaRegistry(stderr)
        self._s = _Serialiser(self._media, stderr)

    # ── Snippets ─────────────────────────────────────────────────────────

    def export_volumes(self):
        from issue.models import Volume
        return [self._s.serialize(v, exclude={'id'}) for v in Volume.objects.all()]

    def export_topics(self):
        from issue.models import Topic
        return [self._s.serialize(t, exclude={'id'}) for t in Topic.objects.all()]

    def export_site_contact(self):
        from home.models import SiteContactSettings
        return [self._s.serialize(s, exclude={'id'}) for s in SiteContactSettings.objects.all()]

    def export_site_header(self):
        from home.models import SiteHeader
        return [self._s.serialize(h, exclude={'id'}) for h in SiteHeader.objects.all()]

    def export_site_footer(self):
        """Export SiteFooter with its full Section → Column → Link hierarchy."""
        from home.models import SiteFooter
        result = []
        for footer in SiteFooter.objects.all():
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
        return result

    def export_editorial_boards(self):
        """Export each board with its ordered member list."""
        from literati.models import EditorialBoard
        result = []
        for board in EditorialBoard.objects.all():
            bd = self._s.serialize(board, exclude={'id'})
            bd['_members'] = [
                self._s.serialize(m, exclude={'id', 'board', 'board_id'})
                for m in board.members.order_by('sort_order')
            ]
            result.append(bd)
        return result

    def export_membership_history(self):
        from literati.models import EditorialBoardMembershipHistory
        return [
            self._s.serialize(h, exclude={'id'})
            for h in EditorialBoardMembershipHistory.objects.all()
        ]

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
            specific = page.specific
            label = (
                f'{type(specific)._meta.app_label}.'
                f'{type(specific)._meta.model_name}'
            )
            if allowed_models is not None and label not in allowed_models:
                continue

            parent = page.get_parent()
            parent_slug_path = (
                _slug_path(parent)
                if parent and not parent.is_root()
                else None
            )

            # Base wagtailcore_page fields
            base_fields = self._s.serialize(page, exclude={
                'id', 'path', 'depth', 'numchild',
                'content_type', 'content_type_id',
                'locale', 'locale_id',
                'translation_key', 'alias_of', 'alias_of_id',
            })

            # MTI-specific fields only
            if type(specific) is Page:
                specific_fields = {}
            else:
                specific_fields = self._s.serialize(
                    specific, exclude=_BASE_PAGE_NAMES | {'id'}
                )

            self.stdout.write(f'    page: {label} [{page.slug}]')
            result.append({
                'model': label,
                'slug': page.slug,
                'slug_path': _slug_path(page),
                'parent_slug_path': parent_slug_path,
                'base_fields': base_fields,
                'specific_fields': specific_fields,
            })
        return result

    # ── Media manifest builders ───────────────────────────────────────────

    def images_manifest(self) -> list:
        result = []
        for pk, img in self._media.images.items():
            result.append({
                'pk': pk,
                'title': img.title,
                'file': f'media/images/{os.path.basename(img.file.name)}' if img.file else None,
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
            result.append({
                'pk': pk,
                'title': doc.title,
                'file': (
                    f'media/documents/{os.path.basename(doc.file.name)}'
                    if doc.file else None
                ),
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
            help='Destination ZIP path  (default: icarus_content.zip)',
        )
        parser.add_argument(
            '--scope',
            choices=['snippets', 'structural', 'full'],
            default='structural',
            help=(
                'snippets   — snippets + media only\n'
                'structural — snippets + HomePage / index pages + media  [default]\n'
                'full       — everything incl. Issues / Articles / Literati'
            ),
        )
        parser.add_argument(
            '--no-media', action='store_true',
            help='Record image/document metadata but do not embed the files',
        )

    def handle(self, *args, **options):
        output = options['output']
        scope = options['scope']
        include_media = not options['no_media']

        ex = ContentExporter(self.stdout, self.stderr)

        # ── 1. Snippets ──────────────────────────────────────────────────
        self.stdout.write(self.style.MIGRATE_HEADING('Exporting snippets…'))
        snippets = {
            'issue.volume':                                   ex.export_volumes(),
            'issue.topic':                                    ex.export_topics(),
            'home.sitecontactsettings':                       ex.export_site_contact(),
            'home.siteheader':                                ex.export_site_header(),
            'home.sitefooter':                                ex.export_site_footer(),
            'literati.editorialboard':                        ex.export_editorial_boards(),
            'literati.editorialboardmembershiphistory':       ex.export_membership_history(),
        }
        for key, rows in snippets.items():
            if rows:
                self.stdout.write(f'  {key}: {len(rows)} record(s)')

        # ── 2. Pages ─────────────────────────────────────────────────────
        pages = []
        if scope in ('structural', 'full'):
            self.stdout.write(self.style.MIGRATE_HEADING('Exporting pages…'))
            allowed = (
                FULL_PAGE_MODELS if scope == 'full' else STRUCTURAL_PAGE_MODELS
            )
            pages = ex.export_pages(allowed_models=allowed)
            self.stdout.write(f'  {len(pages)} page(s) exported')

        # ── 3. Site record ───────────────────────────────────────────────
        site = Site.objects.filter(is_default_site=True).first()
        site_data = {
            'hostname': site.hostname,
            'port': site.port,
            'site_name': site.site_name,
        } if site else {}

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
        self.stdout.write(self.style.MIGRATE_HEADING(f'Writing {output}…'))
        with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(
                'manifest.json',
                json.dumps(manifest, indent=2, default=str),
            )

            if include_media:
                for entry in manifest['images']:
                    img = ex._media.images[entry['pk']]
                    if img.file and entry['file']:
                        try:
                            zf.write(img.file.path, entry['file'])
                        except (FileNotFoundError, ValueError) as e:
                            self.stderr.write(f'  [!] image "{img.title}": {e}')

                for entry in manifest['documents']:
                    doc = ex._media.documents[entry['pk']]
                    if doc.file and entry['file']:
                        try:
                            zf.write(doc.file.path, entry['file'])
                        except (FileNotFoundError, ValueError) as e:
                            self.stderr.write(f'  [!] document "{doc.title}": {e}')

        # ── Summary ──────────────────────────────────────────────────────
        snippet_summary = '  '.join(
            f'{k.split(".")[-1]}×{len(v)}'
            for k, v in snippets.items() if v
        )
        self.stdout.write(self.style.SUCCESS(
            f'\n✓  Export saved → {output}\n'
            f'   images: {len(manifest["images"])}  '
            f'documents: {len(manifest["documents"])}  '
            f'pages: {len(pages)}\n'
            f'   {snippet_summary}'
        ))
