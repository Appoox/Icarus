from django.urls import re_path
from . import consumers

websocket_urlpatterns = [
    re_path(
        r'ws/postbox/admin/notifications/$',
        consumers.PostboxAdminNotificationConsumer.as_asgi(),
    ),
]