from django.conf import settings
from django.db import models


class PackageEntry(models.Model):
    class Status(models.TextChoices):
        REGISTERED = 'REGISTERED', 'Registered'
        IN_TRANSIT = 'IN_TRANSIT', 'In Transit'
        DELIVERED = 'DELIVERED', 'Delivered'

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='package_entries',
    )
    package_name = models.CharField(max_length=255)
    tracking_number = models.CharField(max_length=128)
    courier = models.CharField(max_length=128, blank=True)
    order_date = models.DateField(null=True, blank=True)
    delivered_date = models.DateField(null=True, blank=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.REGISTERED,
    )
    locker_slot = models.CharField(max_length=32, blank=True)
    receiver_name = models.CharField(max_length=255, blank=True)
    receiver_phone = models.CharField(max_length=32, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        unique_together = ('owner', 'tracking_number')

    def __str__(self):
        return f'{self.package_name} ({self.tracking_number})'
