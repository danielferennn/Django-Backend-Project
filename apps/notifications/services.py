from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction

from .models import Notification


def create_notifications(*, user_ids, title: str, body: str) -> list[Notification]:
    normalized_ids = list(dict.fromkeys(user_ids))
    if not normalized_ids:
        raise ValidationError('At least one notification recipient is required.')

    User = get_user_model()
    users_by_id = User.objects.in_bulk(normalized_ids)
    if len(users_by_id) != len(normalized_ids):
        raise ValidationError('Every notification recipient must exist.')

    notifications = [
        Notification(user=users_by_id[user_id], title=title, body=body)
        for user_id in normalized_ids
    ]
    with transaction.atomic():
        return Notification.objects.bulk_create(notifications)
