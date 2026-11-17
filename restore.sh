#!/usr/bin/env bash
# restore.sh - bring back the off-server backups the backups app makes.
# Setup and the full runbook, including rebuilding a lost server: backups/README.md
#
# Runs on the server, from this checkout. It needs Docker, this repository
# and /opt/icarus/shared/rclone/rclone.conf; db, drill and media also need the
# server's .env. It never needs the site itself: it reads the remote with the
# backup-tools container (docker-compose.yml) and restores through the db
# container, so it works with the site down, or on a new server before the
# first deploy.
#
#   ./restore.sh list                         what is on the remote
#   ./restore.sh drill [SNAPSHOT]             restore into a scratch database, check it, drop it
#   ./restore.sh db [SNAPSHOT] [--keep-old]   replace the live database (after a safety dump)
#   ./restore.sh db --local FILE              ... from a dump in the backup volume
#   ./restore.sh media [--overwrite] [--from-archive SNAPSHOT]
#   ./restore.sh env [SNAPSHOT] > FILE        a snapshot's copy of the .env
#
# SNAPSHOT is an ID such as 20261116T210000Z (from `list` or the admin
# dashboard), or `latest`, the default. --yes answers the confirmation.

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

SHARED_DIR="/opt/icarus/shared"
RCLONE_CONFIG_DIR="$SHARED_DIR/rclone"
# The same pinned release as the backup-tools service and the app image.
RCLONE_IMAGE="$(grep -m1 -oE 'rclone/rclone:[0-9][0-9.]*' docker-compose.yml)"
SNAPSHOT_PATTERN='^[0-9]{8}T[0-9]{6}Z$'

ASSUME_YES=0
DB_NAME=""
DB_USER=""
SCRATCH_DB=""   # dropped on exit unless it became the live database
DOWNLOADED=""   # removed from the backup volume on exit
STOPPED=()      # services this script stopped; started again on exit

