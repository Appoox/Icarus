"""
import_content  —  Icarus content import command
=================================================
Reads a ZIP produced by `export_content` and recreates pages, snippets,
images, and documents on a clean (or partial) database.

Usage
-----
    python manage.py import_content icarus_content.zip

    # Preview what would happen without writing anything
    python manage.py import_content icarus_content.zip --dry-run

    # Don't abort on individual row errors — skip and continue
    python manage.py import_content icarus_content.zip --tolerant

    # Skip the "this will modify your database" confirmation
    python manage.py import_content icarus_content.zip --yes

    # Force new media objects even when a title already matches
    python manage.py import_content icarus_content.zip --replace-existing-media

    # Output control
    python manage.py import_content icarus_content.zip -v 2   # per-record lines
    python manage.py import_content icarus_content.zip --no-progress

Idempotency
-----------
• Images and documents are looked up by title before creating; duplicates
  are reused rather than created twice.
• Snippets (Volume, Topic, SiteHeader …) are upserted by their natural key
  (number / slug / name).
• Pages are looked up by slug-path.  If a page already exists under the
  correct parent it is updated in-place; otherwise it is created.

FK remapping
------------
All {"__ref": "image", "pk": N} pointers are resolved after the objects they
reference have been created.  A remapping table {original_pk → new_pk} is
maintained for images and documents throughout the run.

Note on --dry-run
-----------------
Database writes are rolled back, but file-storage writes are NOT transactional,
so a dry run never saves media files to disk — it only reports what it would
create.  FK pointers to those not-yet-created images resolve to None during the
preview; that is expected and is discarded with the rollback.
"""

import io
import json
import os
import sys
import time
import zipfile
from collections import Counter
from datetime import date, datetime

import wagtail
from django.apps import apps as django_apps
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from wagtail.images import get_image_model
from wagtail.documents import get_document_model
from wagtail.models import Page, Site, Locale

from ._console import Console, human_duration
from ._richtext import rewrite_richtext_refs


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_page_by_slug_path(slug_path):
    """Resolve 'home/issues/jan-2026' → Page instance (or None)."""
    if not slug_path:
        return None
    parts = [p for p in slug_path.split('/') if p]
    if not parts:
        return None
    current = Page.get_first_root_node()
    for slug in parts:
        try:
            current = current.get_children().get(slug=slug)
        except Page.DoesNotExist:
            return None
    return current


def _parse_date(val):
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    try:
        return date.fromisoformat(val)
    except (ValueError, TypeError):
        return None


def _parse_datetime(val):
    if val is None:
        return None
    if isinstance(val, datetime):
        return val
    try:
        return datetime.fromisoformat(val)
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# FK / StreamField resolver
# ---------------------------------------------------------------------------

