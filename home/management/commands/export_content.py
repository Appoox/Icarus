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

    # Full restore only: include authors' phone/email/address (keep it private)
    python manage.py export_content --include-personal-data

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
    Snippet  → {"__ref": "snippet",  "model": "issue.topic", "key": {"slug": …}}
Any other FK (user accounts above all) is written as null.

Ids inside StreamField choosers and rich text are mapped on import through
the manifest's page_paths / snippet_keys tables (format 1.1).

StreamField JSON is stored parsed (list), not as a string.

Personal data
-------------
Authors' contact details are left out unless --include-personal-data is
given (see PERSONAL_DATA_FIELDS).
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
from taggit.managers import TaggableManager
from wagtail.fields import RichTextField, StreamField
from wagtail.images import get_image_model
from wagtail.documents import get_document_model
from wagtail.models import Page, Site

from ._console import (
    Console, PRECOMPRESSED_SUFFIXES, human_bytes, human_duration,
)
from ._richtext import iter_richtext_refs
from ._streamwalk import IMAGE_FIELD_NAMES, RefMapper, map_generic, map_stream

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

#: Base-page fields that must never cross databases.  `owner` / `locked_by`
#: are FKs into the user table and `latest_revision` / `live_revision` point at
#: Revision rows — copying those PKs onto a fresh database raises IntegrityError
#: because the referenced rows do not exist there.  `url_path` is derived from
#: the page tree and is recomputed by Wagtail on add_child()/move().
_UNSAFE_PAGE_FIELDS = {
    'owner', 'owner_id',
    'locked_by', 'locked_by_id',
    'latest_revision', 'latest_revision_id',
    'live_revision', 'live_revision_id',
    'url_path',
}

_PAGE_BASE_EXCLUDE = {
    'id', 'path', 'depth', 'numchild',
    'content_type', 'content_type_id',
    'locale', 'locale_id',
    'translation_key', 'alias_of', 'alias_of_id',
} | _UNSAFE_PAGE_FIELDS

#: Snippet models the importer upserts by natural key.  A plain integer FK to
#: one of these would point at the *source* row id, which is meaningless on the
#: destination, so they are exported as a natural-key reference instead.
SNIPPET_NATURAL_KEYS = {
    'issue.volume': ('number',),
    'issue.topic': ('slug',),
    'literati.editorialboard': ('name',),
}


def _personal_data_fields():
    # Same list the audit log excludes (literati.apps), so there is one
    # definition of "an author's contact details".
    from literati.apps import LITERATI_AUDIT_EXCLUDED_FIELDS
    return {'literati.literati': frozenset(LITERATI_AUDIT_EXCLUDED_FIELDS)}


#: Page fields left out of an archive unless --include-personal-data is given.
PERSONAL_DATA_FIELDS = _personal_data_fields()

#: Archive format. 1.1 adds page/snippet lookup tables, child rows, tags,
#: media hashes, and leaves user FKs and (by default) personal data out.
ARCHIVE_VERSION = '1.1'


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


def _file_hash(obj) -> str:
    """
    The media file's SHA-1, as Wagtail stores it. Rows saved outside the
    admin may have none stored; hash the file then, without writing to the
    database (an export only reads).
    """
    if getattr(obj, 'file_hash', ''):
        return obj.file_hash
    from wagtail.utils.file import hash_filelike

    try:
        with obj.open_file() as f:
            return hash_filelike(f)
    except Exception:
        return ''


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

class _Collector(RefMapper):
    """Export-side mapper: records media references, changes nothing."""

    def __init__(self, serialiser):
        self._s = serialiser

    def image(self, pk):
        self._s._media.note_image(pk)
        return pk

    def document(self, pk):
        self._s._media.note_document(pk)
        return pk

    def richtext(self, html):
        self._s.scan_richtext(html)
        return html


