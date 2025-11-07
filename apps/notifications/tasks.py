import logging
from typing import Optional

from celery import shared_task
from django.contrib.auth import get_user_model

from .models import Notification


logger = logging.getLogger(__name__)


@shared_task
def push_notification_task(user_id: int, title: str, body: str, metadata: Optional[dict] = None):
    User = get_user_model()
    user = User.objects.filter(id=user_id).first()
    if not user:
        logger.warning("Notification target user %s not found", user_id)
        return 'user_not_found'

    Notification.objects.create(user=user, title=title, body=body)
    # Hook for FCM/email integrations can be added here using metadata.
    logger.info("Notification queued for user %s: %s", user_id, title)
    return 'notification_created'


@shared_task
def noop_notifications_task():
    return 'notifications ready'
