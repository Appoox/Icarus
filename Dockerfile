# Use an official Python runtime based on Debian 12 "bookworm" as a parent image.
FROM python:3.12-slim-bookworm AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    libmariadb-dev \
    libjpeg62-turbo-dev \
    zlib1g-dev \
    libwebp-dev \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv
COPY requirements.txt .

RUN uv pip install --system \
    --no-cache-dir \
    --target=/install \
    --index-strategy unsafe-best-match \
    -r requirements.txt

FROM python:3.12-slim-bookworm

RUN useradd -m wagtail

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/install \
    PATH="/install/bin:$PATH"


RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    libpq5 \
    libmariadb3 \
    libjpeg62-turbo \
    libwebp7 \
    weasyprint \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /install /install
COPY --from=builder /usr/local/bin /usr/local/bin

WORKDIR /Icarus
COPY --chown=wagtail:wagtail . .

# static/ and media/ are the only runtime-writable dirs, and both are backed by
# named volumes in docker-compose.yml. Create them and give wagtail ownership
# BEFORE dropping privileges. On a freshly-created (empty) named volume, Docker
# copies this ownership onto the volume; an already-existing root-owned volume
# must be chowned once out-of-band (see deploy notes).
RUN mkdir -p /Icarus/static /Icarus/media \
    && chown -R wagtail:wagtail /Icarus/static /Icarus/media

USER wagtail

# collectstatic stays at runtime on purpose: static_volume is served read-only
# by Caddy, and running it on boot keeps the volume refreshed after each rebuild.
CMD set -e; \
    python manage.py collectstatic --noinput; \
    python manage.py migrate --noinput; \
    python manage.py setup_page_lock_schedule; \
    gunicorn -b 0.0.0.0:8000 Icarus.asgi:application -k uvicorn_worker.UvicornWorker

EXPOSE 8000

# # Add user that will be used in the container.
# RUN useradd wagtail

# # Port used by this container to serve HTTP.
# EXPOSE 8000

# # Set environment variables.
# # 1. Force Python stdout and stderr streams to be unbuffered.
# # 2. Set PORT variable that is used by Gunicorn. This should match "EXPOSE"
# #    command.
# ENV PYTHONUNBUFFERED=1 \
#     PYTHONDONTWRITEBYTECODE=1 \
#     PORT=8000

# # Install system packages required by Wagtail and Django.
# RUN apt-get update --yes --quiet && apt-get install --yes --quiet --no-install-recommends \
#     curl \
#     ca-certificates \
#     build-essential \
#     libpq-dev \
#     libmariadb-dev \
#     libjpeg62-turbo-dev \
#     zlib1g-dev \
#     libwebp-dev \
#     weasyprint \
#     # libgobject-2.0-0 \
#     # uv \
#     && rm -rf /var/lib/apt/lists/*

# # RUN curl -LsSf https://astral.sh/uv/install.sh | sh
# ADD https://astral.sh/uv/install.sh /uv-installer.sh
# RUN sh /uv-installer.sh && rm /uv-installer.sh
# ENV PATH="/root/.local/bin/:$PATH"

# ENV UV_LINK_MODE=copy
# ENV UV_PYTHON_CACHE_DIR=/root/.cache/uv/python
# RUN --mount=type=cache,target=/root/.cache/uv 

# # uv sync

# # Install the application server.
# # RUN uv pip install "gunicorn==20.0.4"

# # Install the project requirements.
# COPY requirements.txt .
# RUN uv pip install --system --no-cache-dir -r requirements.txt

# # Use /app folder as a directory where the source code is stored.
# WORKDIR /Icarus

# # Set this directory to be owned by the "wagtail" user. This Wagtail project
# # uses SQLite, the folder needs to be owned by the user that
# # will be writing to the database file.
# RUN chown wagtail:wagtail /Icarus

# # Copy the source code of the project into the container.
# COPY --chown=wagtail:wagtail . .

# # Use user "wagtail" to run the build commands below and the server itself.
# USER wagtail

# # Set up the audio storage directory on the host volume.
# # RUN mkdir -p /Icarus/media
# # RUN chown wagtail:wagtail /Icarus/media

# # COPY Icarus/media /Icarus/media



# # Cron Job for article locking
# # RUN python manage.py setup_page_lock_schedule

# # Collect static files.
# RUN python manage.py collectstatic --noinput --clear

# # Runtime command that executes when "docker run" is called, it does the
# # following:
# #   1. Migrate the database.
# #   2. Start the application server.
# # WARNING:
# #   Migrating database at the same time as starting the server IS NOT THE BEST
# #   PRACTICE. The database should be migrated manually or using the release
# #   phase facilities of your hosting platform. This is used only so the
# #   Wagtail instance can be started with a simple "docker run" command.
# CMD set -xe; \
#     # python manage.py makemigrations --noinput; \
#     python manage.py migrate --noinput; \
#     python manage.py setup_page_lock_schedule; \
#     gunicorn -b 0.0.0.0:8000 Icarus.asgi:application -k uvicorn.workers.UvicornWorker -w 3