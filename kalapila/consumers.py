import json
from channels.generic.websocket import AsyncWebsocketConsumer

class AdminNotificationConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        user = self.scope["user"]
        if user.is_authenticated and user.is_staff:
            self.group_name = "admin_notifications"
            await self.channel_layer.group_add(
                self.group_name,
                self.channel_name
            )
            await self.accept()
        else:
            await self.close()

    async def disconnect(self, close_code):
        if hasattr(self, 'group_name'):
            await self.channel_layer.group_discard(
                self.group_name,
                self.channel_name
            )

    async def send_notification(self, event):
        notification = event['notification']
        await self.send(text_data=json.dumps({
            'type': 'notification',
            'notification': notification
        }))

class PageCommentConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.page_id = self.scope['url_route']['kwargs']['page_id']
        self.group_name = f'page_comments_{self.page_id}'
        
        await self.channel_layer.group_add(
            self.group_name,
            self.channel_name
        )
        await self.accept()

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(
            self.group_name,
            self.channel_name
        )

    async def send_comment(self, event):
        html = event['html']
        parent_id = event.get('parent_id')
        await self.send(text_data=json.dumps({
            'type': 'new_comment',
            'html': html,
            'parent_id': parent_id,
            'comment_id': event.get('comment_id')
        }))
        
    async def delete_comment(self, event):
        comment_id = event['comment_id']
        await self.send(text_data=json.dumps({
            'type': 'delete_comment',
            'comment_id': comment_id,
            'deleted_by_author': event.get('deleted_by_author', False)
        }))
