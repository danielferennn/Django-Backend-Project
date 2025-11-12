import logging
from typing import Iterable, Optional

from celery import shared_task
from django.contrib.auth import get_user_model

from .models import Notification


logger = logging.getLogger(__name__)


def _normalize_recipient_ids(user_ids: Iterable[int]) -> list[int]:
    normalized = []
    for user_id in user_ids or []:
        try:
            normalized.append(int(user_id))
        except (TypeError, ValueError):
            logger.warning("Invalid notification recipient id: %s", user_id)
    return list(dict.fromkeys(normalized))


@shared_task
def push_notification_task(user_ids: Optional[Iterable[int]] = None, title: str = '', body: str = '', metadata: Optional[dict] = None):
    user_ids = _normalize_recipient_ids(user_ids or [])
    if not user_ids:
        logger.warning("Notification skipped because no recipients were provided: %s", title)
        return 'no_recipients'

    User = get_user_model()
    users = list(User.objects.filter(id__in=user_ids))
    if not users:
        logger.warning("Notification recipients %s not found", user_ids)
        return 'users_not_found'

    Notification.objects.bulk_create([
        Notification(user=user, title=title, body=body) for user in users
    ])
    logger.info("Notification queued for users %s: %s", [user.id for user in users], title)
    # Hook for FCM/email integrations can be added here using metadata.
    return 'notification_created'


@shared_task
def noop_notifications_task():
    return 'notifications ready'
