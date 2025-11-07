from django.urls import path

from .views import NotificationListView, NotificationPushView

urlpatterns = [
    path('', NotificationListView.as_view(), name='notifications-list'),
    path('push/', NotificationPushView.as_view(), name='notifications-push'),
]
