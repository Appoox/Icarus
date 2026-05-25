from django.contrib import messages
from django.utils.deprecation import MiddlewareMixin
from .models import UserNotification

class UserNotificationMiddleware(MiddlewareMixin):
    def process_request(self, request):
        if request.user.is_authenticated:
            # Query unread notifications for the current logged-in user
            unread_notifications = UserNotification.objects.filter(
                user=request.user,
                is_read=False
            )
            for notification in unread_notifications:
                # Add notification message to Django's messaging framework
                messages.info(request, notification.message)
                # Mark notification as read
                notification.is_read = True
                notification.save()
