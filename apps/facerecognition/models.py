import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from apps.users.models import User
from smartlocker.storage import private_media_storage


def upload_image_training2(instance, filename):
    extension = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
    suffix = f'.{extension}' if extension in {'jpg', 'jpeg', 'png', 'webp'} else '.jpg'
    return f'face-training/{instance.user_id}/{uuid.uuid4().hex}{suffix}'


def upload_image_access_user(instance, filename):
    return f'face-access-logs/{uuid.uuid4().hex}.jpg'


class Datawajahnew(models.Model):
    """Private face-training images for owner accounts."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='face_data',
        limit_choices_to={'role': User.Role.OWNER},
    )
    image_user = models.ImageField(
        storage=private_media_storage,
        upload_to=upload_image_training2,
        blank=True,
        null=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def clean(self):
        super().clean()
        if self.user.role != User.Role.OWNER:
            raise ValidationError({'user': 'Face data can only be created for users with OWNER role.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    class Meta:
        verbose_name = 'Data Wajah'
        verbose_name_plural = 'Data Wajah'


class Logsmartaccess2(models.Model):
    log_id = models.CharField(
        default=uuid.uuid4,
        primary_key=True,
        editable=False,
        max_length=255,
    )
    id_face_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        to_field='face_id',
        related_name='log_access_user',
        null=True,
    )
    image = models.ImageField(
        storage=private_media_storage,
        upload_to=upload_image_access_user,
        blank=True,
        null=True,
    )
    access_time = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=255)
