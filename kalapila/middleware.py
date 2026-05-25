from django.contrib import messages
from django.utils.deprecation import MiddlewareMixin
from .models import UserNotification


class UserNotificationMiddleware(MiddlewareMixin):
    def process_request(self, request):
        if not request.user.is_authenticated:
            return

        unread = UserNotification.objects.filter(
            user=request.user,
            is_read=False
        )

        # Fetch once into a list — avoids re-querying inside the loop
        unread_list = list(unread)
        if not unread_list:
            return

        for notification in unread_list:
            messages.info(request, notification.message)

        # Single bulk UPDATE instead of N individual saves
        ids = [n.pk for n in unread_list]
        UserNotification.objects.filter(pk__in=ids).update(is_read=True)