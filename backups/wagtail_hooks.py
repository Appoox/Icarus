"""Wagtail admin wiring for the backups dashboard."""
from django.contrib.auth.models import Permission
from django.urls import path, reverse
from wagtail import hooks
from wagtail.admin.menu import MenuItem

from backups import views
from backups.permissions import can_manage_backups


@hooks.register("register_admin_urls")
def register_backup_urls():
    # Wagtail wraps these in its admin login + access_admin check; each view
    # then checks backups.manage_backups itself.
    return [
        path("backups/", views.dashboard, name="backups_dashboard"),
        path("backups/start/<str:kind>/", views.start, name="backups_start"),
        path("backups/runs/<int:pk>/", views.run_detail, name="backups_run"),
        path("backups/status/", views.status, name="backups_status"),
    ]


class _BackupsMenuItem(MenuItem):
    """Hidden from users the dashboard would 403, like the other ops pages."""

    def is_shown(self, request):
        return can_manage_backups(request.user)


@hooks.register("register_admin_menu_item")
def register_backups_menu_item():
    return _BackupsMenuItem("Backups", reverse("backups_dashboard"), icon_name="history", order=912)


@hooks.register("register_permissions")
def register_backup_permissions():
    """Surface 'Can run and monitor off-server backups' in Settings -> Groups."""
    return Permission.objects.filter(content_type__app_label="backups", codename="manage_backups")
