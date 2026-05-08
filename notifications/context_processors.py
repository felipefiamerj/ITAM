from .models import Notification


def notifications_context(request):
    if not request.user.is_authenticated:
        return {
            'notifications_unread_count': 0,
            'notifications_recent': [],
        }

    recent = Notification.objects.filter(user=request.user).order_by('-created_at')[:5]
    unread_count = Notification.objects.filter(user=request.user, is_read=False).count()

    return {
        'notifications_unread_count': unread_count,
        'notifications_recent': recent,
    }
