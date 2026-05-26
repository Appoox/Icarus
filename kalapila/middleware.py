from django.contrib import messages
from django.utils.deprecation import MiddlewareMixin
from django.utils.safestring import mark_safe  # CRITICAL IMPORT: Fixes raw HTML string-escaping behavior
from .models import UserNotification


class UserNotificationMiddleware(MiddlewareMixin):
    # Only re-show sticky warnings after this many requests (avoids every-page spam)
    _WARN_INTERVAL = 10

    def process_request(self, request):
        # ── Check 1: Anonymous Session Guard ───────────────────────────────
        # Skip operations entirely if the visitor context is unauthenticated.
        if not request.user.is_authenticated:
            return

        # ── Check 2: Administrative Dashboard Path Protection ──────────────
        # CRITICAL FIX: This conditional early exit ensures frontend reader alerts 
        # (like profile completion or billing syncs) do not bleed into Wagtail's 
        # admin backend namespace. This halts continuous message-stacking loop cycles.
        if request.path.startswith('/admin/'):
            return

        # ── 1. Unread comment status notifications (fire every time) ──────
        unread = UserNotification.objects.filter(user=request.user, is_read=False)
        unread_list = list(unread)
        if unread_list:
            for notification in unread_list:
                # Render content safely using mark_safe to allow clean HTML rendering
                messages.info(request, mark_safe(notification.message))
            ids = [n.pk for n in unread_list]
            UserNotification.objects.filter(pk__in=ids).update(is_read=True)

        # ── 2. Sticky account warnings (throttled via session counter) ────
        # Increment a hit counter so we don't spam on every page
        session = request.session
        hit = session.get('_warn_hit', 0) + 1
        session['_warn_hit'] = hit

        # Show warnings on the first load and every _WARN_INTERVAL requests after
        if hit == 1 or hit % self._WARN_INTERVAL == 0:
            self._add_account_warnings(request)

    def _add_account_warnings(self, request):
        user = request.user

        # Profile incomplete
        if hasattr(user, 'is_profile_complete') and not user.is_profile_complete:
            # CRITICAL FIX: Wrapped with mark_safe() to ensure the anchor tag is parsed
            # natively by the browser interface instead of escaping as raw string text.
            messages.warning(
                request,
                mark_safe(
                    '👋 Your profile is incomplete. '
                    '<a href="/reader/profile/edit/" style="font-weight:700;text-decoration:underline;">Complete it now</a>'
                    ' to help us serve you better.'
                )
            )

        # Grace period
        if hasattr(user, 'is_in_grace_period') and user.is_in_grace_period:
            # CRITICAL FIX: Wrapped with mark_safe() to render the inline link cleanly.
            messages.warning(
                request,
                mark_safe(
                    '⚠️ Your subscription expired recently. You\'re in a grace period — '
                    '<a href="/reader/profile/" style="font-weight:700;text-decoration:underline;">renew now</a>'
                    ' to keep your...'
                )
            )