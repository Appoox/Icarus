#!/usr/bin/env bash
# deploy.sh - run by the self-hosted GitHub Actions runner after every push
# to master.
#
# Two things this script deliberately guarantees:
#   1. No manual "enter the env" step - the real .env lives outside the git
#      workspace and gets re-linked automatically every run.
#   2. No data loss - it only ever rebuilds/restarts the `web` (and `qcluster`)
#      services. `db`, `bouncer`, and `caddy` (and therefore pgdata /
#      media_volume) are never stopped, recreated, or passed to any
#      `-v`-flagged command.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

# --- 1. .env, with no manual entry --------------------------------------
# Real secrets live outside the git-managed workspace (created once, by
# hand) so that GitHub Actions' checkout step - which runs `git clean` by
# default and would happily delete an untracked/.gitignored .env sitting
# inside the repo - can never touch it. Re-linking it every run is cheap,
# idempotent, and requires no human interaction.
#
# This symlink is all that's needed: Docker Compose automatically reads a
# file named `.env` in this directory for its own ${VAR} substitution, and
# the `web`/`qcluster` services' `env_file:` entries load the same file into
# the container's actual runtime environment. No manual `source`/export step -
# that would only matter for build-time secrets, and collectstatic no
# longer runs at build time (see Dockerfile), so the build needs none.
ENV_SOURCE="/opt/icarus/shared/.env"
if [ ! -f "$ENV_SOURCE" ]; then
    echo "ERROR: $ENV_SOURCE not found. Create it once by hand (see README.md), then re-run." >&2
    exit 1
fi
ln -sf "$ENV_SOURCE" "$PROJECT_DIR/.env"

# --- 2. Rebuild + restart ONLY the app containers -----------------------
# Deliberately `build web` / `up -d --no-deps web qcluster`, never a bare
# `docker compose up -d --build` (which would recreate db/bouncer/caddy too)
# and never anything with `-v` (which would delete pgdata / media_volume).
#
# `qcluster` reuses the image built for `web` (image: icarus-web:latest in
# docker-compose.yml), so a single `build web` produces the image both
# services run, and `up` recreates both from it.
#
# Recreating the `web` container re-runs its CMD from scratch, so
# collectstatic -> migrate -> setup_page_lock_schedule -> gunicorn all execute
# fresh - collectstatic writes into the mounted static_volume on every deploy,
# so this is now genuinely true (it wasn't when collectstatic ran at build).
docker compose build web
docker compose up -d --no-deps 

# --- 3. Housekeeping ------------------------------------------------------
# Only removes dangling (untagged) images left over from old builds.
# Never touches named volumes.
docker image prune -f

echo "Deploy complete: $(git rev-parse --short HEAD)"