class _Resolver:
    """Resolves __ref dicts and rewrites StreamField JSON using remap tables."""

    def __init__(self, image_map, doc_map, console: Console):
        self._img = image_map   # {original_pk -> new_pk}
        self._doc = doc_map
        self._console = console

    # ── Snippet natural keys ─────────────────────────────────────────────

    def resolve_snippet(self, ref):
        """
        Resolve {"__ref": "snippet", "model": "issue.volume", "key": {...}}
        to the destination PK. Snippets are upserted by natural key, so their
        PKs differ from the source database.
        """
        label = ref.get('model', '')
        key = ref.get('key') or {}
        try:
            app_label, model_name = label.split('.')
            Model = django_apps.get_model(app_label, model_name)
        except (LookupError, ValueError) as e:
            self._console.warn(f'cannot resolve snippet model "{label}": {e}')
            return None
        try:
            obj = Model.objects.filter(**key).first()
        except Exception as e:
            self._console.warn(f'snippet lookup {label} {key} failed: {e}')
            return None
        if obj is None:
            self._console.warn(
                f'snippet {label} {key} not found — foreign key left empty'
            )
            return None
        return obj.pk

    # ── M2M lists ────────────────────────────────────────────────────────

    def resolve_m2m(self, ref_list):
        out = []
        for item in ref_list:
            if not isinstance(item, dict):
                out.append(item)
                continue
            ref = item.get('__ref')
            if ref == 'page':
                pg = _get_page_by_slug_path(item.get('slug_path'))
                if pg:
                    out.append(pg.pk)
            elif ref == 'image':
                new_pk = self._img.get(item['pk'])
                if new_pk:
                    out.append(new_pk)
            elif ref == 'document':
                new_pk = self._doc.get(item['pk'])
                if new_pk:
                    out.append(new_pk)
            elif ref == 'snippet':
                new_pk = self.resolve_snippet(item)
                if new_pk:
                    out.append(new_pk)
            else:
                out.append(item)
        return out

    # ── Rich text ─────────────────────────────────────────────────────────

    def rewrite_richtext(self, html):
        """Remap <embed embedtype="image" id> / <a linktype="document" id>."""
        return rewrite_richtext_refs(html, self._img, self._doc)

    # ── StreamField blocks ────────────────────────────────────────────────

    def rewrite_stream(self, blocks, stream_block=None):
        """
        Remap every media PK inside a StreamField value.

        When the field's block definitions are available they drive the walk,
        which catches media wherever it is nested; the name-based pass below is
        kept as a fallback for values whose definitions no longer resolve.
        """
        if stream_block is not None:
            try:
                return self._rewrite_by_definition(stream_block, blocks)
            except Exception as e:
                self._console.warn(f'block-definition rewrite failed: {e}')
        return self._rewrite_generic(blocks)

    def _rewrite_by_definition(self, block, value):
        from wagtail import blocks as wblocks
        from wagtail.images.blocks import ImageChooserBlock
        from wagtail.documents.blocks import DocumentChooserBlock

        if value is None:
            return None

        if isinstance(block, ImageChooserBlock):
            try:
                return self._img.get(int(value), value)
            except (TypeError, ValueError):
                return value
        if isinstance(block, DocumentChooserBlock):
            try:
                return self._doc.get(int(value), value)
            except (TypeError, ValueError):
                return value
        if isinstance(block, wblocks.RichTextBlock):
            return self.rewrite_richtext(value)
        if isinstance(block, wblocks.StructBlock):
            if not isinstance(value, dict):
                return value
            out = dict(value)
            for name, child in block.child_blocks.items():
                if name in out:
                    out[name] = self._rewrite_by_definition(child, out[name])
            return out
        if isinstance(block, wblocks.StreamBlock):
            out = []
            for item in value or []:
                if not isinstance(item, dict):
                    out.append(item)
                    continue
                item = dict(item)
                child = block.child_blocks.get(item.get('type'))
                if child is not None:
                    item['value'] = self._rewrite_by_definition(
                        child, item.get('value')
                    )
                out.append(item)
            return out
        if isinstance(block, wblocks.ListBlock):
            out = []
            for item in value or []:
                if isinstance(item, dict) and 'value' in item:
                    item = dict(item)
                    item['value'] = self._rewrite_by_definition(
                        block.child_block, item['value']
                    )
                    out.append(item)
                else:
                    out.append(
                        self._rewrite_by_definition(block.child_block, item)
                    )
            return out
        if isinstance(value, str):
            return self.rewrite_richtext(value)
        return value

    def _rewrite_generic(self, blocks):
        if not isinstance(blocks, list):
            return blocks
        out = []
        for block in blocks:
            if not isinstance(block, dict):
                out.append(block)
                continue
            block = dict(block)
            btype = block.get('type', '')
            val   = block.get('value')

            if btype == 'image' and isinstance(val, dict):
                old_pk = val.get('image')
                if old_pk is not None:
                    val = dict(val)
                    val['image'] = self._img.get(old_pk, old_pk)
                    block['value'] = val

            elif btype == 'document' and isinstance(val, (int, str)):
                try:
                    old_pk = int(val)
                    block['value'] = self._doc.get(old_pk, old_pk)
                except (TypeError, ValueError):
                    pass

            elif btype in ('audio', 'video') and isinstance(val, dict):
                val = dict(val)
                for key in ('audio_file', 'video_file'):
                    if val.get(key) is not None:
                        val[key] = self._doc.get(val[key], val[key])
                block['value'] = val

            elif isinstance(val, str):
                block['value'] = self.rewrite_richtext(val)
            elif isinstance(val, list):
                block['value'] = self._rewrite_generic(val)
            elif isinstance(val, dict):
                block['value'] = {
                    k: (self._rewrite_generic(v) if isinstance(v, list)
                        else self.rewrite_richtext(v) if isinstance(v, str)
                        else v)
                    for k, v in val.items()
                }

            out.append(block)
        return out


# ---------------------------------------------------------------------------
# Media importer
# ---------------------------------------------------------------------------

