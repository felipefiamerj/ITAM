"""Broadcast helpers for live notifications over Channels."""

from __future__ import annotations

import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.db import transaction

from .models import Notification

logger = logging.getLogger(__name__)
GROUP_PREFIX = 'notifications.user.'


def notification_group_name(user_id):
    return f'{GROUP_PREFIX}{user_id}'


def serialize_notification(notification):
    created_at = getattr(notification, 'created_at', None)
    return {
        'id': getattr(notification, 'pk', None),
        'title': getattr(notification, 'title', ''),
        'message': getattr(notification, 'message', ''),
        'link': getattr(notification, 'link', ''),
        'is_read': bool(getattr(notification, 'is_read', False)),
        'created_at': created_at.isoformat() if created_at else None,
    }


def unread_count_for_user(user_or_id):
    user_id = getattr(user_or_id, 'pk', user_or_id)
    if not user_id:
        return 0
    return Notification.objects.filter(user_id=user_id, is_read=False).count()


def recent_notifications_for_user(user_or_id, limit=5):
    user_id = getattr(user_or_id, 'pk', user_or_id)
    if not user_id:
        return []
    notifications = Notification.objects.filter(user_id=user_id).order_by('-created_at')[:limit]
    return [serialize_notification(notification) for notification in notifications]


def notification_created_payload(notification):
    return {
        'event': 'notification.created',
        'notification': serialize_notification(notification),
        'unread_count': unread_count_for_user(notification.user_id),
    }


def notification_state_payload(user_or_id, include_recent=False):
    payload = {
        'event': 'notifications.sync',
        'unread_count': unread_count_for_user(user_or_id),
    }
    if include_recent:
        payload['recent'] = recent_notifications_for_user(user_or_id)
    return payload


def _send_group_message(group_name, payload):
    channel_layer = get_channel_layer()
    if not channel_layer:
        return
    try:
        async_to_sync(channel_layer.group_send)(
            group_name,
            {
                'type': 'notifications.message',
                'payload': payload,
            },
        )
    except Exception:
        logger.exception('Nao foi possivel enviar a notificacao em tempo real.')


def broadcast_notification(notification):
    if not notification or not getattr(notification, 'user_id', None):
        return

    def _send():
        _send_group_message(notification_group_name(notification.user_id), notification_created_payload(notification))

    transaction.on_commit(_send)


def broadcast_user_state(user_or_id, include_recent=False):
    user_id = getattr(user_or_id, 'pk', user_or_id)
    if not user_id:
        return

    def _send():
        _send_group_message(
            notification_group_name(user_id),
            notification_state_payload(user_or_id, include_recent=include_recent),
        )

    transaction.on_commit(_send)