say()  { printf '\033[1m==>\033[0m %s\n' "$*" >&2; }
note() { printf '    %s\n' "$*" >&2; }
die()  { printf '\033[31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }
usage() { sed -n '2,/^$/s/^# \{0,1\}//p' "$0" >&2; exit "${1:-0}"; }

# ── Settings ────────────────────────────────────────────────────────────────

# Like deploy.sh: the real .env lives outside the checkout. Link it in when
# it exists, so `docker compose` reads the values the site runs with.
if [ ! -e .env ] && [ -f "$SHARED_DIR/.env" ]; then
    ln -s "$SHARED_DIR/.env" .env
fi

# A value from the .env, read rather than sourced: values such as
# DEFAULT_FROM_EMAIL=ശാസ്ത്രഗതി <noreply@…> are not valid shell.
env_value() {
    local line
    [ -f .env ] || return 0
    line="$(grep -E "^[[:space:]]*(export[[:space:]]+)?$1=" .env | tail -n 1 || true)"
    line="${line#*=}"
    line="${line%$'\r'}"
    if [[ "$line" == \"*\" || "$line" == \'*\' ]]; then
        line="${line:1:${#line}-2}"
    fi
    printf '%s' "$line"
}

# The environment wins over the .env (BACKUP_REMOTE=x ./restore.sh list).
setting() {
    if [ -n "${!1:-}" ]; then printf '%s' "${!1}"; else env_value "$1"; fi
}

BACKUP_REMOTE="$(setting BACKUP_REMOTE)"

# A path on the remote: `remote db` -> icarus-backup:db
remote() {
    [ -n "$BACKUP_REMOTE" ] \
        || die "BACKUP_REMOTE is not set. Put it in $SHARED_DIR/.env, or run e.g. BACKUP_REMOTE=icarus-backup: $0 …"
    case "$BACKUP_REMOTE" in
        *: | */) printf '%s%s' "$BACKUP_REMOTE" "$1" ;;
        *) printf '%s/%s' "$BACKUP_REMOTE" "$1" ;;
    esac
}

require_db_settings() {
    [ -f .env ] || die "No .env in this checkout or at $SHARED_DIR/.env. Restore it first: $0 env latest > $SHARED_DIR/.env"
    DB_NAME="$(setting POSTGRES_NAME)"
    DB_USER="$(setting POSTGRES_USER)"
    [[ "$DB_NAME" =~ ^[A-Za-z0-9_]+$ ]] || die "POSTGRES_NAME in .env is missing or not a plain name: '$DB_NAME'."
    [[ "$DB_USER" =~ ^[A-Za-z0-9_]+$ ]] || die "POSTGRES_USER in .env is missing or not a plain name: '$DB_USER'."
}

# ── Docker ──────────────────────────────────────────────────────────────────

# rclone with nothing but its config: enough for list and env, which must
# work on a server that has no .env yet.
rclone_cfg() {
    [ -f "$RCLONE_CONFIG_DIR/rclone.conf" ] \
        || die "$RCLONE_CONFIG_DIR/rclone.conf not found. Put back the copy from your password manager (backups/README.md)."
    docker run --rm -i --user 1000:1000 -v "$RCLONE_CONFIG_DIR:/config/rclone" "$RCLONE_IMAGE" "$@"
}

# rclone with the backup volume (/backups) and the media volume (/data/media).
rclone_vol() {
    docker compose run --rm -T backup-tools "$@"
}

running() { docker compose ps --status running --services 2>/dev/null | grep -qx "$1"; }

psql_db() {
    local database="$1"; shift
    docker compose exec -T -e PGOPTIONS='-c client_min_messages=warning' db \
        psql -X -q -v ON_ERROR_STOP=1 -U "$DB_USER" -d "$database" "$@"
}

psql_value() {
    local database="$1"; shift
    docker compose exec -T db psql -X -qAt -v ON_ERROR_STOP=1 -U "$DB_USER" -d "$database" "$@"
}

db_exists() {
    [ "$(psql_value postgres -c "SELECT 1 FROM pg_database WHERE datname = '$1'")" = "1" ]
}

ensure_db() {
    if ! running db; then
        say "Starting the database container …"
        docker compose up -d db >&2
    fi
    local _
    for _ in $(seq 1 90); do
        if docker compose exec -T db pg_isready -q -U "$DB_USER" -d postgres; then return 0; fi
        sleep 2
    done
    die "The database container did not become ready (docker compose logs db)."
}

# A volume first created by backup-tools (a new server, before qcluster ever
# ran) is root-owned; rclone runs as uid 1000, the app's wagtail user.
prepare_volumes() {
    docker compose run --rm -T --user 0:0 --entrypoint chown backup-tools 1000:1000 /backups /data/media >&2
}

on_exit() {
    local code=$?
    if [ -n "$SCRATCH_DB" ]; then
        psql_db postgres -c "DROP DATABASE IF EXISTS \"$SCRATCH_DB\" WITH (FORCE)" >/dev/null 2>&1 || true
    fi
    if [ "${#STOPPED[@]}" -gt 0 ]; then
        say "Starting ${STOPPED[*]} again …"
        docker compose start "${STOPPED[@]}" >&2 || true
    fi
    if [ -n "$DOWNLOADED" ]; then
        rclone_vol deletefile "$DOWNLOADED" >/dev/null 2>&1 || true
    fi
    exit "$code"
}
trap on_exit EXIT

confirm() {  # MESSAGE WORD
    [ "$ASSUME_YES" = 1 ] && return 0
    [ -t 0 ] || die "This needs a confirmation: run it in a terminal, or add --yes."
    printf '\n%s\nType %s to go ahead: ' "$1" "$2" >&2
    local answer
    read -r answer
    [ "$answer" = "$2" ] || die "Not confirmed. Nothing was changed."
}

# ── Snapshots ───────────────────────────────────────────────────────────────

# Complete snapshots, newest first. A dump counts once its .sha256 exists:
# the backup uploads that last.
snapshot_ids() {
    { rclone_cfg lsf --files-only "$(remote db)" || true; } \
        | sed -nE 's/^.+-([0-9]{8}T[0-9]{6}Z)\.dump\.sha256$/\1/p' | sort -r
}

resolve_snapshot() {
    local wanted="${1:-latest}" ids
    ids="$(snapshot_ids)"
    [ -n "$ids" ] || die "No complete snapshot on $BACKUP_REMOTE (or it could not be listed; see above)."
    if [ "$wanted" = latest ]; then
        head -n 1 <<<"$ids"
        return
    fi
    [[ "$wanted" =~ $SNAPSHOT_PATTERN ]] || die "'$wanted' is not a snapshot ID (like 20261116T210000Z) or 'latest'."
    grep -qx "$wanted" <<<"$ids" || die "No complete snapshot $wanted on $BACKUP_REMOTE; '$0 list' shows them."
    printf '%s\n' "$wanted"
}

# Download a snapshot's dump into the backup volume and check it against the
# checksum recorded at backup time. Prints its path in the volume.
fetch_dump() {
    local snapshot="$1" name expected actual
    name="$( { rclone_cfg lsf --files-only "$(remote db)" || true; } | grep -E -- "-$snapshot\.dump$" | head -n 1)"
    [ -n "$name" ] || die "Snapshot $snapshot has no dump file."
    rclone_vol delete /backups/restore >/dev/null 2>&1 || true
    say "Downloading $name from $BACKUP_REMOTE …"
    rclone_vol copyto "$(remote "db/$name")" "/backups/restore/$name" \
        --stats 20s --stats-one-line --stats-log-level NOTICE >&2
    expected="$(rclone_cfg cat "$(remote "db/$name.sha256")" | awk '{print $1}')"
    actual="$(rclone_vol hashsum sha256 "/backups/restore/$name" | awk '{print $1}')"
    if [ -z "$expected" ] || [ "$expected" != "$actual" ]; then
        die "$name does not match the checksum recorded at backup time ($actual, not $expected). Try an older snapshot."
    fi
    note "Checksum matches."
    printf '/backups/restore/%s\n' "$name"
}

check_archive() {
    # pg_restore --list stops reading after the table of contents, so the
    # writer may get SIGPIPE: only pg_restore's verdict counts here (the
    # checksum already vouched for the bytes).
    { rclone_vol cat "$1" 2>/dev/null || true; } \
        | docker compose exec -T db pg_restore --list >/dev/null \
        || die "$1 is not a readable pg_dump archive."
}

restore_into() {  # PATH DATABASE
    psql_db postgres \
        -c "DROP DATABASE IF EXISTS \"$2\" WITH (FORCE)" \
        -c "CREATE DATABASE \"$2\" TEMPLATE template0 OWNER \"$DB_USER\""
    # --no-owner/--no-privileges: one database role owns everything, and on a
    # new server roles the dump mentions may not exist.
    rclone_vol cat "$1" \
        | docker compose exec -T db pg_restore -U "$DB_USER" -d "$2" --no-owner --no-privileges --exit-on-error
}

# "TABLES PAGES LAST-MIGRATION-DATE" for a restored database.
describe_db() {
    psql_value "$1" -F ' ' -c "SELECT
        (SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'),
        (SELECT count(*) FROM wagtailcore_page),
        (SELECT max(applied)::date FROM django_migrations)"
}

# Swap the restored database in, atomically: both renames or neither.
swap_databases() {  # LIVE SCRATCH OLD-NAME
    local attempt
    for attempt in 1 2 3 4 5; do
        # Autovacuum likes freshly restored tables; end its sessions first.
        if psql_db postgres >/dev/null <<SQL
SELECT pg_terminate_backend(pid) FROM pg_stat_activity
 WHERE datname IN ('$1', '$2') AND pid <> pg_backend_pid();
BEGIN;
ALTER DATABASE "$1" RENAME TO "$3";
ALTER DATABASE "$2" RENAME TO "$1";
COMMIT;
SQL
        then
            return 0
        fi
        note "Something connected during the swap; trying again ($attempt/5) …"
        sleep 2
    done
    die "Could not swap the databases (something keeps connecting). The live database is unchanged."
}

# ── Commands ────────────────────────────────────────────────────────────────

cmd_list() {
    local listing envs name size snapshot taken
    listing="$(rclone_cfg lsf --files-only --format ps --separator ';' "$(remote db)" || true)"
    envs="$(rclone_cfg lsf --files-only "$(remote env)" 2>/dev/null || true)"
    say "Snapshots on $BACKUP_REMOTE, newest first:"
    printf '    %-18s  %-17s  %10s  %s\n' SNAPSHOT "TAKEN (UTC)" DATABASE .ENV >&2
    while IFS=';' read -r name size; do
        [[ "$name" =~ -([0-9]{8}T[0-9]{6}Z)\.dump$ ]] || continue
        snapshot="${BASH_REMATCH[1]}"
        grep -qF "$name.sha256;" <<<"$listing" || continue  # unfinished upload
        taken="${snapshot:0:4}-${snapshot:4:2}-${snapshot:6:2} ${snapshot:9:2}:${snapshot:11:2}"
        printf '    %-18s  %-17s  %10s  %s\n' "$snapshot" "$taken" \
            "$(numfmt --to=iec --suffix=B "$size")" \
            "$(grep -qx "$snapshot.env" <<<"$envs" && echo yes || echo -)"
    done < <(sort -t ';' -k1,1r <<<"$listing")

    local media archives local_dumps
    say "Media copy ($(remote media/current)):"
    media="$(rclone_cfg size --fast-list "$(remote media/current)" 2>/dev/null || true)"
    printf '%s\n' "${media:-(none yet)}" | sed 's/^/    /' >&2
    say "Media archives (files deleted or replaced on the server), newest first:"
    archives="$( { rclone_cfg lsf --dirs-only "$(remote media/archive)" 2>/dev/null || true; } | sed 's#/$##' | sort -r)"
    printf '%s\n' "${archives:-(none)}" | sed 's/^/    /' >&2
    if [ -f .env ]; then
        say "Dumps kept on this server (backup volume):"
        local_dumps="$( { rclone_vol lsf -R --files-only /backups 2>/dev/null || true; } | grep '\.dump$' || true)"
        printf '%s\n' "${local_dumps:-(none)}" | sed 's/^/    /' >&2
    fi
}

cmd_drill() {
    local snapshot path tables pages migrated
    require_db_settings
    snapshot="$(resolve_snapshot "${1:-latest}")"
    ensure_db
    prepare_volumes
    path="$(fetch_dump "$snapshot")"
    DOWNLOADED="$path"
    check_archive "$path"
    SCRATCH_DB="${DB_NAME}__drill"
    say "Restoring $snapshot into the scratch database $SCRATCH_DB; the live database is not touched …"
    restore_into "$path" "$SCRATCH_DB"
    read -r tables pages migrated <<<"$(describe_db "$SCRATCH_DB")"
    say "Drill passed: snapshot $snapshot restores ($tables tables, $pages pages, migrations up to $migrated)."
    note "The scratch database is dropped now; nothing else changed."
}

cmd_db() {
    local wanted="latest" local_file="" keep_old=0 path source tables pages migrated
    while [ $# -gt 0 ]; do
        case "$1" in
            --local) [ $# -ge 2 ] || die "--local needs a file, e.g. pre-restore/icarus-pre-restore-….dump"
                     local_file="$2"; shift 2 ;;
            --keep-old) keep_old=1; shift ;;
            -*) die "Unknown option: $1" ;;
            *) wanted="$1"; shift ;;
        esac
    done
    require_db_settings
    ensure_db
    prepare_volumes

    if [ -n "$local_file" ]; then
        [[ "$local_file" != *..* ]] || die "--local takes a path inside the backup volume."
        path="/backups/${local_file#/backups/}"
        rclone_vol lsf "$path" >/dev/null 2>&1 || die "$path is not in the backup volume ('$0 list' shows what is)."
        source="$path"
    else
        local snapshot
        snapshot="$(resolve_snapshot "$wanted")"
        path="$(fetch_dump "$snapshot")"
        DOWNLOADED="$path"
        source="snapshot $snapshot"
    fi
    check_archive "$path"

    SCRATCH_DB="${DB_NAME}__restore"
    say "Restoring $source into the scratch database $SCRATCH_DB; the site stays up meanwhile …"
    restore_into "$path" "$SCRATCH_DB"
    read -r tables pages migrated <<<"$(describe_db "$SCRATCH_DB")"
    note "It has $tables tables and $pages pages; migrations up to $migrated."

    confirm "Replace the live database '$DB_NAME' with $source?
The site goes offline for a minute or so, and a safety dump of the current database is kept." "$DB_NAME"

    local stamp safety="" old="" service
    stamp="$(date -u +%Y%m%dT%H%M%SZ)"
    for service in qcluster web; do
        if running "$service"; then
            docker compose stop "$service" >&2
            STOPPED+=("$service")
        fi
    done
    say "Site stopped; Caddy shows the maintenance page."

    if db_exists "$DB_NAME"; then
        safety="pre-restore/${DB_NAME}-pre-restore-${stamp}.dump"
        say "Saving the current database to the backup volume: $safety …"
        docker compose exec -T db pg_dump -U "$DB_USER" --format=custom "$DB_NAME" \
            | rclone_vol rcat "/backups/$safety"
        old="${DB_NAME}__before_${stamp}"
        say "Swapping the restored database in …"
        swap_databases "$DB_NAME" "$SCRATCH_DB" "$old"
    else
        psql_db postgres -c "ALTER DATABASE \"$SCRATCH_DB\" RENAME TO \"$DB_NAME\""
    fi
    SCRATCH_DB=""

    # PgBouncer's pooled connections belonged to the database it just lost.
    if running bouncer; then
        docker compose restart bouncer >&2
    fi
    if [ "${#STOPPED[@]}" -gt 0 ]; then
        say "Starting ${STOPPED[*]} …"
        docker compose start "${STOPPED[@]}" >&2
        STOPPED=()
    fi
    if [ -n "$old" ]; then
        if [ "$keep_old" = 1 ]; then
            note "The previous database is kept as $old (--keep-old); drop it when you are sure."
        else
            psql_db postgres -c "DROP DATABASE \"$old\" WITH (FORCE)"
        fi
    fi

    say "Done: the live database is now $source."
    if [ -n "$safety" ]; then
        note "To undo: $0 db --local $safety"
    fi
    note "If the snapshot is older than the code, web applies the newer migrations as it starts."
    note "Media is separate: '$0 media' copies back any files that are missing."
}

