# Off-server backups

Every night the site sends an encrypted copy of itself to Google Drive:

| What | How | Kept |
|---|---|---|
| The database | `pg_dump` custom-format archive, checksummed | newest 30 snapshots + first of each of the last 12 months |
| Media (`MEDIA_ROOT`: images, issue PDFs, page images, audio…) | rsync-style mirror: only new and changed files are uploaded | one current copy; files deleted or replaced on the server stay in an archive for 30 days |
| The server's `.env` (the keys that decrypt readers' phone numbers and addresses) | copied into each snapshot | with its snapshot |

Not backed up, because they are rebuilt: static files (`collectstatic`), the ML models and the GeoIP
databases (downloaded again), Caddy's certificates (issued again), the Docker images and the code (git).

rclone does the copying. It is the rsync of cloud storage: `rclone sync` compares sizes and dates and
uploads only what changed, like `rsync -a --delete`, and `--backup-dir` keeps what it would have deleted,
like rsync's. (rsync itself can't write to Google Drive: it needs ssh or an rsync daemon at the other end.)
The rclone remote is a **crypt** remote wrapping Google Drive, so file names and contents are encrypted on
the server before they leave it. Google sees only sizes and dates.

## Where things run

- **qcluster** (the Django-Q worker) makes the backups: nightly at 02:30 IST, and whenever someone presses
  *Back up now*. Only this container has the rclone config (the Drive token and the encryption keys).
  Code: `backups/engine.py`.
