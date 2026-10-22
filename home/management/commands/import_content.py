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

    # Keep what imports cleanly, skip (and list) what fails
    python manage.py import_content icarus_content.zip --tolerant

    # Skip the "this will modify your database" confirmation; required when
    # not run from a terminal (cron, CI, docker exec without -t)
    python manage.py import_content icarus_content.zip --yes

    # Force new media objects even when the same file already exists
    python manage.py import_content icarus_content.zip --replace-existing-media

    # Output control
    python manage.py import_content icarus_content.zip -v 2   # per-record lines
    python manage.py import_content icarus_content.zip --no-progress

Idempotency
-----------
• Images and documents are matched by file content (Wagtail's file_hash),
  never by title, and reused rather than created twice.
• Snippets (Volume, Topic, EditorialBoard …) are upserted by their natural
  key (number / slug / name).
• Pages are looked up by slug-path. A page of the same type is updated in
  place; a page of a different type at that path is an error (except
  Wagtail's default "Welcome" page on a fresh database, which is replaced).
  Every imported page gets a new revision built from the archive, published
  if the source page was live.

References
----------
FKs, StreamField chooser blocks and rich-text embeds/links are all remapped
onto the destination database: media through the remap tables built while
importing it, pages through their slug paths and snippets through their
natural keys (the archive's page_paths / snippet_keys tables, format 1.1).
A reference that can't be mapped is left empty — never the source id,
which here belongs to some unrelated row. User accounts are never set from
an archive.

Failures and exit codes
-----------------------
Every row is written in its own savepoint. Without --tolerant, any failure
rolls the whole import back (and deletes the media files it wrote) and the
command exits non-zero. With --tolerant, everything that imported cleanly is
committed, the failures are listed, and the command still exits non-zero.

Publishing an imported page triggers no text-to-speech and no newsletter.

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
from contextlib import contextmanager
from datetime import date, datetime

import wagtail
from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from taggit.managers import TaggableManager
from wagtail.images import get_image_model
from wagtail.documents import get_document_model
from wagtail.models import Page, Site, Locale

from ._console import Console, human_duration
from ._richtext import rewrite_richtext_refs
from ._streamwalk import RefMapper, map_stream
from .export_content import SNIPPET_NATURAL_KEYS

#: Archive formats this importer understands.
SUPPORTED_VERSIONS = ('1.0', '1.1')


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

class _Remapper(RefMapper):
    """Import-side mapper for _streamwalk: source ids → destination ids."""

    def __init__(self, resolver):
        self._r = resolver

    def image(self, pk):
        return self._r.image_pk(pk)

    def document(self, pk):
        return self._r.document_pk(pk)

    def page(self, pk):
        return self._r.page_pk(pk)

    def snippet(self, label, pk):
        return self._r.snippet_pk(label, pk)

    def richtext(self, html):
        return self._r.rewrite_richtext(html)


class _Resolver:
    """
    Maps every reference in the archive onto the destination database.

    A reference that can't be mapped becomes empty — never the source PK,
    which on another database belongs to some unrelated row. (Archives in
    format 1.0 have no page/snippet tables; their page and snippet chooser
    ids are left as they were, which is only right on the database they
    came from.)
    """

    def __init__(self, image_map, doc_map, console: Console, page_paths=None, snippet_keys=None):
        self._img = image_map   # {original_pk -> new_pk}
        self._doc = doc_map
        self._console = console
        self._page_paths = page_paths      # {str(source pk): slug path} or None (1.0)
        self._snippet_keys = snippet_keys  # {label: {str(source pk): key}} or None (1.0)

    # ── Single ids ───────────────────────────────────────────────────────

    def image_pk(self, pk):
        return self._img.get(pk)

    def document_pk(self, pk):
        return self._doc.get(pk)

    def page_pk(self, pk):
        if self._page_paths is None:
            return pk
        page = _get_page_by_slug_path(self._page_paths.get(str(pk)))
        return page.pk if page else None

    def snippet_pk(self, label, pk):
        if self._snippet_keys is None:
            return pk
        key = (self._snippet_keys.get(label) or {}).get(str(pk))
        if key is None:
            return None
        return self.resolve_snippet({'model': label, 'key': key})

    # ── Snippet natural keys ─────────────────────────────────────────────

    def resolve_snippet(self, ref):
        """
        Resolve {"__ref": "snippet", "model": "issue.volume", "key": {...}}
        to the destination PK. Snippets are upserted by natural key, so their
        PKs differ from the source database.

        Only the snippet models export_content writes, looked up by exactly
        their natural-key fields: the archive is input, and an arbitrary
        model label or filter from it must not reach the ORM.
        """
        label = ref.get('model', '')
        key = ref.get('key') or {}
        allowed = SNIPPET_NATURAL_KEYS.get(label)
        if allowed is None or not isinstance(key, dict) or set(key) != set(allowed):
            self._console.warn(f'unsupported snippet reference {label!r} {key!r} — left empty')
            return None
        Model = django_apps.get_model(label)
        obj = Model.objects.filter(**key).first()
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
                # A bare PK has no meaning on another database.
                self._console.warn(f'M2M value {item!r} has no portable reference — skipped')
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
        return out

    # ── Rich text ─────────────────────────────────────────────────────────

    def rewrite_richtext(self, html):
        """Remap <embed embedtype="image">, <a linktype="document|page"> ids."""
        return rewrite_richtext_refs(
            html, self.image_pk, self.document_pk,
            page_map=self.page_pk if self._page_paths is not None else None,
            drop_unmapped=True,
        )

    # ── StreamField blocks ────────────────────────────────────────────────

    def rewrite_stream(self, blocks, stream_block=None):
        """Remap every reference inside a StreamField value (see _streamwalk)."""
        return map_stream(
            stream_block, blocks, _Remapper(self),
            on_error=lambda e: self._console.warn(f'block-definition rewrite failed: {e}'),
        )


# ---------------------------------------------------------------------------
# Media importer
# ---------------------------------------------------------------------------

class _MediaImporter:
    """
    Images and documents from the archive.

    Matched to existing media by content (file_hash), never by title — two
    different images called "cover" must not collapse into one. Files go
    through the same checks as an upload in the Wagtail admin (allowed
    extensions, size limit, a real decodable image), because an archive is
    just a ZIP anyone can build.
    """

    def __init__(self, zf, console: Console, stats, dry_run=False,
                 replace_existing=False, failures=None):
        self._zf = zf
        self._console = console
        self._stats = stats
        self._dry = dry_run
        self._replace = replace_existing
        self._failures = failures if failures is not None else []
        self.image_map = {}   # {original_pk -> new_pk}
        self.doc_map   = {}
        #: files written to storage, so they can be removed if the database
        #: work they belong to is rolled back
        self.written_files = []

    def discard_written_files(self, since=0):
        """Delete the files this run wrote from `since` on (their rows were rolled back)."""
        for field_file in self.written_files[since:]:
            try:
                field_file.storage.delete(field_file.name)
            except Exception:
                pass
        del self.written_files[since:]

    def _member(self, entry, kind):
        """The entry's file inside the ZIP, or None (with a warning)."""
        title = entry.get('title') or f'{kind}_{entry["pk"]}'
        file_path = entry.get('file')
        if not file_path:
            self._console.warn(f'{kind} "{title}": no file path — skipped')
            return None
        try:
            return self._zf.getinfo(file_path)
        except KeyError:
            self._console.warn(f'{kind} "{title}": not in ZIP — skipped')
            return None

    def _hash(self, info):
        from wagtail.utils.file import hash_filelike

        with self._zf.open(info) as f:
            return hash_filelike(f)

    def _reuse(self, Model, file_hash, size=None):
        """An existing row holding the same file, or None."""
        from django.db.models import Q

        if self._replace or not file_hash:
            return None
        match = Model.objects.filter(file_hash=file_hash).first()
        if match or size is None:
            return match
        # Rows saved outside the admin (scripts, generated TTS audio) may
        # never have had their hash stored: hash the likely ones now.
        candidates = Model.objects.filter(file_hash='').filter(
            Q(file_size=size) | Q(file_size__isnull=True)
        )[:50]
        for candidate in candidates:
            try:
                if candidate.get_file_hash() == file_hash:
                    return candidate
            except Exception:
                continue  # file missing from storage
        return None

    def _record_failure(self, kind, entry, e):
        title = entry.get('title') or entry.get('pk')
        self._console.error(f'{kind} "{title}": {e}')
        self._failures.append(f'{kind} {title}')

    # ── images ───────────────────────────────────────────────────────────

    def _validated_image(self, info, raw):
        """Return (width, height) or raise ValueError, like Wagtail's upload form."""
        from django.conf import settings
        from PIL import Image as PILImage
        from wagtail.images.utils import get_allowed_image_extensions

        ext = os.path.splitext(info.filename)[1].lower().lstrip('.')
        if ext not in get_allowed_image_extensions():
            raise ValueError(f'".{ext}" is not an allowed image type')
        limit = getattr(settings, 'WAGTAILIMAGES_MAX_UPLOAD_SIZE', 10 * 1024 * 1024)
        if limit and len(raw) > limit:
            raise ValueError(f'{len(raw)} bytes is over the {limit}-byte upload limit')
        try:
            with PILImage.open(io.BytesIO(raw)) as probe:
                probe.verify()
            with PILImage.open(io.BytesIO(raw)) as probe:
                return probe.size  # from the file itself, not the manifest
        except Exception as e:
            raise ValueError(f'not a readable image ({e})')

    def import_images(self, entries):
        from django.conf import settings
        from wagtail.utils.file import hash_filelike

        Im = get_image_model()
        bar = self._console.bar(len(entries), 'images')
        for entry in entries:
            try:
                orig_pk = entry['pk']
                title = entry.get('title') or f'image_{orig_pk}'
                info = self._member(entry, 'image')
                limit = getattr(settings, 'WAGTAILIMAGES_MAX_UPLOAD_SIZE', 10 * 1024 * 1024)
                if info and limit and info.file_size > limit:
                    # Checked before reading: the file is held in memory whole.
                    self._stats['media_rejected'] += 1
                    self._record_failure(
                        'image', entry,
                        f'{info.file_size} bytes is over the {limit}-byte upload limit — rejected',
                    )
                    continue
                raw = self._zf.read(info) if info else None
                file_hash = hash_filelike(io.BytesIO(raw)) if raw is not None else entry.get('file_hash')

                existing = self._reuse(Im, file_hash, len(raw) if raw is not None else None)
                if existing:
                    self._console.detail(f'    ↩  image "{title}" — same file as pk={existing.pk}')
                    self.image_map[orig_pk] = existing.pk
                    self._stats['images_reused'] += 1
                    continue
                if raw is None:
                    continue

                try:
                    width, height = self._validated_image(info, raw)
                except ValueError as e:
                    self._stats['media_rejected'] += 1
                    self._record_failure('image', entry, f'{e} — rejected')
                    continue

                if self._dry:
                    self._console.detail(f'    +  image "{title}" — would create')
                    self._stats['images_created'] += 1
                    continue

                img = Im(title=title)
                for attr in ('focal_point_x', 'focal_point_y',
                             'focal_point_width', 'focal_point_height'):
                    if entry.get(attr) is not None:
                        setattr(img, attr, entry[attr])
                try:
                    with transaction.atomic():
                        # Write the file first with save=False, then set the
                        # dimensions, then save once: assigning the file can
                        # reset width/height via Django's dimension-field hook.
                        img.file.save(os.path.basename(info.filename), ContentFile(raw), save=False)
                        img.width, img.height = width, height
                        img.file_size = len(raw)
                        img.file_hash = file_hash
                        img.save()
                except Exception:
                    if img.file:
                        img.file.delete(save=False)
                    raise
                self.written_files.append(img.file)

                self.image_map[orig_pk] = img.pk
                self._stats['images_created'] += 1
                self._console.detail(f'    +  image "{title}" → pk={img.pk}')
            except Exception as e:
                self._record_failure('image', entry, e)
            finally:
                bar.advance(suffix=entry.get('title', ''))
        bar.finish()

    # ── documents ────────────────────────────────────────────────────────

    def import_documents(self, entries):
        from django.conf import settings
        from django.core.files import File
        from django.db import transaction

        Do = get_document_model()
        allowed = getattr(settings, 'WAGTAILDOCS_EXTENSIONS', None)
        bar = self._console.bar(len(entries), 'documents')
        for entry in entries:
            try:
                orig_pk = entry['pk']
                title = entry.get('title') or f'document_{orig_pk}'
                info = self._member(entry, 'document')
                file_hash = self._hash(info) if info else entry.get('file_hash')

                existing = self._reuse(Do, file_hash, info.file_size if info else None)
                if existing:
                    self._console.detail(f'    ↩  document "{title}" — same file as pk={existing.pk}')
                    self.doc_map[orig_pk] = existing.pk
                    self._stats['documents_reused'] += 1
                    continue
                if info is None:
                    continue

                ext = os.path.splitext(info.filename)[1].lower().lstrip('.')
                if allowed and ext not in allowed:
                    self._stats['media_rejected'] += 1
                    self._record_failure(
                        'document', entry, f'".{ext}" is not an allowed document type — rejected'
                    )
                    continue

                if self._dry:
                    self._console.detail(f'    +  document "{title}" — would create')
                    self._stats['documents_created'] += 1
                    continue

                doc = Do(title=title)
                try:
                    with transaction.atomic(), self._zf.open(info) as source:
                        # Streamed from the ZIP: an audio or video file is
                        # never held in memory whole.
                        doc.file.save(os.path.basename(info.filename), File(source), save=False)
                        doc.file_size = info.file_size
                        doc.file_hash = file_hash
                        doc.save()
                except Exception:
                    if doc.file:
                        doc.file.delete(save=False)
                    raise
                self.written_files.append(doc.file)
                self.doc_map[orig_pk] = doc.pk
                self._stats['documents_created'] += 1
                self._console.detail(f'    +  document "{title}" → pk={doc.pk}')
            except Exception as e:
                self._record_failure('document', entry, e)
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
            if not hasattr(field, 'column') or isinstance(field, TaggableManager):
                # reverse relations, GenericRelation …; tags are restored by
                # name from the page row's 'tags' (1.0 archives held a junk
                # "app.Model.None" string here)
                continue

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

                # ForeignKey — remap via __ref; never trust a bare PK
                elif isinstance(field, _djm.ForeignKey):
                    rm = field.related_model
                    if rm is get_user_model():
                        # Never from an archive (older ones carried the raw
                        # PK of e.g. Literati.reader_user, which attached the
                        # page — and its phone/email sync — to whichever
                        # account had that PK here). Leave it as it is;
                        # Literati.save() links the author's account by phone.
                        continue
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
                        self._console.warn(
                            f'{type(instance).__name__}.{name}: bare foreign key {raw!r} has no '
                            f'portable reference — left empty'
                        )
                        setattr(instance, field.attname, None)

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
    def __init__(self, resolver, applicator, console: Console, stats, dry_run, failures=None):
        self._res = resolver
        self._app = applicator
        self._console = console
        self._stats = stats
        self._dry = dry_run
        self._failures = failures if failures is not None else []

    def _fail(self, label, e):
        self._console.error(f'{label}: {e}')
        self._failures.append(label)

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
            # A savepoint per row: a database error rolls back this row only,
            # instead of poisoning the transaction for everything after it.
            with transaction.atomic():
                self._app.apply(obj, data, skip_fields=skip)
                obj.save()
                self._app.apply_m2m(obj, data)
        except Exception as e:
            self._fail(f'{Model.__name__} {lookup}', e)
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
                try:
                    with transaction.atomic():
                        obj = SiteContactSettings.objects.first() or SiteContactSettings()
                        self._app.apply(obj, row)
                        obj.save()
                except Exception as e:
                    self._fail('SiteContactSettings', e)
                    continue
            self._stats['snippets_updated'] += 1
            self._console.line('    ↻  SiteContactSettings', level=2)

    def import_site_header(self, rows):
        from home.models import SiteHeader
        for row in rows:
            if not self._dry:
                try:
                    with transaction.atomic():
                        obj = SiteHeader.objects.first() or SiteHeader()
                        self._app.apply(obj, row)
                        obj.save()
                        self._app.apply_m2m(obj, row)
                except Exception as e:
                    self._fail('SiteHeader', e)
                    continue
            self._stats['snippets_updated'] += 1
            self._console.line('    ↻  SiteHeader', level=2)

    def import_site_footer(self, rows):
        for fd in rows:
            sections_data = fd.pop('_sections', [])
            if self._dry:
                self._console.line(
                    f'    ~  SiteFooter ({len(sections_data)} section(s)) — dry-run',
                    level=2,
                )
                continue

            try:
                with transaction.atomic():
                    self._rebuild_footer(fd, sections_data)
            except Exception as e:
                self._fail('SiteFooter', e)

    def _rebuild_footer(self, fd, sections_data):
        from home.models import SiteFooter, FooterSection, FooterColumn, FooterColumnLink

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

                    try:
                        with transaction.atomic():
                            # Built in memory and saved once, with the archive's
                            # joined_at: a member's first save writes a history
                            # row, and get_or_create() made that row "joined
                            # today" — next to the real ones imported below.
                            member = EditorialBoardMember.objects.filter(
                                board=board, editor_id=editor_pk
                            ).first()
                            created = member is None
                            if created:
                                member = EditorialBoardMember(board=board, editor_id=editor_pk)
                            self._app.apply(
                                member, md,
                                skip_fields=[
                                    'id', 'board', 'board_id', 'editor', 'editor_id'
                                ]
                            )
                            member.save()
                    except Exception as e:
                        self._fail(f'EditorialBoardMember editor_id={editor_pk}', e)
                        continue
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

                try:
                    with transaction.atomic():
                        # update_or_create: the stint may already exist — written
                        # when the member was attached above — and the archive's
                        # role and end date are the record.
                        _, created = EditorialBoardMembershipHistory.objects.update_or_create(
                            board=board,
                            editor_id=editor_pk,
                            joined_at=joined_at,
                            defaults={
                                'role':    hd.get('role', 'board'),
                                'left_at': _parse_date(hd.get('left_at')),
                            },
                        )
                except Exception as e:
                    self._fail(f'MembershipHistory editor_id={editor_pk}', e)
                    continue
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
    """
    Pages in two passes.

    1. import_pages(): every page, in tree order (parents first), with its
       own fields. References to pages that sort later can't resolve yet.
    2. finalize_pages(): with every page in place, re-apply all references
       (FKs, StreamField and rich-text ids), restore child rows (article
       authors, reprinted articles) and tags, then publish a NEW revision
       built from the imported content.
    """

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
                 strict=False, failures=None):
        self._res = resolver
        self._app = applicator
        self._console = console
        self._stats = stats
        self._dry = dry_run
        self._strict = strict
        self._failures = failures if failures is not None else []
        self._imported = []   # page rows that made it in pass 1
        self._repoint_sites = {}  # slug path -> Site pks parked by _existing_page
        self._planned = set()     # dry run: slug paths that would be created

    def _fail(self, label, e):
        self._console.error(f'{label}: {e}')
        self._failures.append(label)
        self._stats['pages_failed'] += 1

    # ── pass 1 ───────────────────────────────────────────────────────────

    @staticmethod
    def _matching_page(slug_path, PageModel):
        """
        (page already at `slug_path` or None, whether it is a placeholder to replace).

        A fresh database has Wagtail's default "Welcome" page at slug "home"
        — a plain Page. Applying a HomePage row onto it silently dropped
        every HomePage field, so that one case is replaced; any other type
        mismatch is an error rather than a silently half-imported page.
        """
        existing = _get_page_by_slug_path(slug_path)
        if existing is None or existing.specific_class is PageModel:
            return existing, False
        is_wagtail_placeholder = (
            existing.specific_class is Page and existing.depth == 2 and existing.numchild == 0
        )
        if is_wagtail_placeholder and PageModel is not Page:
            return existing, True
        raise ValueError(
            f'a {existing.specific_class.__name__} already exists at this path, '
            f'not a {PageModel.__name__}'
        )

    def _existing_page(self, slug_path, PageModel):
        """The page to update at `slug_path`, or None to create one (see _matching_page)."""
        existing, placeholder = self._matching_page(slug_path, PageModel)
        if placeholder:
            self._console.line(f'  ↻  replacing Wagtail\'s default page at [{slug_path}]')
            # Site.root_page cascades: deleting the page as it stands would
            # delete the Site with it. Park the Sites on the tree root and
            # move them onto the imported page once it exists.
            sites = list(Site.objects.filter(root_page=existing).values_list('pk', flat=True))
            Site.objects.filter(pk__in=sites).update(root_page=Page.get_first_root_node())
            self._repoint_sites[slug_path] = sites
            existing.delete()
            return None
        return existing

    @contextmanager
    def _validation_skipped(self, PageModel):
        """
        Page.save() runs full_clean() unless told otherwise, and neither
        treebeard's add_child() nor Wagtail's publish action tells it. Without
        --strict-validation, switch it off for this model while the block
        runs: the archive holds rows the source database already accepted,
        and an author page imported without its contact details would
        otherwise fail on its required phone number.
        """
        if self._strict:
            yield
            return
        had_own = 'full_clean' in PageModel.__dict__
        original = PageModel.__dict__.get('full_clean')
        PageModel.full_clean = lambda self, *args, **kwargs: None
        try:
            yield
        finally:
            if had_own:
                PageModel.full_clean = original
            else:
                del PageModel.full_clean

    def import_pages(self, page_rows):
        locale = (
            Locale.objects.filter(language_code='en').first()
            or Locale.objects.first()
        )
        bar = self._console.bar(len(page_rows), 'pages')

        for pr in page_rows:
            slug_path = pr.get('slug_path')
            try:
                model_label      = pr['model']
                parent_slug_path = pr.get('parent_slug_path')
                base_fields      = pr.get('base_fields', {})
                specific_fields  = pr.get('specific_fields', {})

                try:
                    PageModel = django_apps.get_model(model_label)
                except (LookupError, ValueError) as e:
                    raise ValueError(f'unknown page type {model_label}') from e
                if not issubclass(PageModel, Page):
                    raise ValueError(f'{model_label} is not a page type')

                if parent_slug_path:
                    parent = _get_page_by_slug_path(parent_slug_path)
                    if parent is None and not (self._dry and parent_slug_path in self._planned):
                        raise ValueError(f'parent "{parent_slug_path}" not found')
                else:
                    parent = Page.get_first_root_node()

                if self._dry:
                    existing, placeholder = self._matching_page(slug_path, PageModel)
                    if existing is None or placeholder:
                        self._planned.add(slug_path)
                    updating = existing is not None and not placeholder
                    self._console.detail(f'  {"↻" if updating else "+"}  {model_label} [{slug_path}]')
                    self._stats['pages_updated' if updating else 'pages_created'] += 1
                    continue

                with transaction.atomic():
                    existing = self._existing_page(slug_path, PageModel)
                    self._console.detail(f'  {"↻" if existing else "+"}  {model_label} [{slug_path}]')
                    page = existing.specific if existing else PageModel(locale=locale)
                    self._app.apply(page, base_fields, skip_fields=self._BASE_SKIP)
                    self._app.apply(page, specific_fields, skip_fields={'id'})
                    if existing:
                        self._save_page(page)
                    else:
                        with self._validation_skipped(PageModel):
                            parent.add_child(instance=page)
                        sites = self._repoint_sites.pop(slug_path, None)
                        if sites:
                            Site.objects.filter(pk__in=sites).update(root_page=page)
                    self._app.apply_m2m(page, base_fields)
                    self._app.apply_m2m(page, specific_fields)
                self._stats['pages_updated' if existing else 'pages_created'] += 1
                self._imported.append(pr)
            except Exception as e:
                self._fail(f'page [{slug_path}]', e)
            finally:
                bar.advance(suffix=pr.get('slug', ''))
        bar.finish()

    # ── pass 2 ───────────────────────────────────────────────────────────

    @staticmethod
    def _needs_second_pass(field_name, value):
        """References that may point at pages created later in pass 1."""
        if isinstance(value, dict):
            return value.get('__ref') in ('page', 'snippet')
        if isinstance(value, list):
            return True  # StreamField: page choosers / rich-text page links
        return isinstance(value, str) and 'linktype="page"' in value

    def finalize_pages(self):
        if self._dry or not self._imported:
            return
        bar = self._console.bar(len(self._imported), 'finalise')
        for pr in self._imported:
            slug_path = pr['slug_path']
            try:
                with transaction.atomic():
                    page = _get_page_by_slug_path(slug_path).specific
                    fields = {}
                    for source in (pr.get('base_fields', {}), pr.get('specific_fields', {})):
                        for name, value in source.items():
                            if name != '__m2m' and self._needs_second_pass(name, value):
                                fields[name] = value
                        if source.get('__m2m'):
                            fields.setdefault('__m2m', {}).update(source['__m2m'])
                    self._app.apply(page, fields, skip_fields=self._BASE_SKIP - {'__m2m'})
                    self._restore_children(page, pr.get('children') or {})
                    self._restore_tags(page, pr.get('tags') or {})
                    self._save_page(page)
                    self._app.apply_m2m(page, fields)
                    self._publish(page, live=pr.get('base_fields', {}).get('live', True))
                self._stats['pages_finalised'] += 1
            except Exception as e:
                self._fail(f'page [{slug_path}] (references/children)', e)
            finally:
                bar.advance(suffix=slug_path)
        bar.finish()

    def _restore_children(self, page, children):
        """Replace each InlinePanel relation with the archive's rows."""
        from modelcluster.models import get_all_child_relations

        relations = {
            rel.get_accessor_name(): rel for rel in get_all_child_relations(page)
            if not rel.related_model.__module__.startswith('wagtail.')
        }
        for accessor, rows in children.items():
            rel = relations.get(accessor)
            if rel is None:
                self._console.warn(f'{type(page).__name__}.{accessor}: no such relation — skipped')
                continue
            ChildModel, parental = rel.related_model, rel.field
            objs = []
            for row in rows:
                child = ChildModel()
                self._app.apply(child, row, skip_fields={'id', parental.name, parental.attname})
                missing = [
                    f.name for f in ChildModel._meta.concrete_fields
                    if f.is_relation and not f.null and f is not parental
                    and getattr(child, f.attname, None) is None
                ]
                if missing:
                    self._console.warn(
                        f'{type(page).__name__}.{accessor}: a row references '
                        f'{", ".join(missing)} that isn\'t on this site — row skipped'
                    )
                    continue
                objs.append(child)
            getattr(page, accessor).set(objs)

    @staticmethod
    def _restore_tags(page, tags):
        for field_name, names in tags.items():
            getattr(page, field_name).set(list(names))

    def _save_page(self, page):
        """Save a page, bypassing full_clean() unless --strict-validation."""
        if self._strict:
            page.save()
            return
        try:
            page.save(clean=False)
        except TypeError:      # not a Wagtail Page (no clean kwarg)
            page.save()

    def _publish(self, page, live=True):
        """
        A new revision from the imported content, published if the source page
        was live. (Publishing get_latest_revision() republished this
        database's *previous* revision over everything just imported.)
        """
        revision = page.save_revision(clean=self._strict)
        if live:
            with self._validation_skipped(type(page)):
                revision.publish()


# ---------------------------------------------------------------------------
# Site record helper
# ---------------------------------------------------------------------------

def _import_site(site_data, console: Console):
    """
    Take the site *name* from the archive, never its hostname or port.

    The archive's hostname belongs to the server it came from. Copying it
    onto a staging or replacement server made this site answer to
    production's name (or to nothing, behind a different proxy) and broke
    every full URL Wagtail builds.
    """
    if not site_data:
        return
    site = Site.objects.filter(is_default_site=True).first()
    if site is None:
        console.warn('no default Site record on this database — site data ignored')
        return
    name = site_data.get('site_name')
    if name and name != site.site_name:
        site.site_name = name
        site.save(update_fields=['site_name'])
    console.line(f'  ↻  Site "{site.site_name}" — hostname kept as {site.hostname}:{site.port}')
    if site_data.get('hostname') and site_data['hostname'] != site.hostname:
        console.line(f'     (archive came from {site_data["hostname"]}:{site_data.get("port")})', level=2)


@contextmanager
def _publish_side_effects_muted():
    """
    An import restores content; it isn't news. Publishing an imported
    Article would queue text-to-speech for it again (a paid API call per
    article, and a new audio file replacing the imported one), and
    publishing an imported Issue would mail its newsletter to every reader.
    """
    from wagtail.signals import page_published

    from articles.models import Article
    from articles.signals import on_article_published
    from issue.models import Issue
    from reader.signals import on_issue_published

    receivers = ((on_article_published, Article), (on_issue_published, Issue))
    muted = [(r, s) for r, s in receivers if page_published.disconnect(r, sender=s)]
    try:
        yield
    finally:
        for receiver, sender in muted:
            page_published.connect(receiver, sender=sender)


class _ImportFailed(Exception):
    """Raised inside the transaction to roll the whole import back."""


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
            help='Keep whatever imports cleanly and skip what fails (the '
                 'command still exits non-zero and lists what was skipped). '
                 'Without it, any failure rolls the whole import back.',
        )
        parser.add_argument(
            '--skip-existing-media', action='store_true',
            help='Reuse media whose file is already here (this is the default)',
        )
        parser.add_argument(
            '--replace-existing-media', action='store_true',
            help='Create new media objects even when the same file already exists',
        )
        parser.add_argument(
            '--yes', '-y', action='store_true',
            help='Skip the confirmation prompt (required when not run from a terminal)',
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
        version = str(manifest.get('version') or '')
        if version not in SUPPORTED_VERSIONS:
            raise CommandError(
                f'archive format "{version}" is not one this importer understands '
                f'({", ".join(SUPPORTED_VERSIONS)}) — export it again with this release'
            )
        if version == '1.0':
            console.warn(
                'format 1.0 archive: no page/snippet tables, child rows or tags. Page '
                'links and snippet choosers inside content keep their source ids, '
                'article author credits are not restored, and media is matched by '
                'file content only where the archive carries the files.'
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
            # Proceeding silently here meant a stray cron line or CI step
            # could overwrite a production site with no one asked.
            raise CommandError(
                'Not running from a terminal, so there is no one to confirm. '
                'Re-run with --yes to import, or --dry-run to preview.'
            )
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
        failures = []   # one label per thing that did not import

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

            mi = _MediaImporter(
                zf, console, stats, dry_run=dry_run,
                replace_existing=options['replace_existing_media'],
                failures=failures,
            )
            # The remap tables are filled in by mi as media is imported.
            resolver = _Resolver(
                mi.image_map, mi.doc_map, console,
                page_paths=manifest.get('page_paths'),
                snippet_keys=manifest.get('snippet_keys'),
            )
            applicator = _Applicator(resolver, console)
            si = _SnippetImporter(resolver, applicator, console, stats, dry_run, failures=failures)
            pi = _PageImporter(
                resolver, applicator, console, stats, dry_run,
                strict=options['strict_validation'], failures=failures,
            )
            snips = manifest.get('snippets', {})

            def _step(label, fn):
                """
                One import step in its own savepoint. Rows inside a step have
                their own savepoints and record their failures; this only
                catches an error that escapes a step altogether.
                """
                console.heading(label)
                checkpoint = (
                    Counter(stats), dict(mi.image_map), dict(mi.doc_map),
                    len(mi.written_files), len(pi._imported), len(failures),
                )
                try:
                    with transaction.atomic():
                        fn()
                except Exception as e:
                    if not tolerant:
                        raise
                    # The step's savepoint is gone: put the bookkeeping back too,
                    # so the summary only counts what is really committed.
                    counts, images, docs, n_files, n_pages, n_failures = checkpoint
                    stats.clear(); stats.update(counts)
                    mi.image_map.clear(); mi.image_map.update(images)
                    mi.doc_map.clear(); mi.doc_map.update(docs)
                    mi.discard_written_files(since=n_files)
                    del pi._imported[n_pages:]
                    del failures[n_failures:]
                    console.error(f'{label} {e} — step rolled back')
                    failures.append(f'step "{label.rstrip("…")}"')

            try:
                with _publish_side_effects_muted(), transaction.atomic():

                    # 1 — media
                    _step('Importing images…',
                          lambda: mi.import_images(manifest.get('images', [])))
                    _step('Importing documents…',
                          lambda: mi.import_documents(manifest.get('documents', [])))

                    # 2 — site name (the hostname stays this server's own)
                    if not dry_run:
                        _step('Importing site record…',
                              lambda: _import_site(manifest.get('site', {}), console))

                    # 3 — snippets with no page references
                    _step('Importing volumes…',
                          lambda: si.import_volumes(snips.get('issue.volume', [])))
                    _step('Importing topics…',
                          lambda: si.import_topics(snips.get('issue.topic', [])))
                    _step('Importing site contact settings…',
                          lambda: si.import_site_contact(
                              snips.get('home.sitecontactsettings', [])))

                    # 4 — board rows before pages: Issue pages carry an
                    #     editorial_board FK. Members are attached in step 7,
                    #     once the Literati pages they point at exist.
                    _step('Importing editorial boards…',
                          lambda: si.import_board_shells(
                              snips.get('literati.editorialboard', [])))

                    # 5 — pages, in tree order (parents before children)
                    _step('Importing pages…',
                          lambda: pi.import_pages(manifest.get('pages', [])))

                    # 6 — with every page in place: references between pages,
                    #     child rows, tags, and a published revision
                    _step('Linking and publishing pages…', pi.finalize_pages)

                    # 7 — everything that points at pages
                    _step('Importing site header…',
                          lambda: si.import_site_header(
                              snips.get('home.siteheader', [])))
                    _step('Importing site footer…',
                          lambda: si.import_site_footer(
                              snips.get('home.sitefooter', [])))
                    _step('Attaching board members…',
                          lambda: si.import_editorial_boards(
                              snips.get('literati.editorialboard', [])))
                    _step('Importing membership history…',
                          lambda: si.import_membership_history(
                              snips.get('literati.editorialboardmembershiphistory', [])))

                    if failures and not tolerant and not dry_run:
                        raise _ImportFailed()
                    if dry_run:
                        transaction.set_rollback(True)
            except BaseException as e:
                # Rolled back: the files written for those rows go too.
                mi.discard_written_files()
                if isinstance(e, _ImportFailed):
                    self._report_failures(failures, console)
                    raise CommandError(
                        f'{len(failures)} item(s) failed to import, so nothing was '
                        f'written. Fix them, or re-run with --tolerant to keep the rest.'
                    )
                if isinstance(e, Exception) and not isinstance(e, CommandError):
                    raise CommandError(f'Import failed and was rolled back — nothing was written: {e}') from e
                raise

        self._summary(started, archive_path, dry_run, stats, manifest, console)
        if failures:
            self._report_failures(failures, console)
            if dry_run:
                raise CommandError(f'Dry run: {len(failures)} item(s) would fail to import.')
            raise CommandError(
                f'Imported with {len(failures)} item(s) skipped (listed above); '
                f'everything else is committed.'
            )

    # ── reporting ────────────────────────────────────────────────────────

    def _report_failures(self, failures, console):
        shown = failures[:25]
        self.stderr.write(self.style.ERROR(f'\n✗  {len(failures)} item(s) did not import:'))
        for label in shown:
            self.stderr.write(self.style.ERROR(f'     · {label}'))
        if len(failures) > len(shown):
            self.stderr.write(self.style.ERROR(f'     … and {len(failures) - len(shown)} more (-v 2 for all)'))

    def _summary(self, started, archive_path, dry_run, stats, manifest, console):
        elapsed = human_duration(time.monotonic() - started)
        verb = 'Would import' if dry_run else 'Imported'
        console.success(
            f'\n✓  {verb} from {archive_path}  ({elapsed})\n'
            f'   images    : {stats["images_created"]} created · '
            f'{stats["images_reused"]} reused\n'
            f'   documents : {stats["documents_created"]} created · '
            f'{stats["documents_reused"]} reused\n'
            f'   media     : {stats["media_rejected"]} rejected by upload checks\n'
            f'   pages     : {stats["pages_created"]} created · '
            f'{stats["pages_updated"]} updated · {stats["pages_failed"]} failed\n'
            f'   snippets  : {stats["snippets_created"]} created · '
            f'{stats["snippets_updated"]} updated'
        )
        if not dry_run:
            unsent = [
                pr.get('slug_path') for pr in manifest.get('pages', [])
                if pr.get('model') == 'issue.issue'
                and pr.get('base_fields', {}).get('live')
                and not pr.get('specific_fields', {}).get('newsletter_sent_at')
            ]
            if unsent:
                self.stdout.write(
                    f'   {len(unsent)} imported issue(s) were never mailed. Importing '
                    f'sends no newsletters; use send_newsletter --issue-id <pk> to send one.'
                )
        if console.warnings or console.errors:
            hint = '' if console.verbosity >= 2 else ' — re-run with -v 2 for detail'
            self.stdout.write(self.style.WARNING(
                f'   {console.warnings} warning(s), {console.errors} error(s){hint}'
            ))
