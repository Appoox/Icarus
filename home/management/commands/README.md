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

An archive leaves authors' contact details out unless you export with
`--include-personal-data` (see below).

---

## export_content

```
python manage.py export_content [--output FILE] [--scope SCOPE] [--no-media]
                                [--include-personal-data] [--no-progress] [-v 0|1|2]
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

The ZIP is written to `<name>.partial` and renamed into place only once it is
complete, so an interrupted export never leaves a truncated archive that looks
like a good one.

### `--include-personal-data`

Off by default. Without it, the author (Literati) fields that hold contact
details — phone number, email, postal address — are left out of the archive,
the same fields the audit log excludes. An archive is an unencrypted file that
tends to get copied around; most uses (staging, a content deploy) don't need
any of it.

Use it only for a full restore onto a replacement server, and keep that
archive private. `check_archive` shows whether an archive carries personal
data. Authors imported without it have no phone number, so their pages can't
be saved from the admin until one is entered.

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
it is skipped. `<a linktype="page">` links are remapped on import through the
archive's page table (below).

The exporter and importer share one StreamField walker (`_streamwalk.py`), so
whatever the exporter collects — images, documents, pages, snippets, inside
any nesting — the importer remaps. Before, they were two walkers that had
drifted apart (`image_override` was collected but never remapped).

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

Snippets the importer upserts by natural key (`Volume`, `Topic`,
`EditorialBoard`) are stored by that key:

```json
{ "__ref": "snippet", "model": "issue.topic", "key": { "slug": "science" } }
```

Any other FK — above all **user accounts** (`Literati.reader_user`) — has no
portable identity and is written as `null`. Older archives carried the raw
PK, and importing it attached the author page (and its phone/email sync) to
whichever account had that PK on the destination.

Ids inside StreamField chooser blocks and rich text can't carry a ref dict,
so the manifest has two lookup tables (format 1.1):

- `page_paths` — `{source page pk: slug path}`
- `snippet_keys` — `{model: {source pk: natural key}}`

Each page row also carries its InlinePanel rows (`children`, e.g. article
author credits) and its tags by name (`tags`). Media entries carry
Wagtail's `file_hash`.

StreamField JSON is stored as a parsed Python list (not a raw string) so
that the walker can remap it without a double JSON round-trip.

---

## import_content

```
python manage.py import_content ARCHIVE [--dry-run] [--tolerant] [--yes]
                                [--replace-existing-media] [--strict-validation]
                                [--no-progress] [-v 0|1|2]
