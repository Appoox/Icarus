"""
WSGI config for Icarus project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.2/howto/deployment/wsgi/
"""

import os

from django.core.asgi import get_asgi_application
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.auth import AuthMiddlewareStack
import kalapila.routing
import postbox.routing

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "Icarus.settings.dev")

application = ProtocolTypeRouter({
    "http": get_asgi_application(),
    "websocket": AuthMiddlewareStack(
        URLRouter(
            kalapila.routing.websocket_urlpatterns + 
            postbox.routing.websocket_urlpatterns
        )
    ),
})