cmd_media() {
    local overwrite=0 archive="" source
    while [ $# -gt 0 ]; do
        case "$1" in
            --overwrite) overwrite=1; shift ;;
            --from-archive) [ $# -ge 2 ] || die "--from-archive needs a snapshot ID."
                            archive="$2"; shift 2 ;;
            *) die "Unknown argument: $1" ;;
        esac
    done
    [ -f .env ] || die "No .env in this checkout or at $SHARED_DIR/.env (docker compose needs it)."
    source="$(remote media/current)"
    if [ -n "$archive" ]; then
        [[ "$archive" =~ $SNAPSHOT_PATTERN ]] || die "'$archive' is not a snapshot ID."
        source="$(remote "media/archive/$archive")"
    fi
    if [ "$overwrite" = 1 ]; then
        confirm "Copy media from $source into the media volume, replacing files that differ from the backup?" "media"
    fi
    prepare_volumes

    local flags=(--transfers 8 --checkers 16 --fast-list --stats 30s --stats-one-line --stats-log-level NOTICE)
    if [ "$overwrite" = 0 ]; then
        # Only what is missing: never overwrite a file the server already has.
        flags+=(--ignore-existing)
    fi
    say "Copying media from $source …"
    rclone_vol copy "$source" /data/media "${flags[@]}" >&2
    say "Media restored from $source."
}

