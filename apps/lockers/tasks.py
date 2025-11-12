import time

from celery import shared_task

from apps.notifications.tasks import push_notification_task


@shared_task
def send_notification_task(user_id, message, title="SmartLocker Update"):
    time.sleep(1)
    push_notification_task(user_ids=[user_id], title=title, body=message)
    return f"Notification queued for user {user_id}"
