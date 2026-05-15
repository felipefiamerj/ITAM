from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .models import Notification
from .realtime import broadcast_user_state


@login_required
def notifications_list(request):
    notifications = Notification.objects.filter(user=request.user).order_by('-created_at')
    return render(
        request,
        'notifications/lista.html',
        {
            'notifications': notifications[:50],
            'unread_notifications_count': notifications.filter(is_read=False).count(),
        },
    )


@login_required
def notification_read(request, pk):
    notification = get_object_or_404(Notification, pk=pk, user=request.user)
    notification.mark_as_read()
    broadcast_user_state(notification.user)
    return redirect(request.GET.get('next') or 'notifications')


@login_required
def notification_read_all(request):
    Notification.objects.filter(user=request.user, is_read=False).update(is_read=True, read_at=timezone.now())
    broadcast_user_state(request.user)
    messages.success(request, 'Notificações marcadas como lidas.')
    return redirect(request.GET.get('next') or 'notifications')