cmd_env() {
    local snapshot
    snapshot="$(resolve_snapshot "${1:-latest}")"
    if [ -t 1 ]; then
        die "This prints the server's secrets. Send them to a file: $0 env $snapshot > $SHARED_DIR/.env.restored"
    fi
    rclone_cfg cat "$(remote "env/$snapshot.env")" \
        || die "Snapshot $snapshot has no copy of the .env."
    say "Wrote the .env from snapshot $snapshot."
}

# ── Main ────────────────────────────────────────────────────────────────────

[ $# -gt 0 ] || usage 1
command="$1"
shift
args=()
for arg in "$@"; do
    case "$arg" in
        -y | --yes) ASSUME_YES=1 ;;
        -h | --help) usage 0 ;;
        *) args+=("$arg") ;;
    esac
done
set -- ${args[@]+"${args[@]}"}

command -v docker >/dev/null || die "Docker is not installed."
[ -n "$RCLONE_IMAGE" ] || die "Could not find the rclone image in docker-compose.yml."

case "$command" in
    list) cmd_list "$@" ;;
    drill) cmd_drill "$@" ;;
    db) cmd_db "$@" ;;
    media) cmd_media "$@" ;;
    env) cmd_env "$@" ;;
    help | -h | --help) usage 0 ;;
    *) usage 1 ;;
esac
