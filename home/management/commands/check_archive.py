"""
check_archive  —  Inspect an export_content ZIP without importing anything.

Usage
-----
    python manage.py check_archive icarus_content.zip
    python manage.py check_archive icarus_content.zip --verbose
"""

import json
import os
import zipfile

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = 'Inspect an export_content ZIP archive without importing anything'

    def add_arguments(self, parser):
        parser.add_argument('archive', help='Path to the ZIP archive')
        parser.add_argument(
            '--verbose', '-v', action='store_true',
            help='Print every record title/slug rather than just counts',
        )

    def handle(self, *args, **options):
        archive_path = options['archive']
        verbose      = options['verbose']

        if not os.path.exists(archive_path):
            raise CommandError(f'Archive not found: {archive_path}')

        size_mb = os.path.getsize(archive_path) / 1_048_576

        with zipfile.ZipFile(archive_path, 'r') as zf:
            names = zf.namelist()
            try:
                manifest = json.loads(zf.read('manifest.json'))
            except KeyError:
                raise CommandError('Not a valid Icarus export: manifest.json missing')

        w = self.stdout.write
        ok = self.style.SUCCESS
        hd = self.style.MIGRATE_HEADING
        mi = self.style.MIGRATE_LABEL   # dim / info

        w('')
        w(ok(f'  {os.path.basename(archive_path)}  ({size_mb:.1f} MB)'))
        w(f'  version  : {manifest.get("version", "?")}')
        w(f'  created  : {manifest.get("created_at", "?")[:19]}')
        w(f'  wagtail  : {manifest.get("wagtail_version", "?")}')
        w(f'  scope    : {manifest.get("scope", "?")}')
        w(f'  zip files: {len(names)} entries')

        # ── Site ──────────────────────────────────────────────────────────
        site = manifest.get('site', {})
        if site:
            w('')
            w(hd('Site'))
            w(f'  {site.get("site_name", "—")}  '
              f'{site.get("hostname", "?")}:{site.get("port", 80)}')

        # ── Media ─────────────────────────────────────────────────────────
        images    = manifest.get('images', [])
        documents = manifest.get('documents', [])
        w('')
        w(hd(f'Media  ({len(images)} images, {len(documents)} documents)'))

        if verbose:
            for img in images:
                in_zip = img.get('file', '') in names
                flag = ok('✓') if in_zip else self.style.ERROR('✗')
                w(f'  {flag} [img  pk={img["pk"]:>4}] {img.get("title", "—")}')
            for doc in documents:
                in_zip = doc.get('file', '') in names
                flag = ok('✓') if in_zip else self.style.ERROR('✗')
                w(f'  {flag} [doc  pk={doc["pk"]:>4}] {doc.get("title", "—")}')
        else:
            imgs_in_zip = sum(
                1 for i in images if i.get('file', '') in names
            )
            docs_in_zip = sum(
                1 for d in documents if d.get('file', '') in names
            )
            w(f'  images    in ZIP: {imgs_in_zip}/{len(images)}')
            w(f'  documents in ZIP: {docs_in_zip}/{len(documents)}')

        # ── Pages ─────────────────────────────────────────────────────────
        pages = manifest.get('pages', [])
        w('')
        w(hd(f'Pages  ({len(pages)} total)'))
        if pages:
            # Group by model
            by_model: dict = {}
            for pg in pages:
                by_model.setdefault(pg['model'], []).append(pg)
            for model, group in sorted(by_model.items()):
                w(f'  {model:<45} × {len(group)}')
                if verbose:
                    for pg in group:
                        live = '●' if pg.get('base_fields', {}).get('live') else '○'
                        w(f'    {live} {pg["slug_path"]}')

        # ── Snippets ──────────────────────────────────────────────────────
        snippets = manifest.get('snippets', {})
        w('')
        w(hd('Snippets'))
        for key, rows in snippets.items():
            if not rows:
                continue
            w(f'  {key:<50} × {len(rows)}')
            if verbose:
                for row in rows:
                    label = (
                        row.get('name')
                        or row.get('title')
                        or row.get('slug')
                        or row.get('number')
                        or '—'
                    )
                    members = len(row.get('_members', []))
                    suffix  = f'  ({members} members)' if members else ''
                    w(f'    · {label}{suffix}')

        # ── Potential issues ─────────────────────────────────────────────
        issues = []
        for img in images:
            if img.get('file') and img['file'] not in names:
                issues.append(f'image "{img.get("title")}" file missing from ZIP')
        for doc in documents:
            if doc.get('file') and doc['file'] not in names:
                issues.append(f'document "{doc.get("title")}" file missing from ZIP')

        w('')
        if issues:
            w(self.style.WARNING(f'  ⚠  {len(issues)} potential issue(s):'))
            for issue in issues:
                w(self.style.WARNING(f'     · {issue}'))
        else:
            w(ok('  ✓  No structural issues detected'))
        w('')
