# Icarus Content Export / Import

Three management commands that let you carry structured content between
deployments without restoring a full `pg_dump`.

---

## Commands

| Command | Purpose |
|---|---|
| `export_content` | Serialise pages + snippets + media to a ZIP |
| `import_content` | Recreate everything from a ZIP on a clean DB |
| `check_archive`  | Inspect a ZIP without touching the database |

---

## Quick start

```bash
# 1. On the source server — export everything (pages + snippets + media)
python manage.py export_content --output prod_20260701.zip

# 2. Transfer the ZIP to the target machine
scp prod_20260701.zip deploy@new-server:/opt/icarus/

# 3. On the target — preview what will happen
python manage.py import_content prod_20260701.zip --dry-run

# 4. Import for real
python manage.py import_content prod_20260701.zip
```

---

## export_content

```
python manage.py export_content [--output FILE] [--scope SCOPE] [--no-media]
                                [--no-progress] [-v 0|1|2]
```

### Output verbosity

| Flag | Effect |
|---|---|
| `-v 0` | Silent except errors |
| `-v 1` | Headings, progress bars, summary *(default)* |
| `-v 2` | Adds one line per record — use this to identify a problem record |
| `--no-progress` | Disable progress bars (auto-disabled when output is piped) |

Progress bars redraw in place on a terminal; when output is redirected to a
file or CI log they collapse to a single start line and a single done line.

The export never aborts on one bad record: failures are reported as
`[!] <Model> pk=… — skipped` and the archive is still written. The summary
prints the warning count.

### `--output`

Accepts a file path or a directory. Passing a directory (or a path ending in
`/`) generates an auto-timestamped `icarus_content_YYYYmmdd_HHMMSS.zip` inside
it. Missing parent directories are created.

### `--scope`

| Value | What is exported |
|---|---|
| `full`       | **Every page type in the tree** + all snippets + media  *(default)* |
| `structural` | HomePage + index pages + all snippets + media (no articles/issues) |
| `snippets`   | Snippets + media only (no pages) |

`full` uses no model allow-list, so a page model added to the project later
cannot silently drop out of a "full" export.

### `--no-media`

Records image / document metadata in the manifest but does not embed the
actual files in the ZIP.  Useful for schema-only migrations where media
already exists at the destination via a shared volume or S3 sync.

### ZIP structure

```
icarus_content.zip
├── manifest.json              ← all serialised data
├── media/
│   ├── images/<pk>/<file>     ← original image files
│   └── documents/<pk>/<file>  ← PDFs, MP3s, etc.
```

Media sits under a per-PK directory so two files that share a basename
(`logo.png` in different upload folders) cannot collide inside the archive.
Already-compressed formats (JPEG, PNG, MP3, MP4 …) are stored verbatim rather
than deflated, which speeds up export noticeably on media-heavy sites.

### What is serialised

- **Snippets**: `Volume`, `Topic`, `SiteContactSettings`, `SiteHeader`,
  `SiteFooter` (with full section → column → link hierarchy),
  `EditorialBoard` (with ordered member list),
  `EditorialBoardMembershipHistory`
- **Pages**: all pages in `--scope`, stored in tree order (parents first)
  so that `add_child` works during import
- **Images** and **Documents**: metadata + file bytes
- **Site record**: hostname, port, site name of the default site

### Media discovery (rich text + StreamField)

Media is collected from the content that is actually exported, from three
places:

1. **Model FK fields** — `cover_image`, `PDF_file`, …
2. **StreamField values**, walked against the field's own *block definitions*
   (`ImageChooserBlock`, `DocumentChooserBlock`, `RichTextBlock`, and anything
   nested inside `StructBlock` / `ListBlock` / `StreamBlock`). Driving off the
   definitions rather than block names means new or renamed blocks are picked
   up with no change to the exporter.
3. **Rich text markup**, where Wagtail stores references as PKs inside tags
   rather than as model FKs:

   ```html
   <embed embedtype="image" id="42" format="fullwidth" alt="…"/>
   <a linktype="document" id="7">the PDF</a>
   ```

   These are invisible to a field-level scan, so they are parsed explicitly —
   in `RichTextField` model fields *and* in every `RichTextBlock` (including
   image captions, blockquote text and `ListBlock` items). `import_content`
   rewrites the same tags to the destination PKs.

`<embed embedtype="media">` is an external oEmbed URL, not a stored file, so
it is skipped. `<a linktype="page">` is left untouched.

> **Scope matters for media.** Only exported content is scanned, so
> `--scope structural` carries no article images. The command warns when pages
> are excluded by the active scope.

### How FK references are stored

Foreign keys that must survive PK remapping on import are stored as
typed dicts rather than raw integers:

```json
{ "__ref": "image",    "pk": 42 }
{ "__ref": "document", "pk": 17 }
{ "__ref": "page",     "slug_path": "home/issues" }
```

Plain integer FKs (e.g. `Volume`, `Topic`) are stored as-is because those
are looked up by natural key on import.

StreamField JSON is stored as a parsed Python list (not a raw string) so
that the image/document PK rewriter can walk it without a double
JSON round-trip.

---

## import_content

```
python manage.py import_content ARCHIVE [--dry-run] [--tolerant] [--yes]
                                [--replace-existing-media] [--no-progress] [-v 0|1|2]
```

Before writing, the command prints what the archive contains and asks for
confirmation. Pass `--yes` to skip the prompt; it is skipped automatically
when stdin is not a terminal (CI, cron) or with `--dry-run`.

The manifest's format version and the Wagtail major version it was built on
are checked on load — a mismatch is reported as a warning rather than a
silent failure.

