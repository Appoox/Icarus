from django.urls import re_path

from . import consumers

websocket_urlpatterns = [
    re_path(r'ws/kalapila/admin/notifications/$', consumers.AdminNotificationConsumer.as_asgi()),
    re_path(r'ws/kalapila/page/(?P<page_id>\d+)/comments/$', consumers.PageCommentConsumer.as_asgi()),
]