class _Serialiser:
    """Converts model field values to JSON-safe dicts, noting media refs."""

    # Block fields that hold a bare image PK. Struct blocks name their image
    # child by field (e.g. `image`, `image_override`), so those need a
    # field-name sweep or their images never make it into the export bundle.
    # (The walk itself lives in _streamwalk, shared with import_content.)
    _IMAGE_FIELD_NAMES = IMAGE_FIELD_NAMES

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

    def _scan_stream(self, node, stream_block=None):
        """Note every image/document referenced anywhere in StreamField JSON."""
        map_stream(
            stream_block, node, _Collector(self),
            on_error=lambda e: self._console.warn(f'block-definition scan failed: {e}'),
        )

    def _scan_generic(self, node):
        """Name-based scan (values whose block definitions are unavailable)."""
        map_generic(node, _Collector(self))

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
            # Snippets upserted by natural key on import (Volume, Topic,
            # EditorialBoard): the source PK is meaningless on the destination.
            if rm is not None:
                label = f'{rm._meta.app_label}.{rm._meta.model_name}'
                keys = SNIPPET_NATURAL_KEYS.get(label)
                if keys:
                    obj = rm.objects.filter(pk=raw_id).first()
                    if obj is None:
                        return None
                    return {
                        '__ref': 'snippet',
                        'model': label,
                        'key': {k: getattr(obj, k, None) for k in keys},
                    }
            # Anything else — above all user accounts (Literati.reader_user) —
            # has no portable identity. Exporting the raw PK made the importer
            # attach the row to whichever account had that PK on the
            # destination (and Literati.save() then rewrote that account's
            # phone and email). Left out; Literati.save() re-links the
            # author's reader account by phone number on import.
            return None

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
                elif rm and rm._meta.label_lower in SNIPPET_NATURAL_KEYS:
                    # e.g. Literati.areas_of_interest → Topic: by natural key,
                    # since the source PKs mean nothing on the destination.
                    label = rm._meta.label_lower
                    keys = SNIPPET_NATURAL_KEYS[label]
                    m2m[name] = [
                        {'__ref': 'snippet', 'model': label,
                         'key': {k: getattr(obj, k, None) for k in keys}}
                        for obj in qs
                    ]
                else:
                    self._console.detail(
                        f'    {type(instance).__name__}.{name}: no portable reference for '
                        f'{rm._meta.label_lower if rm else "?"} — not exported'
                    )
                continue

            if isinstance(field, TaggableManager):
                # A manager, not a value (it was written out as the junk
                # string "app.Model.None"). Tags go out by name in 'tags'.
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

    def __init__(self, console: Console, include_personal_data=False):
        self.console = console
        self._media = _MediaRegistry(console)
        self._s = _Serialiser(self._media, console)
        self.bar = None
        self.include_personal_data = include_personal_data
        #: {source page pk: slug path} — lets the importer remap page ids held
        #: in page choosers and rich-text page links.
        self.page_paths = {}

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
            self.page_paths[str(page.pk)] = _slug_path(page)
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
                    exclude = _BASE_PAGE_NAMES | {'id'}
                    if label in PERSONAL_DATA_FIELDS and not self.include_personal_data:
                        exclude = exclude | PERSONAL_DATA_FIELDS[label]
                    specific_fields = self._s.serialize(specific, exclude=exclude)

                self.console.detail(f'    page: {label} [{page.slug}]')
                result.append({
                    'model': label,
                    'slug': page.slug,
                    'slug_path': _slug_path(page),
                    'parent_slug_path': parent_slug_path,
                    'base_fields': base_fields,
                    'specific_fields': specific_fields,
                    'children': self._children(specific),
                    'tags': self._tags(specific),
                })
            except Exception as e:
                self.console.warn(
                    f'page pk={getattr(page, "pk", "?")} '
                    f'[{getattr(page, "slug", "?")}]: {e} — skipped'
                )
            finally:
                self._tick(getattr(page, 'slug', ''))
        return result

    def _children(self, page):
        """
        InlinePanel rows (article authors, reprinted articles…), keyed by the
        relation's accessor. They live in their own tables, so a field-level
        serialise never saw them and every article lost its author credits.
        Tag rows are exported by name in _tags() instead.
        """
        from modelcluster.models import get_all_child_relations
        from taggit.models import ItemBase

        out = {}
        for rel in get_all_child_relations(page):
            if issubclass(rel.related_model, ItemBase):
                continue
            if rel.related_model.__module__.startswith('wagtail.'):
                # Wagtail's own rows (admin comments): tied to user accounts
                # and to this database's revisions, not content.
                continue
            accessor = rel.get_accessor_name()
            fk = rel.field
            out[accessor] = [
                self._s.serialize(child, exclude={'id', fk.name, fk.attname})
                for child in getattr(page, accessor).all()
            ]
        return out

    @staticmethod
    def _tags(page):
        from taggit.managers import TaggableManager

        return {
            f.name: sorted(t.name for t in getattr(page, f.name).all())
            for f in page._meta.get_fields() if isinstance(f, TaggableManager)
        }

    def snippet_keys(self):
        """{label: {source pk: natural key}} for snippet choosers inside streams."""
        from django.apps import apps

        out = {}
        for label, keys in SNIPPET_NATURAL_KEYS.items():
            Model = apps.get_model(label)
            out[label] = {
                str(pk): dict(zip(keys, values))
                for pk, *values in Model.objects.values_list('pk', *keys)
            }
        return out

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
                # Media is matched on import by content, not by title.
                'file_hash': _file_hash(img),
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
                'file_hash': _file_hash(doc),
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
        parser.add_argument(
            '--include-personal-data', action='store_true',
            help=(
                "Include authors' phone numbers, email and postal addresses. "
                'Off by default: an archive is an unencrypted file. Use it '
                'only for a full restore, and keep that archive private.'
            ),
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

        ex = ContentExporter(console, include_personal_data=options['include_personal_data'])
        if not options['include_personal_data']:
            console.line("  authors' contact details left out (use --include-personal-data for a full restore)")

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
            'version': ARCHIVE_VERSION,
            'created_at': datetime.now().isoformat(),
            'wagtail_version': '.'.join(str(v) for v in wagtail.VERSION[:3]),
            'scope': scope,
            'personal_data': bool(options['include_personal_data']),
            'site': site_data,
            'images': ex.images_manifest(),
            'documents': ex.documents_manifest(),
            'snippets': snippets,
            'pages': pages,
            'page_paths': ex.page_paths,
            'snippet_keys': ex.snippet_keys(),
        }

        # ── 5. Write ZIP ─────────────────────────────────────────────────
        console.heading(f'Writing {output}…')
        media_bytes = 0
        media_written = 0
        # Written beside the target and renamed into place only when complete,
        # so an interrupted export never leaves a truncated archive that looks
        # like a good one.
        partial = output.with_name(output.name + '.partial')
        try:
            media_written, media_bytes = self._write_zip(partial, manifest, ex, include_media, console)
            os.replace(partial, output)
        finally:
            if partial.exists():
                partial.unlink()

        # ── Summary ──────────────────────────────────────────────────────
        self._summary(started, output, pages, manifest, snippets, ex, media_written, media_bytes, console)

    def _write_zip(self, path, manifest, ex, include_media, console):
        media_bytes = 0
        media_written = 0
        with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as zf:
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
        return media_written, media_bytes

    def _summary(self, started, output, pages, manifest, snippets, ex, media_written, media_bytes, console):
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