- **The admin dashboard**, *Backups* in the Wagtail sidebar (`/admin/backups/`), starts backups,
  verifications and setup checks, shows their live log, the snapshots on Drive and the storage left.
  Superusers see it; anyone else needs **Can run and monitor off-server backups** (Settings → Groups).
  It has no restore button, on purpose: see [Security](#security).
- **`restore.sh`**, in the repository root, restores. It runs on the server, needs neither the site nor
  its database, and works on a brand-new server.
- **Alerts**: twice a day (08:45 and 20:45 IST) ADMIN_EMAILS get an email if the last complete backup is
  more than 26 hours old, backups are not set up, or the latest verification failed. Every Sunday at 04:00
  IST a verification downloads the newest snapshot and proves it reads back.

## Setting it up

Do this once, on the server, in the checkout `deploy.sh` runs from (the self-hosted runner's workspace).

1. **A Google account for the backups.** Use one that belongs to the organisation rather than to a
   person, with two-factor authentication on, and with room: the free 15 GB is shared with that account's
   Gmail and Photos. Roughly, you need the media folder's size, plus 40 database dumps, plus the media
   changed in a month. The dashboard shows what is used and free once it runs.

2. **Create the rclone config.** The folder must belong to uid 1000, the app's user, because rclone
   rewrites the file whenever it refreshes Google's access token:

   ```bash
   sudo install -d -o 1000 -g 1000 -m 700 /opt/icarus/shared/rclone
   ```

   ```bash
   docker compose run --rm backup-tools config
   ```

   Make two remotes:

   - `n` (new remote), name **`gdrive`**, storage **`drive`**. Leave `client_id` and `client_secret`
     empty, scope **`drive`**, leave `service_account_file` empty, no advanced config.
     *Use web browser to automatically authenticate?* **`n`**: the server has no browser. rclone prints
     an `rclone authorize "drive" "…"` command: run it on a computer with a browser and rclone installed
     ([rclone.org/downloads](https://rclone.org/downloads/)), sign in with the backup account, and paste
     the token it prints back into the server. Not a shared drive.
   - `n` again, name **`icarus-backup`**, storage **`crypt`**, remote **`gdrive:icarus-backups`**,
     `filename_encryption` **standard**, `directory_name_encryption` **true**. For both passwords choose
     **`g`** (generate), **256** bits, and keep them.
   - `q` to quit.

   If you use your own Google OAuth client ID instead of rclone's, set its publishing status to
   **In production** in the Google Cloud console. A client left in *Testing* has its tokens expire after
   7 days, and the backups stop (the alert email then tells you).

3. **Save `rclone.conf` in your password manager, now.** It holds the encryption passwords: without them
   nobody, you included, can read the backups, and they are not in the backups.

   ```bash
   sudo cat /opt/icarus/shared/rclone/rclone.conf
   ```

4. **Let the worker read the `.env`**, so each snapshot carries a copy:

   ```bash
   sudo chgrp 1000 /opt/icarus/shared/.env
   ```

   ```bash
   sudo chmod 640 /opt/icarus/shared/.env
   ```

5. **Point the site at the remote.** In `/opt/icarus/shared/.env` add `BACKUP_REMOTE=icarus-backup:`,
   and check `ADMIN_EMAILS` is set (the alerts go there). The other `BACKUP_*` settings are optional; see
   `.env.example`.

6. **Deploy** (merge to `master`). The web container registers the three schedules as it starts, and
   qcluster gets its new mounts.

7. **Check it.** In the admin, *Backups* → **Check setup**, then **Back up now**, then **Verify latest
   backup**. The same from the command line, with the full log in the terminal:

   ```bash
   docker compose exec qcluster python manage.py backup --check
   ```

   ```bash
   docker compose exec qcluster python manage.py backup
   ```

8. **Rehearse a restore**, and repeat every few months: it restores the newest snapshot into a scratch
   database, checks it and drops it, touching nothing live.

   ```bash
   ./restore.sh drill latest
   ```

If this server's crontab still runs the old `backup-db.sh`, remove that line: the script is gone, and the
nightly backup replaces it. Its dumps in `/opt/icarus/backups` can be deleted once a backup has reached
Drive.

## Restoring

`./restore.sh list` shows the snapshots on Drive, newest first; so does the dashboard, with the exact
command for each. A snapshot ID is a UTC time like `20261116T210000Z`; `latest` is the newest.

**The database**, e.g. after a bad migration or a mistake in the admin:

```bash
./restore.sh db latest
```

It downloads the snapshot, checks its checksum, and restores it into a scratch database while the site
stays up. Then it asks for confirmation, stops web and qcluster (Caddy shows the maintenance page), saves
a safety dump of the current database, swaps the restored one in (atomically: both renames happen or
neither does) and starts the site again. If anything fails, the site comes back on the old database.
To undo a restore, use the safety dump it names:

```bash
./restore.sh db --local pre-restore/icarus-pre-restore-20261116T093000Z.dump
```

If the snapshot is older than the code, web applies the newer migrations as it starts.

**Media files** that are missing on the server (existing files are never touched; `--overwrite` replaces
files that differ from the backup):

```bash
./restore.sh media
```

Files deleted or replaced on the server in the last 30 days are in the archive of the backup that noticed,
`./restore.sh list` shows which. To bring back what one night's backup archived:

```bash
./restore.sh media --from-archive 20261116T210000Z
```

**The `.env`** as it was at a snapshot (it refuses to print secrets to the terminal):

```bash
./restore.sh env latest > /opt/icarus/shared/.env.restored
```

### Rebuilding a lost server

1. A new server with Docker. Clone this repository (into the self-hosted runner's workspace if you set
   one up again) and `cd` into it.
2. Put back `rclone.conf` from the password manager:

   ```bash
   sudo install -d -o 1000 -g 1000 -m 700 /opt/icarus/shared/rclone
   ```

   ```bash
   sudo install -o 1000 -g 1000 -m 600 /dev/stdin /opt/icarus/shared/rclone/rclone.conf
   ```

   (paste the file, then Ctrl-D).
3. Get the `.env` back, read it over, and install it:

   ```bash
   BACKUP_REMOTE=icarus-backup: ./restore.sh env latest > env.restored
   ```

   ```bash
   sudo install -g 1000 -m 640 env.restored /opt/icarus/shared/.env && rm env.restored
   ```

4. The database (this starts the db container on its own), then the media:

   ```bash
   ./restore.sh db latest --yes
   ```

   ```bash
   ./restore.sh media
   ```

5. `./deploy.sh` builds the app and starts everything.
6. Point the DNS records at the new server; Caddy gets new certificates by itself.
7. In the admin, *Backups* → **Check setup** and **Back up now**.

### Reconnecting Google Drive

When a run fails with an expired or revoked token (a password change on the Google account does that):

```bash
docker compose run --rm backup-tools config reconnect gdrive:
```

Then update the copy of `rclone.conf` in the password manager.

## Troubleshooting

The dashboard shows each run's log and setup checks, and every failure says what to do. Also:

- **A run stays "Queued"**: the worker isn't running. `docker compose ps qcluster`, then
  `docker compose logs --tail 50 qcluster`.
- **"rclone.conf must be readable and writable by uid 1000"**: `sudo chown -R 1000:1000 /opt/icarus/shared/rclone`.
- **"pg_dump N cannot dump a PostgreSQL M server"**: the database image was upgraded; raise
  `postgresql-client-N` in the Dockerfile to match.
- **"The media upload ran out of time"**: the first upload of a large media folder can outlast one task
  (Django-Q stops tasks after `Q_CLUSTER['timeout']`). Nothing is lost; each night carries on where the
  last stopped. To finish it in one go, run it without a time limit:
  `docker compose exec qcluster python manage.py backup`.
- **Server disk**: the newest 3 dumps stay in the `backup_volume` Docker volume (`BACKUP_KEEP_LOCAL`),
  and a dump only starts with room to spare.
- **Drive storage**: pruned snapshots skip the Drive trash, so space comes back at once.

## Security

- Backups hold every reader's personal data, so an unencrypted remote is refused
  (`BACKUP_REQUIRE_ENCRYPTION`). rclone's crypt remote uses XSalsa20-Poly1305 with keys derived from the
  two passwords by scrypt.
- `rclone.conf` is mounted into qcluster only, never into web, which faces the internet.
- There is no restore button. Replacing the live database is not something the running app can safely
  do to itself, and a stolen admin session must not be able to roll the site back or wipe it. Restores
  need a shell on the server.
- Anyone with the Google account and `rclone.conf` can read the backups, and delete them. Keep both in
  the password manager and nowhere else; don't sign in to the backup account for anything else.