class _MediaImporter:
    def __init__(self, zf, console: Console, stats, dry_run=False,
                 replace_existing=False):
        self._zf = zf
        self._console = console
        self._stats = stats
        self._dry = dry_run
        self._replace = replace_existing
        self.image_map = {}   # {original_pk -> new_pk}
        self.doc_map   = {}

    @staticmethod
    def _dimensions(entry, raw):
        """
        Resolve an image's pixel dimensions.

        wagtailimages_image.width/height are NOT NULL and nothing on the model
        populates them — Wagtail fills them in from its upload form, which an
        import never goes through. The manifest records them; when it doesn't
        (older archive), fall back to reading the bytes with Pillow, which
        Wagtail already depends on.
        """
        width, height = entry.get('width'), entry.get('height')
        if width and height:
            return int(width), int(height)
        try:
            from PIL import Image as PILImage
            with PILImage.open(io.BytesIO(raw)) as probe:
                return probe.size
        except Exception:
            return None, None

    def _load(self, entry, kind):
        """Common lookup/extract step. Returns (title, file_path, raw|None)."""
        title = entry.get('title') or f'{kind}_{entry["pk"]}'
        file_path = entry.get('file')
        if not file_path:
            self._console.warn(f'{kind} "{title}": no file path — skipped')
            return title, None, None
        try:
            return title, file_path, self._zf.read(file_path)
        except KeyError:
            self._console.warn(f'{kind} "{title}": not in ZIP — skipped')
            return title, file_path, None

    def import_images(self, entries):
        Im = get_image_model()
        bar = self._console.bar(len(entries), 'images')
        for entry in entries:
            try:
                orig_pk = entry['pk']
                title = entry.get('title') or f'image_{orig_pk}'

                existing = (
                    None if self._replace
                    else Im.objects.filter(title=title).first()
                )
                if existing:
                    self._console.detail(f'    ↩  image "{title}" — reusing pk={existing.pk}')
                    self.image_map[orig_pk] = existing.pk
                    self._stats['images_reused'] += 1
                    continue

                title, file_path, raw = self._load(entry, 'image')
                if raw is None:
                    continue

                if self._dry:
                    self._console.detail(f'    +  image "{title}" — would create')
                    self._stats['images_created'] += 1
                    continue

                width, height = self._dimensions(entry, raw)
                if not width or not height:
                    self._console.warn(
                        f'image "{title}": cannot determine pixel dimensions '
                        f'— skipped (width/height are NOT NULL)'
                    )
                    continue

                img = Im(title=title)
                for attr in ('focal_point_x', 'focal_point_y',
                             'focal_point_width', 'focal_point_height'):
                    if entry.get(attr) is not None:
                        setattr(img, attr, entry[attr])

                # Write the file first with save=False, then set the dimensions,
                # then save once.  Assigning the file can reset width/height via
                # Django's dimension-field hook, so our values must be applied
                # after it and before the INSERT.
                img.file.save(
                    os.path.basename(file_path), ContentFile(raw), save=False
                )
                img.width, img.height = width, height
                img.file_size = len(raw)
                try:
                    img._set_file_hash()
                except Exception:
                    pass          # optional metadata; never fail the import
                img.save()

                self.image_map[orig_pk] = img.pk
                self._stats['images_created'] += 1
                self._console.detail(f'    +  image "{title}" → pk={img.pk}')
            finally:
                bar.advance(suffix=entry.get('title', ''))
        bar.finish()

    def import_documents(self, entries):
        Do = get_document_model()
        bar = self._console.bar(len(entries), 'documents')
        for entry in entries:
            try:
                orig_pk = entry['pk']
                title = entry.get('title') or f'document_{orig_pk}'

                existing = (
                    None if self._replace
                    else Do.objects.filter(title=title).first()
                )
                if existing:
                    self._console.detail(f'    ↩  document "{title}" — reusing pk={existing.pk}')
                    self.doc_map[orig_pk] = existing.pk
                    self._stats['documents_reused'] += 1
                    continue

                title, file_path, raw = self._load(entry, 'document')
                if raw is None:
                    continue

                if self._dry:
                    self._console.detail(f'    +  document "{title}" — would create')
                    self._stats['documents_created'] += 1
                    continue

                doc = Do(title=title)
                doc.file.save(
                    os.path.basename(file_path), ContentFile(raw), save=False
                )
                doc.file_size = len(raw)
                try:
                    doc._set_file_hash()
                except Exception:
                    pass          # optional metadata; never fail the import
                doc.save()
                self.doc_map[orig_pk] = doc.pk
                self._stats['documents_created'] += 1
                self._console.detail(f'    +  document "{title}" → pk={doc.pk}')
            finally:
                bar.advance(suffix=entry.get('title', ''))
        bar.finish()


# ---------------------------------------------------------------------------
# Field applicator
# ---------------------------------------------------------------------------

