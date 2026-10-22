"""
Local development settings.

    DJANGO_SETTINGS_MODULE=Icarus.settings.dev python manage.py runserver

Secrets come from .env exactly as in production (see .env.example); this
module only relaxes what a laptop needs, and keeps mail on this machine.
Set DEBUG=True in your .env.
Never point a public server at this module — use Icarus.settings.production.
"""
from .base import *  # noqa: F401,F403
from .base import env

# Local hosts on top of the production list, so a server accidentally left
# on this module keeps working instead of answering every Host header ("*").
ALLOWED_HOSTS = env.list(
    "ALLOWED_HOSTS",
    default=["localhost", "127.0.0.1", "[::1]"] + ALLOWED_HOSTS,  # noqa: F405
)
BASE_URL = env.str("BASE_URL", default="http://localhost:8000").rstrip("/")
WAGTAILADMIN_BASE_URL = env.str("WAGTAILADMIN_BASE_URL", default=BASE_URL)

# Mail sent in development is printed to the terminal, whatever EMAIL_BACKEND
# your .env holds: with a copy of the live database, a test newsletter or
# password reset would otherwise reach real readers. To really send (to a
# local mail catcher, say), set DEV_EMAIL_BACKEND.
EMAIL_BACKEND = env.str(
    "DEV_EMAIL_BACKEND", default="django.core.mail.backends.console.EmailBackend"
)

try:
    from .local import *  # type: ignore  # noqa: F401,F403
except ImportError:
    pass
