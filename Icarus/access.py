"""
Who may read the paywalled archive — one rule for every path to it.

The same PDFs and page images are reachable through Caddy (forward_auth →
protected_media.py), the librarian viewer and download views, search
results and the Article/Issue PDF routes. They must all agree, so they
all ask may_read_archive().
"""
from functools import wraps

from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied


def may_read_archive(user):
    """Active subscribers, staff and superusers."""
    if not getattr(user, "is_authenticated", False):
        return False
    return bool(
        user.is_staff
        or user.is_superuser
        or getattr(user, "is_subscribed", False)
    )


def archive_access_required(view_func):
    """
    View decorator: anonymous visitors are sent to the login page; signed-in
    readers without a subscription get the 403 page (which links to plans).
    """
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        if not may_read_archive(request.user):
            raise PermissionDenied("Archive access requires a subscription.")
        return view_func(request, *args, **kwargs)

    return _wrapped