class _Applicator:
    """Applies a serialised dict back onto a model instance."""

    def __init__(self, resolver, console: Console):
        self._res = resolver
        self._console = console

    def apply(self, instance, data, skip_fields=None):
        """
        Copy values from `data` onto `instance` fields.
        Does NOT call save() — the caller is responsible.
        """
        from django.db import models as _djm
        from wagtail.fields import RichTextField as _RTF, StreamField as _SF

        Im   = get_image_model()
        Do   = get_document_model()
        skip = set(skip_fields or []) | {'__m2m'}

        for field in instance._meta.get_fields():
            name = field.name
            if name in skip or name not in data:
                continue
            if not hasattr(field, 'column'):
                continue   # skip reverse relations, GenericRelation, etc.

            raw = data[name]
            try:
                # StreamField — rewrite image/doc PKs then store as JSON string
                if isinstance(field, _SF):
                    blocks = (
                        self._res.rewrite_stream(
                            raw, getattr(field, 'stream_block', None)
                        )
                        if isinstance(raw, list) else raw
                    )
                    setattr(instance, name, json.dumps(blocks))

                # RichTextField — remap inline <embed>/<a> media references
                elif isinstance(field, _RTF):
                    setattr(instance, name, self._res.rewrite_richtext(raw))

                # ForeignKey — remap via __ref or plain PK
                elif isinstance(field, _djm.ForeignKey):
                    rm = field.related_model
                    if raw is None:
                        setattr(instance, field.attname, None)
                    elif isinstance(raw, dict) and raw.get('__ref') == 'snippet':
                        setattr(instance, field.attname,
                                self._res.resolve_snippet(raw))
                    elif rm and issubclass(rm, Im):
                        new_pk = self._res._img.get(raw.get('pk')) if isinstance(raw, dict) else None
                        setattr(instance, field.attname, new_pk)
                    elif rm and issubclass(rm, Do):
                        new_pk = self._res._doc.get(raw.get('pk')) if isinstance(raw, dict) else None
                        setattr(instance, field.attname, new_pk)
                    elif rm and issubclass(rm, Page):
                        pg = _get_page_by_slug_path(raw.get('slug_path')) if isinstance(raw, dict) else None
                        setattr(instance, field.attname, pg.pk if pg else None)
                    else:
                        setattr(instance, field.attname, raw)

                # Date / DateTime
                elif isinstance(field, _djm.DateTimeField):
                    setattr(instance, name, _parse_datetime(raw))
                elif isinstance(field, _djm.DateField):
                    setattr(instance, name, _parse_date(raw))

                # M2M — handled separately via apply_m2m()
                elif isinstance(field, _djm.ManyToManyField):
                    pass

                # Scalar (CharField, IntegerField, BooleanField, …)
                else:
                    setattr(instance, name, raw)

            except Exception as e:
                self._console.warn(
                    f'{type(instance).__name__}.{name}={raw!r}: {e}'
                )

    def apply_m2m(self, instance, data):
        """Apply M2M fields stored under data['__m2m']. Call after save()."""
        for name, ref_list in data.get('__m2m', {}).items():
            resolved = self._res.resolve_m2m(ref_list)
            try:
                getattr(instance, name).set(resolved)
            except Exception as e:
                self._console.warn(
                    f'M2M {type(instance).__name__}.{name}: {e}'
                )


# ---------------------------------------------------------------------------
# Snippet importers
# ---------------------------------------------------------------------------

