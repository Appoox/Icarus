"""
Production settings — the module the live site must run:

    DJANGO_SETTINGS_MODULE=Icarus.settings.production

Self-contained: everything secret comes from the environment via base.py;
nothing here depends on an untracked local.py.
"""
from .base import *  # noqa: F401,F403

DEBUG = False

# The mock gateway grants paid plans without payment. Never on production.
MOCK_PAYMENTS_ENABLED = False

# ── HTTPS behind Caddy ────────────────────────────────────────────────
# Caddy terminates TLS and always sets X-Forwarded-Proto (it overwrites
# any value a client sends), so Django can trust it to know a request
# arrived over HTTPS.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True

SILENCED_SYSTEM_CHECKS = [
    # W004 (HSTS) and W008 (SSL redirect): Caddy does both, for every
    # response including static and media files (see the Caddyfile). A
    # Django-side redirect could also bounce Caddy's internal forward_auth
    # sub-requests and deny every paywalled file.
    "security.W004",
    "security.W008",
    # W019: X_FRAME_OPTIONS is SAMEORIGIN on purpose — the archive viewer
    # frames our own PDFs.
    "security.W019",
]

# ManifestStaticFilesStorage is recommended in production, to prevent
# outdated JavaScript / CSS assets being served from cache
# (e.g. after a Wagtail upgrade).
# See https://docs.djangoproject.com/en/5.2/ref/contrib/staticfiles/#manifeststaticfilesstorage
# (A copy, not an in-place edit: base.STORAGES is shared by every module.)
STORAGES = {  # noqa: F405
    **STORAGES,  # noqa: F405
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.ManifestStaticFilesStorage",
    },
}