The final summary reports created / reused / updated / skipped counts per
category plus the warning count.

### Import order

The command enforces a dependency-safe order inside a single `transaction.atomic()`:

1. Images
2. Documents
3. Site record
4. Volumes → Topics → SiteContactSettings → SiteHeader → SiteFooter
5. Pages *(tree order — parents before children)*
6. EditorialBoards → MembershipHistory *(depend on Literati pages)*

### Idempotency

Running `import_content` twice on the same ZIP is safe:

- **Images / Documents** — looked up by `title`; if found, the existing
  PK is added to the remap table and the file is not re-uploaded.
- **Snippets** — upserted by natural key (`number` for Volume, `slug` for
  Topic, `name` for EditorialBoard).
- **Pages** — looked up by slug-path.  If found under the correct parent,
  only the fields are updated; the page is not moved or recreated.
- **SiteFooter** — the existing section/column/link hierarchy is deleted
  and rebuilt cleanly on each import (the footer itself is upserted).

### `--dry-run`

Runs the full import logic inside an atomic block then calls
`transaction.set_rollback(True)` before exit.  The output shows exactly
what would be created or updated without writing a single row.

Note that file-storage writes are *not* transactional, so a dry run also skips
saving media files to disk — otherwise a "preview" would litter the media
directory with orphans that the rollback cannot remove.  FK pointers to those
not-yet-created images therefore resolve to `None` during a preview; that is
expected and is discarded with the rollback.

### `--tolerant`

Wraps each import step in a try/except.  A failure in one step (e.g. a
missing parent page) is logged as a warning and the command continues to
the next step.  Without `--tolerant`, any error aborts the whole
transaction.

### `--skip-existing-media` / `--replace-existing-media`

Reusing media that already matches by title is the **default** behaviour, so
`--skip-existing-media` is accepted for backwards compatibility but changes
nothing.  Pass `--replace-existing-media` to force creation of new media
objects even when a title matches — useful when the destination holds stale
files that should be superseded.

---

## check_archive

```
python manage.py check_archive ARCHIVE [--verbose | -v 2]
```

`--verbose` and Django's `-v 2` are equivalent. Note there is no `-v` short
flag for `--verbose`: `-v` belongs to Django's own `--verbosity` and
re-registering it makes argparse raise before the command can start.

Reads the ZIP manifest and prints a summary of what it contains — model
counts, media presence, potential issues — without connecting to the DB
or writing anything.

```
  prod_20260701.zip  (14.3 MB)
  version  : 1.0
  created  : 2026-07-01T09:14:22
  wagtail  : 7.4.0
  scope    : structural
  zip files: 87 entries

  Site
    Sasthragathi  sasthragathi.in:443

  Media  (62 images, 8 documents)
    images    in ZIP: 62/62
    documents in ZIP: 8/8

  Pages  (6 total)
    articles.articleindexpage         × 1
    home.homepage                     × 1
    issue.issueindexpage              × 1
    literati.authorindexpage          × 1

  Snippets
    issue.volume                      × 38
    issue.topic                       × 12
    home.sitecontactsettings          × 1
    home.siteheader                   × 1
    home.sitefooter                   × 1
    literati.editorialboard           × 3
    literati.editorialboardmembership × 24

  ✓  No structural issues detected
```

---

## Typical deployment workflow

```bash
# ── Source server ─────────────────────────────────────────────────────
python manage.py export_content --output release_$(date +%Y%m%d).zip

# ── CI / transfer ────────────────────────────────────────────────────
scp release_*.zip deploy@prod:/opt/icarus/exports/

# ── Target server (fresh DB — migrations already applied) ────────────
python manage.py migrate
python manage.py createsuperuser

# Inspect first
python manage.py check_archive exports/release_*.zip --verbose

# Dry-run
python manage.py import_content exports/release_*.zip --dry-run

# Import
python manage.py import_content exports/release_*.zip

# Rebuild search index if wagtail-meilisearch is active
python manage.py update_index
```

---

## Extending for new models

### Adding a new snippet type

1. Add an `export_<name>` method to `ContentExporter` in `export_content.py`
   following the same pattern as `export_volumes`.
2. Call it in `Command.handle` and add the result to `snippets[...]`.
3. Add an `import_<name>` method to `_SnippetImporter` in `import_content.py`.
4. Call it via `_step(...)` in the correct dependency order in `Command.handle`.

### Adding a new page model

No code changes needed — `export_pages` serialises all page models
generically and the default `full` scope exports every page type.  Add the
model label to `STRUCTURAL_PAGE_MODELS` in `export_content.py` only if it
should also appear in the narrower `structural` scope.

### Adding a new StreamField block type

If the new block contains an image or document FK, add a branch to
`_Serialiser._scan_stream` (export) and `_Resolver.rewrite_stream`
(import) following the `audio`/`video` pattern.

---

## Limitations

- **Wagtail revisions** are not exported.  Imported pages are saved as a
  single revision and immediately published if the original was live.
- **Renditions / image caches** are not exported; Wagtail regenerates them
  on first request.
- **Reader accounts** (`ReaderUser`) are intentionally excluded — they
  contain encrypted PII and belong to a production restore path, not a
  content deploy.
- **`the_librarian` document chunks and embeddings** are excluded — these
  are regenerated by `ingest_archive` after content is imported.
- **Ordering fields** that use `django-treebeard` path encoding (page tree)
  are handled by `add_child`; `sort_order` on orderable snippets
  (e.g. `FooterColumnLink`) is carried through the `__m2m` or direct
  field serialisation.