class _SnippetImporter:
    def __init__(self, resolver, applicator, console: Console, stats, dry_run):
        self._res = resolver
        self._app = applicator
        self._console = console
        self._stats = stats
        self._dry = dry_run

    def _upsert(self, Model, lookup, data, extra_skip=None):
        skip = set(extra_skip or []) | set(lookup.keys())
        try:
            # NB: not get_or_create — that INSERTs with only the natural key
            # set, which trips every NOT NULL column on the model (Volume has
            # year_start). Build the row in memory, apply all fields, save once.
            obj = Model.objects.filter(**lookup).first()
            created = obj is None
            if created:
                obj = Model(**lookup)
        except Exception as e:
            self._console.warn(f'{Model.__name__} {lookup}: {e}')
            return None

        self._console.detail(f'    {"+" if created else "↻"}  {Model.__name__} {lookup}')
        if self._dry:
            self._stats['snippets_created' if created else 'snippets_updated'] += 1
            return obj if not created else None

        try:
            self._app.apply(obj, data, skip_fields=skip)
            obj.save()
            self._app.apply_m2m(obj, data)
        except Exception as e:
            self._console.warn(f'{Model.__name__} {lookup}: {e}')
            return None
        self._stats['snippets_created' if created else 'snippets_updated'] += 1
        return obj

    def import_volumes(self, rows):
        from issue.models import Volume
        bar = self._console.bar(len(rows), 'volumes')
        for row in rows:
            self._upsert(Volume, {'number': row['number']}, row)
            bar.advance(suffix=str(row.get('number', '')))
        bar.finish()

    def import_topics(self, rows):
        from issue.models import Topic
        bar = self._console.bar(len(rows), 'topics')
        for row in rows:
            self._upsert(Topic, {'slug': row['slug']}, row)
            bar.advance(suffix=str(row.get('slug', '')))
        bar.finish()

    def import_site_contact(self, rows):
        from home.models import SiteContactSettings
        for row in rows:
            if not self._dry:
                obj = SiteContactSettings.objects.first() or SiteContactSettings()
                self._app.apply(obj, row)
                obj.save()
            self._stats['snippets_updated'] += 1
            self._console.line('    ↻  SiteContactSettings', level=2)

    def import_site_header(self, rows):
        from home.models import SiteHeader
        for row in rows:
            if not self._dry:
                obj = SiteHeader.objects.first() or SiteHeader()
                self._app.apply(obj, row)
                obj.save()
                self._app.apply_m2m(obj, row)
            self._stats['snippets_updated'] += 1
            self._console.line('    ↻  SiteHeader', level=2)

    def import_site_footer(self, rows):
        from home.models import (
            SiteFooter, FooterSection, FooterColumn, FooterColumnLink
        )
        for fd in rows:
            sections_data = fd.pop('_sections', [])
            if self._dry:
                self._console.line(
                    f'    ~  SiteFooter ({len(sections_data)} section(s)) — dry-run',
                    level=2,
                )
                continue

            footer = SiteFooter.objects.first() or SiteFooter()
            self._app.apply(footer, fd, skip_fields=['id'])
            footer.save()
            self._stats['snippets_updated'] += 1
            self._console.line('    ↻  SiteFooter', level=2)

            # Wipe existing hierarchy so the import is repeatable
            footer.sections.all().delete()

            for sd in sections_data:
                cols_data = sd.pop('_columns', [])
                section = FooterSection(footer=footer, heading=sd.get('heading', ''))
                self._app.apply(
                    section, sd,
                    skip_fields=['id', 'footer', 'footer_id', 'heading']
                )
                section.save()

                for cd in cols_data:
                    links_data = cd.pop('_links', [])
                    column = FooterColumn(section=section, label=cd.get('label', ''))
                    self._app.apply(
                        column, cd,
                        skip_fields=['id', 'section', 'section_id', 'label']
                    )
                    column.save()

                    for ld in links_data:
                        link = FooterColumnLink(column=column)
                        self._app.apply(
                            link, ld,
                            skip_fields=['id', 'column', 'column_id']
                        )
                        link.save()

    def import_board_shells(self, rows):
        """
        Create the EditorialBoard rows only, before pages are imported.

        Issue pages carry an editorial_board FK, so the boards have to exist
        first; their *members* point at Literati pages, so those can only be
        attached afterwards. Splitting the two breaks the circular dependency.
        """
        from literati.models import EditorialBoard
        for bd in rows:
            name = bd.get('name')
            if not name:
                continue
            payload = {k: v for k, v in bd.items() if k != '_members'}
            self._upsert(EditorialBoard, {'name': name}, payload)

    def import_editorial_boards(self, rows):
        from literati.models import EditorialBoard, EditorialBoardMember
        bar = self._console.bar(len(rows), 'boards')
        for bd in rows:
            try:
                members_data = bd.pop('_members', [])
                board = self._upsert(EditorialBoard, {'name': bd['name']}, bd)
                if board is None or self._dry:
                    continue

                for md in members_data:
                    editor_ref = md.get('editor') or md.get('editor_id')
                    if isinstance(editor_ref, dict) and editor_ref.get('__ref') == 'page':
                        pg = _get_page_by_slug_path(editor_ref.get('slug_path'))
                        editor_pk = pg.pk if pg else None
                    else:
                        editor_pk = editor_ref

                    if editor_pk is None:
                        self._console.warn(
                            f'EditorialBoardMember: editor not found {editor_ref!r}'
                        )
                        continue

                    member, created = EditorialBoardMember.objects.get_or_create(
                        board=board, editor_id=editor_pk
                    )
                    self._app.apply(
                        member, md,
                        skip_fields=[
                            'id', 'board', 'board_id', 'editor', 'editor_id'
                        ]
                    )
                    member.save()
                    self._console.detail(
                        f'    {"+" if created else "↻"}  '
                        f'EditorialBoardMember editor_id={editor_pk}'
                    )
            finally:
                bar.advance(suffix=str(bd.get('name', '')))
        bar.finish()

    def import_membership_history(self, rows):
        from literati.models import (
            EditorialBoardMembershipHistory, EditorialBoard
        )
        bar = self._console.bar(len(rows), 'history')
        for hd in rows:
            try:
                board_ref  = hd.get('board') or hd.get('board_id')
                editor_ref = hd.get('editor') or hd.get('editor_id')

                # Resolve board (exported as its natural key — the name)
                if isinstance(board_ref, str):
                    board = EditorialBoard.objects.filter(name=board_ref).first()
                elif isinstance(board_ref, int):
                    board = EditorialBoard.objects.filter(pk=board_ref).first()
                else:
                    board = None

                # Resolve editor (stored as page __ref)
                if isinstance(editor_ref, dict) and editor_ref.get('__ref') == 'page':
                    pg = _get_page_by_slug_path(editor_ref.get('slug_path'))
                    editor_pk = pg.pk if pg else None
                else:
                    editor_pk = editor_ref

                joined_at = _parse_date(hd.get('joined_at'))
                if not board or not editor_pk or not joined_at:
                    missing = ', '.join(
                        label for label, ok in (
                            ('board', board), ('editor', editor_pk),
                            ('joined_at', joined_at),
                        ) if not ok
                    )
                    self._console.warn(
                        f'MembershipHistory: unresolved {missing} — skipped'
                    )
                    continue

                if self._dry:
                    self._console.detail(
                        f'    ~  MembershipHistory editor_id={editor_pk} — dry-run'
                    )
                    continue

                _, created = EditorialBoardMembershipHistory.objects.get_or_create(
                    board=board,
                    editor_id=editor_pk,
                    joined_at=joined_at,
                    defaults={
                        'role':    hd.get('role', 'board'),
                        'left_at': _parse_date(hd.get('left_at')),
                    },
                )
                self._stats['snippets_created' if created else 'snippets_updated'] += 1
                self._console.detail(
                    f'    {"+" if created else "↩"}  '
                    f'MembershipHistory editor_id={editor_pk}'
                )
            finally:
                bar.advance()
        bar.finish()


