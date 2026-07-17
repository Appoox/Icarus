#!/usr/bin/env bash
# deploy.sh - run by the self-hosted GitHub Actions runner after every push
# to master.
#
# Two things this script deliberately guarantees:
#   1. No manual "enter the env" step - the real .env lives outside the git
#      workspace and gets re-linked automatically every run.
#   2. No data loss - it NEVER runs `docker compose down` and NEVER passes `-v`
#      to any command, so the named volumes (pgdata, media, caddy certs) are
#      never removed. `docker compose up -d` only (re)creates containers whose
#      image or config actually changed; on a normal deploy that is just `web`
#      and `qcluster` (their image was rebuilt), leaving db/bouncer/caddy
#      running untouched.
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

# --- 2. Build the app image, then bring up the whole stack --------------
# This previously used `up -d --no-deps web qcluster`. That only works when
# db/bouncer/caddy are ALREADY running: on a FRESH host, --no-deps skips
# starting those dependencies, so web comes up with no database (crash-loop)
# and there is no caddy to serve traffic. That is the fresh-start failure.
#
# Plain `up -d` fixes it. On a fresh host it honours depends_on + healthchecks
# (db healthy -> bouncer -> web -> caddy, plus qcluster) and creates everything
# that is not yet running. On a redeploy it stays surgical: compose only
# recreates containers whose image/config changed - i.e. web + qcluster, whose
# image we just rebuilt - and leaves db/bouncer/caddy alone. There is still no
# `-v` and no `down`, so the data-safety guarantee above holds in both cases.
#
# `qcluster` reuses the image built for `web` (image: icarus-web:latest in
# docker-compose.yml), so one `build web` produces the image both services run.
docker compose build web
docker compose up -d

# --- 3. Reload Caddy config ----------------------------------------------
# The Caddyfile is a bind mount, and bind-mount CONTENTS are not part of
# Compose's container config hash — so a commit that only edits the Caddyfile
# does NOT cause `up -d` to recreate caddy, and the change would silently
# never take effect. `caddy reload` fixes that: it validates the new config
# first and applies it with zero downtime (on an invalid Caddyfile it errors
# out — failing this deploy loudly — while caddy keeps serving the old,
# known-good config). Idempotent when nothing changed, and harmless right
# after a recreate, so it runs unconditionally on every deploy.
docker compose exec -T caddy caddy reload --config /etc/caddy/Caddyfile

# --- 4. Housekeeping ------------------------------------------------------
# Only removes dangling (untagged) images left over from old builds.
# Never touches named volumes.
docker image prune -f

echo "Deploy complete: $(git rev-parse --short HEAD)"