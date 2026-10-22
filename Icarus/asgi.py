"""
ASGI config for Icarus project (HTTP + WebSockets via Channels).

For more information on this file, see
https://docs.djangoproject.com/en/5.2/howto/deployment/asgi/
"""

import os

# Settings must be chosen before anything imports Django models.
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Icarus.settings.production")

from django.core.asgi import get_asgi_application  # noqa: E402

# Initialise Django (apps registry, settings) before importing consumers.
django_asgi_app = get_asgi_application()

from channels.auth import AuthMiddlewareStack  # noqa: E402
from channels.routing import ProtocolTypeRouter, URLRouter  # noqa: E402
from channels.security.websocket import AllowedHostsOriginValidator  # noqa: E402

import kalapila.routing  # noqa: E402
import postbox.routing  # noqa: E402

application = ProtocolTypeRouter({
    "http": django_asgi_app,
    # Browsers send the session cookie with any cross-site WebSocket, so
    # only accept handshakes whose Origin is one of our ALLOWED_HOSTS.
    "websocket": AllowedHostsOriginValidator(
        AuthMiddlewareStack(
            URLRouter(
                kalapila.routing.websocket_urlpatterns
                + postbox.routing.websocket_urlpatterns
            )
        )
    ),
})
