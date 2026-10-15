from typing import Iterable, Optional

from celery import shared_task

from .services import create_notifications


@shared_task
def push_notification_task(user_ids: Optional[Iterable[int]] = None, title: str = '', body: str = ''):
    recipients = list(dict.fromkeys(user_ids or []))
    if not recipients:
        return 'no_recipients'
    create_notifications(user_ids=recipients, title=title, body=body)
    return 'notification_created'