# ---------------------------------------------------------------------------
# Page importer
# ---------------------------------------------------------------------------

class _PageImporter:
    # Fields on wagtailcore_page that must never be set on the child table
    # Mirrors _PAGE_BASE_EXCLUDE in export_content: owner / locked_by are FKs
    # into the user table and latest_revision / live_revision point at Revision
    # rows, so copying those source PKs here would raise IntegrityError.
    # url_path is recomputed by Wagtail from the tree.
    _BASE_SKIP = frozenset({
        'id', 'path', 'depth', 'numchild',
        'content_type', 'content_type_id',
        'locale', 'locale_id',
        'translation_key', 'alias_of', 'alias_of_id',
        'owner', 'owner_id',
        'locked_by', 'locked_by_id',
        'latest_revision', 'latest_revision_id',
        'live_revision', 'live_revision_id',
        'url_path',
    })

    def __init__(self, resolver, applicator, console: Console, stats, dry_run,
                 strict=False):
        self._res = resolver
        self._app = applicator
        self._console = console
        self._stats = stats
        self._dry = dry_run
        self._strict = strict

    def import_pages(self, page_rows):
        locale = (
            Locale.objects.filter(language_code='en').first()
            or Locale.objects.first()
        )
        bar = self._console.bar(len(page_rows), 'pages')

        for pr in page_rows:
            try:
                model_label      = pr['model']
                slug_path        = pr['slug_path']
                parent_slug_path = pr.get('parent_slug_path')
                base_fields      = pr.get('base_fields', {})
                specific_fields  = pr.get('specific_fields', {})

                # ── Resolve model class ──────────────────────────────────
                try:
                    app_label, model_name = model_label.split('.')
                    PageModel = django_apps.get_model(app_label, model_name)
                except (LookupError, ValueError) as e:
                    self._console.warn(f'Cannot resolve {model_label}: {e} — skipped')
                    self._stats['pages_skipped'] += 1
                    continue

                # ── Find parent page ─────────────────────────────────────
                if parent_slug_path:
                    parent = _get_page_by_slug_path(parent_slug_path)
                    if parent is None:
                        self._console.warn(
                            f'Parent "{parent_slug_path}" not found '
                            f'for {slug_path} — skipped'
                        )
                        self._stats['pages_skipped'] += 1
                        continue
                else:
                    parent = Page.get_first_root_node()

                existing = _get_page_by_slug_path(slug_path)
                self._console.detail(
                    f'  {"↻" if existing else "+"}  {model_label} [{slug_path}]'
                )
                if self._dry:
                    self._stats['pages_updated' if existing else 'pages_created'] += 1
                    continue

                try:
                    if existing:
                        specific = existing.specific
                        self._app.apply(
                            specific, base_fields, skip_fields=self._BASE_SKIP
                        )
                        self._app.apply(
                            specific, specific_fields, skip_fields={'id'}
                        )
                        self._save_page(specific)
                        self._app.apply_m2m(specific, base_fields)
                        self._app.apply_m2m(specific, specific_fields)
                        target = specific
                        self._stats['pages_updated'] += 1
                    else:
                        page = PageModel(locale=locale)
                        self._app.apply(
                            page, base_fields, skip_fields=self._BASE_SKIP
                        )
                        self._app.apply(
                            page, specific_fields, skip_fields={'id'}
                        )
                        if not self._strict:
                            # treebeard's add_child() calls save() with no
                            # kwargs, so Page.full_clean() runs and rejects rows
                            # the source database already holds (e.g. a blank
                            # required phone_number). Shadow it per-instance.
                            page.full_clean = lambda *a, **kw: None
                        parent.add_child(instance=page)
                        self._app.apply_m2m(page, base_fields)
                        self._app.apply_m2m(page, specific_fields)
                        target = page
                        self._stats['pages_created'] += 1
                except Exception as e:
                    self._console.warn(f'Failed to import {slug_path}: {e}')
                    self._stats['pages_skipped'] += 1
                    continue

                if base_fields.get('live', True):
                    self._publish(target)
            finally:
                bar.advance(suffix=pr.get('slug', ''))
        bar.finish()

    @staticmethod
    def _has_deferred_ref(value):
        """True if a serialised value points at a page or natural-key snippet."""
        if isinstance(value, dict):
            return value.get('__ref') in ('page', 'snippet')
        if isinstance(value, list):
            return any(
                isinstance(v, dict) and v.get('__ref') in ('page', 'snippet')
                for v in value
            )
        return False

    def resolve_deferred(self, page_rows):
        """
        Second pass: re-apply page/snippet references now that every page and
        snippet exists.

        Pages are created in tree order, so a page whose FK points *forward* —
        an Article referencing an Issue that sorts later — resolved to None on
        the first pass. This re-applies only those fields, once.
        """
        pending = []
        for pr in page_rows:
            fields = {}
            for source in (pr.get('base_fields', {}), pr.get('specific_fields', {})):
                for name, value in source.items():
                    if name != '__m2m' and self._has_deferred_ref(value):
                        fields[name] = value
                m2m = {
                    k: v for k, v in (source.get('__m2m') or {}).items()
                    if self._has_deferred_ref(v)
                }
                if m2m:
                    fields.setdefault('__m2m', {}).update(m2m)
            if fields:
                pending.append((pr['slug_path'], fields))

        if not pending:
            return
        bar = self._console.bar(len(pending), 'relink')
        for slug_path, fields in pending:
            try:
                page = _get_page_by_slug_path(slug_path)
                if page is None:
                    continue
                specific = page.specific
                self._app.apply(specific, fields, skip_fields=self._BASE_SKIP - {'__m2m'})
                self._save_page(specific)
                self._app.apply_m2m(specific, fields)
                self._stats['pages_relinked'] += 1
            except Exception as e:
                self._console.warn(f'relink {slug_path}: {e}')
            finally:
                bar.advance(suffix=slug_path)
        bar.finish()

    def _save_page(self, page):
        """Save a page, bypassing full_clean() unless --strict-validation."""
        if self._strict:
            page.save()
            return
        try:
            page.save(clean=False)
        except TypeError:      # not a Wagtail Page (no clean kwarg)
            page.save()

    def _publish(self, page):
        try:
            rev = page.get_latest_revision()
            if rev:
                rev.publish()
            else:
                page.save_revision().publish()
        except Exception as e:
            # non-fatal — the page is already in the tree, just not re-published
            self._console.detail(f'      (publish skipped for {page}: {e})')


