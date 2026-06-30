from django.urls import path
from . import views

app_name = 'postbox'

urlpatterns = [
    path('', views.postbox_page, name='page'),
    # Admin notification endpoints (called by postbox_admin_notifications.js)
    path(
        'admin/notifications/',
        views.postbox_admin_notifications,
        name='admin_notifications',
    ),
    path(
        'admin/notifications/<int:pk>/read/',
        views.mark_postbox_notification_read,
        name='mark_notification_read',
    ),
]