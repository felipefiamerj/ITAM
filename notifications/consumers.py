"""Channels consumer for live notification updates."""

from channels.generic.websocket import AsyncJsonWebsocketConsumer

from .realtime import notification_group_name


class NotificationConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        user = self.scope.get('user')
        if not user or not user.is_authenticated or not getattr(user, 'is_active', True):
            await self.close(code=4401)
            return

        self.user_id = user.pk
        self.group_name = notification_group_name(self.user_id)
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, code):
        group_name = getattr(self, 'group_name', None)
        if group_name:
            await self.channel_layer.group_discard(group_name, self.channel_name)

    async def notifications_message(self, event):
        await self.send_json(event['payload'])
