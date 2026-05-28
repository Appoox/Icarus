from django.contrib import messages
from django.utils.deprecation import MiddlewareMixin
from django.utils.safestring import mark_safe  # CRITICAL IMPORT: Fixes raw HTML string-escaping behavior
from django.db.models import Q
from .models import UserNotification


class UserNotificationMiddleware(MiddlewareMixin):
    # Only re-show sticky warnings after this many requests (avoids every-page spam)
    _WARN_INTERVAL = 10

    def process_request(self, request):
        # ── Check 1: Anonymous Session Guard ───────────────────────────────
        if not request.user.is_authenticated:
            return

        # ── Check 2: Administrative Dashboard & Asset Protection ───────────
        # Halts frontend reader alerts from executing on admin backend paths,
        # static assets, media paths, or background AJAX requests.
        path = request.path
        if (
            path.startswith('/admin/') or 
            path.startswith('/django-admin/') or
            path.startswith('/static/') or 
            path.startswith('/media/') or
            request.headers.get('x-requested-with') == 'XMLHttpRequest'
        ):
            return

        # ── 1. Unread comment status notifications (fire every time) ──────
        unread = UserNotification.objects.filter(user=request.user, is_read=False)
        unread_list = list(unread)
        if unread_list:
            # Inspect pending queue to prevent duplicate notifications from double-renders
            storage = messages.get_messages(request)
            pending_messages = [msg.message for msg in storage]
            storage.used = False  # Reset storage iterator so messages still render in templates

            for notification in unread_list:
                safe_msg = mark_safe(notification.message)
                if safe_msg not in pending_messages:
                    messages.info(request, safe_msg)
            
            ids = [n.pk for n in unread_list]
            UserNotification.objects.filter(pk__in=ids).update(is_read=True)

        # ── 2. Sticky account warnings (throttled via session counter) ────
        session = request.session
        hit = session.get('_warn_hit', 0) + 1
        session['_warn_hit'] = hit

        # Show warnings on the first load and every _WARN_INTERVAL requests after
        if hit == 1 or hit % self._WARN_INTERVAL == 0:
            self._add_account_warnings(request)

    def _add_account_warnings(self, request):
        user = request.user

        # Fetch the pending messages to inspect for duplicates
        storage = messages.get_messages(request)
        pending_messages = [msg.message for msg in storage]
        storage.used = False  # Reset storage iterator so messages still render in templates

        # Profile incomplete
        if hasattr(user, 'is_profile_complete') and not user.is_profile_complete:
            warn_msg = mark_safe(
                '👋 Your profile is incomplete. '
                '<a href="/reader/profile/edit/" style="font-weight:700;text-decoration:underline;">Complete it now</a>'
                ' to help us serve you better.'
            )
            # ONLY add the message if it is not already sitting in the queue
            if warn_msg not in pending_messages:
                messages.warning(request, warn_msg)

        # Grace period
        if hasattr(user, 'is_in_grace_period') and user.is_in_grace_period:
            grace_msg = mark_safe(
                '⚠️ Your subscription expired recently. You\'re in a grace period — '
                '<a href="/reader/profile/" style="font-weight:700;text-decoration:underline;">renew now</a>'
                ' to keep your...'
            )
            # ONLY add the message if it is not already sitting in the queue
            if grace_msg not in pending_messages:
                messages.warning(request, grace_msg)