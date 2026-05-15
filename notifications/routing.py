"""WebSocket routes for notification realtime updates."""

from django.urls import path

from .consumers import NotificationConsumer

websocket_urlpatterns = [
    path('ws/notifications/', NotificationConsumer.as_asgi(), name='ws_notifications'),
]
