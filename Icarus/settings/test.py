"""
Settings for the automated test suite.

    python manage.py test --settings=Icarus.settings.test

Self-contained: no production secret is needed, and nothing in your .env
changes what the tests check — base.py reads no .env under these settings.
The one exception is DATABASE_URL: when it isn't set in the environment, it
comes from your .env, so the tests use the same PostgreSQL as the dev server
(they create and drop a test_<name> database beside yours). That server needs
the pgvector extension (see .github/workflows/ci.yml).
"""
import os
import tempfile
from pathlib import Path


def _nearest_dotenv():
    """The .env base.py would read: the nearest one from this folder upwards."""
    for folder in Path(__file__).resolve().parents:
        if (folder / ".env").is_file():
            return str(folder / ".env")
    return None


_dotenv = os.environ.get("ICARUS_DOTENV")
if _dotenv is None:
    _dotenv = _nearest_dotenv()
if "DATABASE_URL" not in os.environ and _dotenv and os.path.isfile(_dotenv):
    from dotenv import dotenv_values

    _url = dotenv_values(_dotenv).get("DATABASE_URL")
    if _url:
        os.environ["DATABASE_URL"] = _url
os.environ["ICARUS_DOTENV"] = ""  # base.py: read no .env

# base.py reads these at import time, so they must exist before the import.
# Throwaway values — they only ever touch the throwaway test database.
os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production")
os.environ.setdefault("SALT_KEY", "test-salt-key-not-secret")
os.environ.setdefault("PHONE_HASH_KEY", "test-phone-hash-key")
os.environ.setdefault(
    "DATABASE_URL", "postgres://icarus:icarus@localhost:5432/icarus"
)

from .base import *  # noqa: E402,F401,F403

DEBUG = False

BASE_URL = "http://testserver"
WAGTAILADMIN_BASE_URL = BASE_URL
MOCK_PAYMENTS_ENABLED = False  # tests opt in with override_settings

ALLOWED_HOSTS = ["testserver", "localhost", "127.0.0.1"]

# Fast, deterministic auth for tests.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
AXES_ENABLED = False

# Nothing leaves the process.
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}

# Uploaded files go to a throwaway directory, never the real media/.
MEDIA_ROOT = tempfile.mkdtemp(prefix="icarus-test-media-")

# Queued tasks are written to the ORM broker and never executed, so saving
# an Article does not call Google TTS. Tests that care patch async_task.
Q_CLUSTER = {**Q_CLUSTER, "name": "IcarusTest", "workers": 1}  # noqa: F405

# The test client has no User-Agent; don't 403 it as a crawler.
AI_CRAWLER_BLOCK_ENABLED = False

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"null": {"class": "logging.NullHandler"}},
    "root": {"handlers": ["null"], "level": "CRITICAL"},
}

# Match the committed schema (the_librarian 0001: vector(1024)) so the model
# state equals the migrations. base.py reads this from EMBEDDING_DIM.
LIBRARIAN_EMBEDDING_DIM = 1024