# ---------------------------------------------------------------------------
# Site record helper
# ---------------------------------------------------------------------------

def _import_site(site_data, console: Console):
    if not site_data:
        return
    site = Site.objects.filter(is_default_site=True).first()
    if site:
        site.hostname  = site_data.get('hostname',  site.hostname)
        site.port      = site_data.get('port',      site.port)
        site.site_name = site_data.get('site_name', site.site_name)
        site.save()
        console.line(f'  ↻  Site "{site.hostname}:{site.port}"')
    else:
        console.warn('no default Site record on this database — site data ignored')


# ---------------------------------------------------------------------------
# Management command
# ---------------------------------------------------------------------------

class Command(BaseCommand):
    help = 'Import Icarus pages and snippets from a ZIP produced by export_content'

    def add_arguments(self, parser):
        parser.add_argument('archive', help='Path to the ZIP archive')
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Show what would be imported without writing anything',
        )
        parser.add_argument(
            '--tolerant', action='store_true',
            help='Skip individual errors and continue rather than aborting',
        )
        parser.add_argument(
            '--skip-existing-media', action='store_true',
            help='Reuse media that already exists by title (this is the default)',
        )
        parser.add_argument(
            '--replace-existing-media', action='store_true',
            help='Create new media objects even when a title already matches',
        )
        parser.add_argument(
            '--yes', '-y', action='store_true',
            help='Skip the confirmation prompt',
        )
        parser.add_argument(
            '--strict-validation', action='store_true',
            help='Run model validation on imported pages. Off by default: an '
                 'archive restores rows the source database already accepted, '
                 'and validation added since then would reject them.',
        )
        parser.add_argument(
            '--no-progress', action='store_true',
            help='Disable progress bars (they are auto-disabled when piped)',
        )

    # ── pre-flight ───────────────────────────────────────────────────────

    def _check_compatibility(self, manifest, console):
        version = manifest.get('version')
        if version != '1.0':
            console.warn(
                f'archive format version "{version}" != "1.0" — import may misbehave'
            )
        archive_wagtail = str(manifest.get('wagtail_version') or '')
        running = '.'.join(str(v) for v in wagtail.VERSION[:3])
        if archive_wagtail and archive_wagtail.split('.')[0] != running.split('.')[0]:
            console.warn(
                f'archive built on Wagtail {archive_wagtail}, running {running} — '
                f'StreamField formats may differ'
            )

    def _confirm(self, manifest, archive_path, options):
        if options['dry_run'] or options['yes']:
            return
        if not sys.stdin or not sys.stdin.isatty():
            return   # non-interactive (CI, cron): proceed without prompting
        self.stdout.write(self.style.WARNING(
            f'\nAbout to import "{archive_path}" into this database.\n'
            f'  pages     : {len(manifest.get("pages", []))}\n'
            f'  images    : {len(manifest.get("images", []))}\n'
            f'  documents : {len(manifest.get("documents", []))}\n'
            'Existing pages/snippets matched by key will be UPDATED in place, '
            'and the site footer hierarchy will be rebuilt.\n'
        ))
        answer = input('Type "yes" to continue (or re-run with --dry-run): ')
        if answer.strip().lower() not in ('y', 'yes'):
            raise CommandError('Aborted — nothing was written.')

    def handle(self, *args, **options):
        started = time.monotonic()
        archive_path = options['archive']
        dry_run      = options['dry_run']
        tolerant     = options['tolerant']
        console = Console(
            self.stdout, self.stderr, self.style,
            verbosity=options.get('verbosity', 1),
            progress=not options['no_progress'],
        )
        stats = Counter()

        if not os.path.exists(archive_path):
            raise CommandError(f'Archive not found: {archive_path}')

        if dry_run:
            self.stdout.write(
                self.style.WARNING('DRY RUN — no changes will be written\n')
            )

        with zipfile.ZipFile(archive_path, 'r') as zf:
            console.heading('Reading manifest…')
            try:
                manifest = json.loads(zf.read('manifest.json'))
            except KeyError:
                raise CommandError('ZIP does not contain manifest.json')
            except json.JSONDecodeError as e:
                raise CommandError(f'manifest.json is not valid JSON: {e}')

            console.line(
                f'  version={manifest.get("version")}  '
                f'scope={manifest.get("scope")}  '
                f'created={str(manifest.get("created_at", "?"))[:19]}'
            )
            self._check_compatibility(manifest, console)
            self._confirm(manifest, archive_path, options)

            def _step(label, fn):
                console.heading(label)
                if tolerant:
                    try:
                        fn()
                    except Exception as e:
                        console.error(f'{label}: {e}')
                else:
                    fn()

            with transaction.atomic():

                # 1 — media
                mi = _MediaImporter(
                    zf, console, stats, dry_run=dry_run,
                    replace_existing=options['replace_existing_media'],
                )
                _step('Importing images…',
                      lambda: mi.import_images(manifest.get('images', [])))
                _step('Importing documents…',
                      lambda: mi.import_documents(manifest.get('documents', [])))

                # 2 — build resolver / applicator with the completed remap tables
                resolver   = _Resolver(mi.image_map, mi.doc_map, console)
                applicator = _Applicator(resolver, console)
                si = _SnippetImporter(
                    resolver, applicator, console, stats, dry_run
                )
                pi = _PageImporter(
                    resolver, applicator, console, stats, dry_run,
                    strict=options['strict_validation'],
                )
                snips = manifest.get('snippets', {})

                # 3 — site record
                if not dry_run:
                    _step('Importing site record…',
                          lambda: _import_site(manifest.get('site', {}), console))

                # 4 — snippets with no inter-page FK dependencies
                _step('Importing volumes…',
                      lambda: si.import_volumes(snips.get('issue.volume', [])))
                _step('Importing topics…',
                      lambda: si.import_topics(snips.get('issue.topic', [])))
                _step('Importing site contact settings…',
                      lambda: si.import_site_contact(
                          snips.get('home.sitecontactsettings', [])))
                _step('Importing site header…',
                      lambda: si.import_site_header(
                          snips.get('home.siteheader', [])))
                _step('Importing site footer…',
                      lambda: si.import_site_footer(
                          snips.get('home.sitefooter', [])))

                # 5a — board rows before pages: Issue pages carry an
                #      editorial_board FK. Members are attached in step 6,
                #      once the Literati pages they point at exist.
                _step('Importing editorial boards…',
                      lambda: si.import_board_shells(
                          snips.get('literati.editorialboard', [])))

                # 5b — pages (tree order — parents before children)
                _step('Importing pages…',
                      lambda: pi.import_pages(manifest.get('pages', [])))

                # 5c — re-resolve forward references (an Article pointing at an
                #      Issue that sorts later in the tree)
                _step('Relinking page references…',
                      lambda: pi.resolve_deferred(manifest.get('pages', [])))

                # 6 — board members and history (depend on Literati pages)
                _step('Attaching board members…',
                      lambda: si.import_editorial_boards(
                          snips.get('literati.editorialboard', [])))
                _step('Importing membership history…',
                      lambda: si.import_membership_history(
                          snips.get('literati.editorialboardmembershiphistory', [])))

                if dry_run:
                    transaction.set_rollback(True)

        # ── Summary ──────────────────────────────────────────────────────
        elapsed = human_duration(time.monotonic() - started)
        verb = 'Would import' if dry_run else 'Imported'
        console.success(
            f'\n✓  {verb} from {archive_path}  ({elapsed})\n'
            f'   images    : {stats["images_created"]} created · '
            f'{stats["images_reused"]} reused\n'
            f'   documents : {stats["documents_created"]} created · '
            f'{stats["documents_reused"]} reused\n'
            f'   pages     : {stats["pages_created"]} created · '
            f'{stats["pages_updated"]} updated · {stats["pages_skipped"]} skipped · '
            f'{stats["pages_relinked"]} relinked\n'
            f'   snippets  : {stats["snippets_created"]} created · '
            f'{stats["snippets_updated"]} updated'
        )
        if console.warnings or console.errors:
            hint = '' if console.verbosity >= 2 else ' — re-run with -v 2 for detail'
            self.stdout.write(self.style.WARNING(
                f'   {console.warnings} warning(s), {console.errors} error(s){hint}'
            ))
