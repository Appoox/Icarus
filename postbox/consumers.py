import json
from channels.generic.websocket import AsyncWebsocketConsumer


class PostboxAdminNotificationConsumer(AsyncWebsocketConsumer):
    """
    Pushes real-time postbox notification toasts to staff members.
    Mirrors AdminNotificationConsumer in kalapila but uses a separate
    channel group so the two systems don't cross-contaminate.
    """

    async def connect(self):
        user = self.scope["user"]
        if user.is_authenticated and user.is_staff:
            self.group_name = "admin_postbox_notifications"
            await self.channel_layer.group_add(
                self.group_name,
                self.channel_name,
            )
            await self.accept()
        else:
            await self.close()

    async def disconnect(self, close_code):
        if hasattr(self, 'group_name'):
            await self.channel_layer.group_discard(
                self.group_name,
                self.channel_name,
            )

    # Called by channel_layer.group_send(type="send_postbox_notification")
    async def send_postbox_notification(self, event):
        await self.send(text_data=json.dumps({
            "type":         "notification",
            "notification": event["notification"],
        }))