```

Before writing, the command prints what the archive contains and asks for
confirmation. Pass `--yes` to skip the prompt. When stdin is not a terminal
(cron, CI, `docker exec` without `-t`) there is no one to ask, so the command
**refuses to run without `--yes`** — it used to go ahead silently.
`--dry-run` never prompts.

The manifest's format version is checked on load: formats `1.0` and `1.1` are
accepted (1.0 with a warning about what it lacks), anything else is refused.
A different Wagtail major version is reported as a warning.

### Exit codes and failures

Every row is written in its own savepoint, so one bad row can't poison the
rows after it.

| Mode | On any failure |
|---|---|
| default | The whole import is rolled back, the media files it wrote are deleted, the failures are listed, and the command exits non-zero. |
| `--tolerant` | Everything that imported cleanly is committed, the failures are listed, and the command **still exits non-zero**, so a script can't mistake a partial import for a complete one. |
| `--dry-run` | Nothing is written; exits non-zero if anything would fail. |

The summary counts only what was really written (created / reused / updated
/ failed per category, and media rejected by the upload checks).

### Import order

Inside a single `transaction.atomic()`:

1. Images, documents
2. Site name
3. Volumes → Topics → SiteContactSettings → EditorialBoard rows
4. Pages *(tree order — parents before children)*
5. Pages again, with every page in place: references between pages,
   StreamField/rich-text ids, InlinePanel rows, tags, then a new published
   revision
6. SiteHeader → SiteFooter *(footer links point at pages)* → board members →
   membership history *(these point at Literati pages)*

### What the import never does

- **Hostname.** Only the site *name* comes from the archive; the default
  Site keeps this server's hostname and port. Copying them made a staging or
  replacement server answer to the source server's address.
- **User accounts.** No FK into the user table is set from an archive.
- **Side effects of publishing.** Imported Articles don't queue text-to-speech
  (a paid API call per article) and imported Issues don't send the newsletter.
  The summary lists live issues that were never mailed; send one with
  `send_newsletter --issue-id <pk>` if that is wanted.

### Idempotency

Running `import_content` twice on the same ZIP is safe:

- **Images / Documents** — matched by file content (Wagtail's `file_hash`),
  never by title: two different pictures both called "cover" stay two images.
  Rows saved without a stored hash (scripts, generated TTS audio) are hashed
  on the fly when their size matches.
- **Snippets** — upserted by natural key (`number` for Volume, `slug` for
  Topic, `name` for EditorialBoard).
- **Pages** — looked up by slug path. A page of the same type is updated in
  place; a page of a *different* type at that path is a failure rather than a
  half-applied update. The one exception is Wagtail's default "Welcome" page
  on a fresh database, which is replaced by the archive's HomePage (and the
  default Site is moved onto it).
- **Revisions** — every imported page gets a new revision built from the
  imported content, published if the source page was live. (Publishing the
  latest *existing* revision used to put this database's old content back
  over the import.)
- **SiteFooter** — the section/column/link hierarchy is deleted and rebuilt
  on each import (the footer itself is upserted).

### Uploaded files are checked like admin uploads

An archive is just a ZIP anyone can build, so media goes through the same
checks as an upload in the Wagtail admin: allowed image extensions
(`WAGTAILIMAGES_EXTENSIONS`; no SVG by default), the size limit
(`WAGTAILIMAGES_MAX_UPLOAD_SIZE`), a real decodable image (dimensions are read
from the file, not the manifest), and `WAGTAILDOCS_EXTENSIONS` for documents.
A rejected file counts as a failure.

### `--dry-run`

Runs the import logic inside the transaction and rolls it back. File-storage
writes are *not* transactional, so a dry run never saves media files — it
reports what it would create. References to those not-yet-created images
resolve to empty during the preview; that is expected.

### `--strict-validation`

Runs model validation on imported pages. Off by default: an archive restores
rows the source database already accepted, and validation added since (or an
author page without its contact details) would reject them.

### `--skip-existing-media` / `--replace-existing-media`

Reusing media whose file is already here is the **default**, so
`--skip-existing-media` is accepted for backwards compatibility but changes
nothing. `--replace-existing-media` creates new media objects even when the
same file exists.

---

## check_archive

```
python manage.py check_archive ARCHIVE [--verbose | -v 2]
```

`--verbose` and Django's `-v 2` are equivalent. Note there is no `-v` short
flag for `--verbose`: `-v` belongs to Django's own `--verbosity` and
re-registering it makes argparse raise before the command can start.

Reads the ZIP manifest and prints a summary of what it contains — format,
whether it carries personal data, model counts, media presence, potential
issues — without connecting to the DB or writing anything.

```
  prod_20260701.zip  (14.3 MB)
  version  : 1.1
  created  : 2026-07-01T09:14:22
  wagtail  : 7.4.3
  scope    : structural
  personal : no (authors' contact details left out)
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

# Import (add --yes when running from a script)
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

Nothing to do for blocks built from Wagtail's choosers
(`ImageChooserBlock`, `DocumentChooserBlock`, `PageChooserBlock`,
`SnippetChooserBlock` for the snippets in `SNIPPET_NATURAL_KEYS`) and
`RichTextBlock`, at any nesting: `_streamwalk.map_by_definition` handles them
for both commands. A snippet chooser for another model needs that model in
`SNIPPET_NATURAL_KEYS` (and an importer for it); until then its ids import
as empty.

---

## Limitations

- **Wagtail revisions** are not exported. Each imported page gets one new
  revision, published if the original was live.
- **Wagtail admin comments** and other rows tied to user accounts are not
  exported.
- **Unpublished drafts** are not exported: Wagtail keeps them only in
  revisions, and an archive carries each page's saved fields (for a live
  page, its published content).
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
