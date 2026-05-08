from django.urls import path

from .views import notification_read, notification_read_all, notifications_list

urlpatterns = [
    path('', notifications_list, name='notifications'),
    path('<int:pk>/read/', notification_read, name='notification_read'),
    path('read-all/', notification_read_all, name='notification_read_all'),
]
