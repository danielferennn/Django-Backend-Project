from django.conf import settings
from django.db import models


class IoTEvent(models.Model):
    class EventType(models.TextChoices):
        GENERIC = 'GENERIC', 'Generic'
        DEVICE = 'DEVICE', 'Device Event'

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='iot_events',
    )
    event_type = models.CharField(
        max_length=50,
        choices=EventType.choices,
        default=EventType.GENERIC,
    )
    payload = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.event_type} @ {self.created_at.isoformat()}"
