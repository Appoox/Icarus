# Icarus — ശാസ്ത്രഗതി

The Wagtail site behind [sasthragathy.org](https://sasthragathy.org): the
magazine (issues, articles, authors), reader accounts and subscriptions,
comments (kalapila), feedback (postbox) and the searchable archive (the_librarian).

Stack: Python 3.12 · Django 5.2 LTS · Wagtail 7.4 · PostgreSQL 18 + pgvector ·
Django-Q2 (background jobs) · Channels (admin notifications) · Caddy · Docker Compose.

## Local development

1. PostgreSQL 18 with the `vector` extension, and Python 3.12.
2. `python -m venv .venv && . .venv/bin/activate && pip install -r requirements-dev.txt`
3. `cp .env.example .env` and fill in:
   - `SECRET_KEY`, `SALT_KEY`, `PHONE_HASH_KEY`. To work on a copy of the
     live database, use the live values: the encrypted phone numbers and
     addresses don't open with any others.
   - `DATABASE_URL`, `DEBUG=True`, `EMBEDDING_DIM=1024`
   - `MOCK_PAYMENTS_ENABLED=True` to try subscriptions without a payment gateway
4. Use the development settings. `manage.py` defaults to production, and the
   `.env` is read too late to change that, so set it in your shell:

   ```sh
   export DJANGO_SETTINGS_MODULE=Icarus.settings.dev
   python manage.py migrate
   python manage.py createcachetable
   python manage.py runserver
   ```

Background jobs (newsletter, text-to-speech, archive ingestion, retention
purges) run in a second terminal: `python manage.py qcluster`.

`runserver` doesn't serve WebSockets, so the admin's live comment
notifications don't appear under it. To try those, serve the same ASGI app as
production: `uvicorn Icarus.asgi:application --reload`. Restart it after
editing templates; `runserver` reloads them by itself.

Safe by default under the dev settings:
- **Email** is printed to the terminal, whatever `EMAIL_BACKEND` your `.env`
  holds, so a test newsletter or password reset can't reach real readers. Set
  `DEV_EMAIL_BACKEND` to send for real (e.g. to a local mail catcher).
- **Text-to-speech** is not: if your `.env` has `GOOGLE_CREDENTIALS_JSON`,
  publishing an article while `qcluster` runs calls the paid Google API.
  Leave it out unless you're working on TTS.

## Tests

```sh
python manage.py test --settings=Icarus.settings.test
coverage run manage.py test --settings=Icarus.settings.test && coverage report
```

`Icarus.settings.test` needs no secrets, and ignores your `.env` except for
`DATABASE_URL` (default `postgres://icarus:icarus@localhost:5432/icarus`).
The tests create a `test_<name>` database beside yours, so that database user
needs `CREATEDB`. If creating it fails on `CREATE EXTENSION vector`, enable
pgvector in the template database once, as a superuser:
`psql -d template1 -c 'CREATE EXTENSION IF NOT EXISTS vector'`.
CI (`.github/workflows/ci.yml`) runs the suite, the migrations check, the
coverage floor in `.coveragerc` and `check --deploy` on every push and pull
request to `development`.

## Deploying

Every push to `master` deploys: `.github/workflows/deploy.yml` runs
`deploy.sh` (build, `docker compose up -d`, Caddy reload) on the server's
self-hosted runner. The tests (`ci.yml`) run on pushes to `master` too, but
don't hold a deploy back, so check they're green before merging into `master`.

The live site must run `Icarus.settings.production`. Secrets come from
`/opt/icarus/shared/.env` (see `.env.example`).

### Never change these on a live site

| Variable | Why |
|---|---|
| `SECRET_KEY`, `SALT_KEY` | Together they derive the key that encrypts readers' phone numbers and addresses. To rotate `SECRET_KEY`, move the old value into `SECRET_KEY_FALLBACKS`. |
| `PHONE_HASH_KEY` | Every reader logs in through this HMAC of their phone number. |

### One-off steps after upgrading to this release

1. Make sure the server's `.env` has `SECRET_KEY`, `SALT_KEY`, `PHONE_HASH_KEY`
   (unchanged) and `DJANGO_SETTINGS_MODULE=Icarus.settings.production` (or no
   value — production is the default now). `FIELD_ENCRYPTION_KEY` is no longer used.
2. Set the `EMAIL_*` variables; otherwise password-reset and newsletter mail is
   only printed to the container log.
3. Set `EMBEDDING_DIM` to match the archive's vector column (1024 in the
   migrations; `manage.py check` warns with `the_librarian.W001` if they differ).
4. `python manage.py reset_author_passwords --dry-run`, then
   `python manage.py reset_author_passwords --email` — disables the guessable
   passwords older author accounts were given.
5. `/reader/process-payment/` is now off (404): the mock gateway handed out
   paid plans for free. Readers can't buy plans until a real gateway is added.
6. `python manage.py wagtail_update_image_renditions` — remakes every resized
   image. Wagtail only uses `WAGTAILIMAGES_FORMAT_CONVERSIONS` (WebP) for new
   renditions, so the ones made before it was set are still PNG or JPEG, some
   of them CMYK with a 544 KB print colour profile, often 10–40× the size of
   the WebP version. Run it once, at a quiet time: it takes a while and uses a
   lot of CPU.

Run automatically at container start: `migrate`, `createcachetable`,
`setup_page_lock_schedule`, `setup_geoip_schedule`, `setup_retention_schedules`.

## Useful commands

| Command | What it does |
|---|---|
| `send_newsletter --issue-id N [--dry-run]` | (Re)send an issue's newsletter by hand. |
| `purge_deactivated_users` / `purge_expired_data` | DPDP retention purges (scheduled). |
| `reset_author_passwords [--dry-run] [--email]` | See upgrade step 4. |
| `export_content` / `import_content` / `check_archive` | Content export and import; see `home/management/commands/README.md`. |
| `update_geoip` | Refresh the offline GeoIP databases. |
