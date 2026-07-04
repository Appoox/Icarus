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

    # Reuse media that already exists on disk (matched by title)
    python manage.py import_content icarus_content.zip --skip-existing-media

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
"""

import json
import os
import zipfile
from datetime import date, datetime

from django.apps import apps as django_apps
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from wagtail.images import get_image_model
from wagtail.documents import get_document_model
from wagtail.models import Page, Site, Locale


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

    def __init__(self, image_map, doc_map, stderr):
        self._img = image_map   # {original_pk -> new_pk}
        self._doc = doc_map
        self._err = stderr

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
            else:
                out.append(item)
        return out

    # ── StreamField blocks ────────────────────────────────────────────────

    def rewrite_stream(self, blocks):
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

            elif isinstance(val, list):
                block['value'] = self.rewrite_stream(val)
            elif isinstance(val, dict):
                block['value'] = {
                    k: (self.rewrite_stream(v) if isinstance(v, list) else v)
                    for k, v in val.items()
                }

            out.append(block)
        return out


# ---------------------------------------------------------------------------
# Media importer
# ---------------------------------------------------------------------------

class _MediaImporter:
    def __init__(self, zf, skip_existing, stdout, stderr):
        self._zf     = zf
        self._skip   = skip_existing
        self.stdout  = stdout
        self.stderr  = stderr
        self.image_map = {}   # {original_pk -> new_pk}
        self.doc_map   = {}

    def import_images(self, entries):
        Im = get_image_model()
        for entry in entries:
            orig_pk = entry['pk']
            title   = entry.get('title', f'image_{orig_pk}')

            existing = Im.objects.filter(title=title).first()
            if existing:
                self.stdout.write(f'    ↩  image "{title}" — reusing pk={existing.pk}')
                self.image_map[orig_pk] = existing.pk
                continue

            file_path = entry.get('file')
            if not file_path:
                self.stderr.write(f'    [!] image "{title}": no file path — skipped')
                continue
            try:
                raw = self._zf.read(file_path)
            except KeyError:
                self.stderr.write(f'    [!] image "{title}": not in ZIP — skipped')
                continue

            img = Im(title=title)
            for attr in ('focal_point_x', 'focal_point_y',
                         'focal_point_width', 'focal_point_height'):
                if entry.get(attr) is not None:
                    setattr(img, attr, entry[attr])
            img.file.save(os.path.basename(file_path), ContentFile(raw), save=True)
            self.image_map[orig_pk] = img.pk
            self.stdout.write(f'    +  image "{title}" → pk={img.pk}')

    def import_documents(self, entries):
        Do = get_document_model()
        for entry in entries:
            orig_pk = entry['pk']
            title   = entry.get('title', f'document_{orig_pk}')

            existing = Do.objects.filter(title=title).first()
            if existing:
                self.stdout.write(f'    ↩  document "{title}" — reusing pk={existing.pk}')
                self.doc_map[orig_pk] = existing.pk
                continue

            file_path = entry.get('file')
            if not file_path:
                self.stderr.write(f'    [!] document "{title}": no file path — skipped')
                continue
            try:
                raw = self._zf.read(file_path)
            except KeyError:
                self.stderr.write(f'    [!] document "{title}": not in ZIP — skipped')
                continue

            doc = get_document_model()(title=title)
            doc.file.save(os.path.basename(file_path), ContentFile(raw), save=True)
            self.doc_map[orig_pk] = doc.pk
            self.stdout.write(f'    +  document "{title}" → pk={doc.pk}')


# ---------------------------------------------------------------------------
# Field applicator
# ---------------------------------------------------------------------------

class _Applicator:
    """Applies a serialised dict back onto a model instance."""

    def __init__(self, resolver, stderr):
        self._res = resolver
        self._err = stderr

    def apply(self, instance, data, skip_fields=None):
        """
        Copy values from `data` onto `instance` fields.
        Does NOT call save() — the caller is responsible.
        """
        from django.db import models as _djm
        from wagtail.fields import StreamField as _SF

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
                    blocks = self._res.rewrite_stream(raw) if isinstance(raw, list) else raw
                    setattr(instance, name, json.dumps(blocks))

                # ForeignKey — remap via __ref or plain PK
                elif isinstance(field, _djm.ForeignKey):
                    rm = field.related_model
                    if raw is None:
                        setattr(instance, field.attname, None)
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
                self._err.write(
                    f'      [!] {type(instance).__name__}.{name}={raw!r}: {e}'
                )

    def apply_m2m(self, instance, data):
        """Apply M2M fields stored under data['__m2m']. Call after save()."""
        for name, ref_list in data.get('__m2m', {}).items():
            resolved = self._res.resolve_m2m(ref_list)
            try:
                getattr(instance, name).set(resolved)
            except Exception as e:
                self._err.write(
                    f'      [!] M2M {type(instance).__name__}.{name}: {e}'
                )


# ---------------------------------------------------------------------------
# Snippet importers
# ---------------------------------------------------------------------------

class _SnippetImporter:
    def __init__(self, resolver, applicator, stdout, stderr, dry_run):
        self._res  = resolver
        self._app  = applicator
        self.stdout = stdout
        self.stderr = stderr
        self._dry  = dry_run

    def _upsert(self, Model, lookup, data, extra_skip=None):
        skip = set(extra_skip or []) | set(lookup.keys())
        try:
            obj, created = Model.objects.get_or_create(**lookup)
        except Exception as e:
            self.stderr.write(f'    [!] {Model.__name__} {lookup}: {e}')
            return None
        self.stdout.write(f'    {"+" if created else "↻"}  {Model.__name__} {lookup}')
        if not self._dry:
            self._app.apply(obj, data, skip_fields=skip)
            obj.save()
            self._app.apply_m2m(obj, data)
        return obj

    def import_volumes(self, rows):
        from issue.models import Volume
        for row in rows:
            self._upsert(Volume, {'number': row['number']}, row)

    def import_topics(self, rows):
        from issue.models import Topic
        for row in rows:
            self._upsert(Topic, {'slug': row['slug']}, row)

    def import_site_contact(self, rows):
        from home.models import SiteContactSettings
        for row in rows:
            if not self._dry:
                obj = SiteContactSettings.objects.first() or SiteContactSettings()
                self._app.apply(obj, row)
                obj.save()
            self.stdout.write('    ↻  SiteContactSettings')

    def import_site_header(self, rows):
        from home.models import SiteHeader
        for row in rows:
            if not self._dry:
                obj = SiteHeader.objects.first() or SiteHeader()
                self._app.apply(obj, row)
                obj.save()
                self._app.apply_m2m(obj, row)
            self.stdout.write('    ↻  SiteHeader')

    def import_site_footer(self, rows):
        from home.models import (
            SiteFooter, FooterSection, FooterColumn, FooterColumnLink
        )
        for fd in rows:
            sections_data = fd.pop('_sections', [])
            if self._dry:
                self.stdout.write(
                    f'    ~  SiteFooter ({len(sections_data)} section(s)) — dry-run'
                )
                continue

            footer = SiteFooter.objects.first() or SiteFooter()
            self._app.apply(footer, fd, skip_fields=['id'])
            footer.save()
            self.stdout.write('    ↻  SiteFooter')

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

    def import_editorial_boards(self, rows):
        from literati.models import EditorialBoard, EditorialBoardMember
        for bd in rows:
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
                    self.stderr.write(
                        f'      [!] EditorialBoardMember: '
                        f'editor not found {editor_ref!r}'
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
                self.stdout.write(
                    f'    {"+" if created else "↻"}  '
                    f'EditorialBoardMember editor_id={editor_pk}'
                )

    def import_membership_history(self, rows):
        from literati.models import (
            EditorialBoardMembershipHistory, EditorialBoard
        )
        for hd in rows:
            board_ref  = hd.get('board') or hd.get('board_id')
            editor_ref = hd.get('editor') or hd.get('editor_id')

            # Resolve board (stored as plain name string or int PK)
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
                self.stderr.write(
                    '      [!] MembershipHistory: unresolved refs — skipped'
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
            self.stdout.write(
                f'    {"+" if created else "↩"}  '
                f'MembershipHistory editor_id={editor_pk}'
            )


# ---------------------------------------------------------------------------
# Page importer
# ---------------------------------------------------------------------------

class _PageImporter:
    # Fields on wagtailcore_page that must never be set on the child table
    _BASE_SKIP = frozenset({
        'id', 'path', 'depth', 'numchild',
        'content_type', 'content_type_id',
        'locale', 'locale_id',
        'translation_key', 'alias_of', 'alias_of_id',
    })

    def __init__(self, resolver, applicator, stdout, stderr, dry_run):
        self._res  = resolver
        self._app  = applicator
        self.stdout = stdout
        self.stderr = stderr
        self._dry  = dry_run

    def import_pages(self, page_rows):
        locale = (
            Locale.objects.filter(language_code='en').first()
            or Locale.objects.first()
        )

        for pr in page_rows:
            model_label      = pr['model']
            slug_path        = pr['slug_path']
            parent_slug_path = pr.get('parent_slug_path')
            base_fields      = pr.get('base_fields', {})
            specific_fields  = pr.get('specific_fields', {})

            # ── Resolve model class ──────────────────────────────────────
            try:
                app_label, model_name = model_label.split('.')
                PageModel = django_apps.get_model(app_label, model_name)
            except (LookupError, ValueError) as e:
                self.stderr.write(
                    f'  [!] Cannot resolve {model_label}: {e} — skipped'
                )
                continue

            # ── Find parent page ─────────────────────────────────────────
            if parent_slug_path:
                parent = _get_page_by_slug_path(parent_slug_path)
                if parent is None:
                    self.stderr.write(
                        f'  [!] Parent "{parent_slug_path}" not found '
                        f'for {slug_path} — skipped'
                    )
                    continue
            else:
                parent = Page.get_first_root_node()

            existing = _get_page_by_slug_path(slug_path)
            self.stdout.write(
                f'  {"↻" if existing else "+"}  {model_label} [{slug_path}]'
            )
            if self._dry:
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
                    specific.save()
                    self._app.apply_m2m(specific, base_fields)
                    self._app.apply_m2m(specific, specific_fields)
                    target = specific
                else:
                    page = PageModel(locale=locale)
                    self._app.apply(
                        page, base_fields, skip_fields=self._BASE_SKIP
                    )
                    self._app.apply(
                        page, specific_fields, skip_fields={'id'}
                    )
                    parent.add_child(instance=page)
                    self._app.apply_m2m(page, base_fields)
                    self._app.apply_m2m(page, specific_fields)
                    target = page
            except Exception as e:
                self.stderr.write(f'  [!] Failed to import {slug_path}: {e}')
                continue

            if base_fields.get('live', True):
                self._publish(target)

    @staticmethod
    def _publish(page):
        try:
            rev = page.get_latest_revision()
            if rev:
                rev.publish()
            else:
                page.save_revision().publish()
        except Exception:
            pass   # non-fatal — page is already in the tree


# ---------------------------------------------------------------------------
# Site record helper
# ---------------------------------------------------------------------------

def _import_site(site_data, stdout):
    if not site_data:
        return
    site = Site.objects.filter(is_default_site=True).first()
    if site:
        site.hostname  = site_data.get('hostname',  site.hostname)
        site.port      = site_data.get('port',      site.port)
        site.site_name = site_data.get('site_name', site.site_name)
        site.save()
        stdout.write(f'  ↻  Site "{site.hostname}:{site.port}"')


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
            help='Reuse media that already exists by title; skip re-uploading',
        )

    def handle(self, *args, **options):
        archive_path        = options['archive']
        dry_run             = options['dry_run']
        tolerant            = options['tolerant']
        skip_existing_media = options['skip_existing_media']

        if not os.path.exists(archive_path):
            raise CommandError(f'Archive not found: {archive_path}')

        if dry_run:
            self.stdout.write(
                self.style.WARNING('DRY RUN — no changes will be written\n')
            )

        with zipfile.ZipFile(archive_path, 'r') as zf:
            self.stdout.write(self.style.MIGRATE_HEADING('Reading manifest…'))
            try:
                manifest = json.loads(zf.read('manifest.json'))
            except KeyError:
                raise CommandError('ZIP does not contain manifest.json')

            self.stdout.write(
                f'  version={manifest.get("version")}  '
                f'scope={manifest.get("scope")}  '
                f'created={manifest.get("created_at", "?")[:19]}'
            )

            def _step(label, fn):
                self.stdout.write(self.style.MIGRATE_HEADING(label))
                if tolerant:
                    try:
                        fn()
                    except Exception as e:
                        self.stderr.write(
                            self.style.ERROR(f'  ERROR — {label}: {e}')
                        )
                else:
                    fn()

            with transaction.atomic():

                # 1 — media
                mi = _MediaImporter(
                    zf, skip_existing_media, self.stdout, self.stderr
                )
                _step('Importing images…',
                      lambda: mi.import_images(manifest.get('images', [])))
                _step('Importing documents…',
                      lambda: mi.import_documents(manifest.get('documents', [])))

                # 2 — build resolver / applicator with the completed remap tables
                resolver   = _Resolver(mi.image_map, mi.doc_map, self.stderr)
                applicator = _Applicator(resolver, self.stderr)
                si = _SnippetImporter(
                    resolver, applicator, self.stdout, self.stderr, dry_run
                )
                pi = _PageImporter(
                    resolver, applicator, self.stdout, self.stderr, dry_run
                )
                snips = manifest.get('snippets', {})

                # 3 — site record
                _step('Importing site record…',
                      lambda: _import_site(manifest.get('site', {}), self.stdout))

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

                # 5 — pages (must precede editorial boards; Literati pages
                #           must exist before member FKs can be resolved)
                _step('Importing pages…',
                      lambda: pi.import_pages(manifest.get('pages', [])))

                # 6 — editorial boards and history (depend on Literati pages)
                _step('Importing editorial boards…',
                      lambda: si.import_editorial_boards(
                          snips.get('literati.editorialboard', [])))
                _step('Importing membership history…',
                      lambda: si.import_membership_history(
                          snips.get('literati.editorialboardmembershiphistory', [])))

                if dry_run:
                    transaction.set_rollback(True)

        verb = 'Would import' if dry_run else 'Imported'
        self.stdout.write(self.style.SUCCESS(
            f'\n✓  {verb} from {archive_path}\n'
            f'   images:    {len(manifest.get("images", []))}\n'
            f'   documents: {len(manifest.get("documents", []))}\n'
            f'   pages:     {len(manifest.get("pages", []))}\n'
            f'   snippets:  volumes · topics · site header/footer · '
            f'editorial boards'
        ))
