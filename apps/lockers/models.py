import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q


class Locker(models.Model):
    class LockerType(models.TextChoices):
        INBOUND = 'INBOUND', 'Inbound'
        STORAGE = 'STORAGE', 'Storage'
        MARKETPLACE = 'MARKETPLACE', 'Marketplace'

    class LockerStatus(models.TextChoices):
        AVAILABLE = 'AVAILABLE', 'Available'
        OCCUPIED = 'OCCUPIED', 'Occupied'
        RESERVED = 'RESERVED', 'Reserved'
        MAINTENANCE = 'MAINTENANCE', 'Maintenance'

    number = models.CharField(max_length=10, unique=True)
    type = models.CharField(max_length=20, choices=LockerType.choices)
    status = models.CharField(max_length=20, choices=LockerStatus.choices, default=LockerStatus.AVAILABLE)
    last_opened_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    gpio_pin = models.IntegerField(null=True, blank=True, help_text='BCM GPIO pin connected to this locker actuator')


class LockerLog(models.Model):
    class Action(models.TextChoices):
        OPEN = 'OPEN', 'Open'
        CLOSE = 'CLOSE', 'Close'
        DEPOSIT = 'DEPOSIT', 'Deposit'
        RETRIEVE = 'RETRIEVE', 'Retrieve'
        RECONCILE = 'RECONCILE', 'Reconcile'

    locker = models.ForeignKey(Locker, on_delete=models.PROTECT, related_name='logs')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    command = models.ForeignKey(
        'lockers.LockerRequest',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='logs',
    )
    transaction = models.ForeignKey(
        'marketplace.Transaction',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='locker_logs',
    )
    delivery = models.ForeignKey(
        'lockers.Delivery',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='locker_logs',
    )
    action = models.CharField(max_length=20, choices=Action.choices)
    timestamp = models.DateTimeField(auto_now_add=True)
    is_successful = models.BooleanField(default=True)
    details = models.TextField(blank=True, null=True)

    def save(self, *args, **kwargs):
        if self.pk:
            original = type(self).objects.filter(pk=self.pk).values(
                'locker_id', 'command_id', 'transaction_id', 'delivery_id',
            ).first()
            if original and any(
                original[field] != getattr(self, field)
                for field in ('locker_id', 'command_id', 'transaction_id', 'delivery_id')
            ):
                raise ValidationError('Locker log attribution is immutable.')
        super().save(*args, **kwargs)


class LockerRequest(models.Model):
    class Purpose(models.TextChoices):
        INBOUND_DEPOSIT = 'INBOUND_DEPOSIT', 'Inbound deposit'
        INBOUND_RETRIEVE = 'INBOUND_RETRIEVE', 'Inbound retrieval'
        STORAGE_DEPOSIT = 'STORAGE_DEPOSIT', 'Storage deposit'
        STORAGE_RETRIEVE = 'STORAGE_RETRIEVE', 'Storage retrieval'
        MARKETPLACE_DEPOSIT = 'MARKETPLACE_DEPOSIT', 'Marketplace deposit'
        MARKETPLACE_RETRIEVE = 'MARKETPLACE_RETRIEVE', 'Marketplace retrieval'
    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        SENT = 'SENT', 'Sent'
        OPENED = 'OPENED', 'Opened'
        COMPLETED = 'COMPLETED', 'Completed'
        FAILED = 'FAILED', 'Failed'
        UNKNOWN = 'UNKNOWN', 'Unknown'

    command_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    locker = models.ForeignKey(Locker, on_delete=models.PROTECT, null=True, blank=True, related_name='commands')
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    transaction = models.ForeignKey(
        'marketplace.Transaction',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='locker_commands',
    )
    delivery = models.ForeignKey(
        'lockers.Delivery',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='locker_commands',
    )
    purpose = models.CharField(max_length=32, choices=Purpose.choices, null=True, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    expires_at = models.DateTimeField(null=True, blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    error_code = models.CharField(max_length=64, blank=True)
    legacy_metadata = models.JSONField(default=dict, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=('locker',),
                condition=Q(locker__isnull=False, status__in=('PENDING', 'SENT', 'OPENED', 'UNKNOWN')),
                name='uniq_active_locker_command',
            ),
        ]
        indexes = [
            models.Index(fields=('transaction', 'status'), name='locker_cmd_tx_status_idx'),
            models.Index(fields=('delivery', 'status'), name='locker_cmd_delivery_status_idx'),
        ]

    def clean(self):
        super().clean()
        legacy = (
            self.status == self.Status.FAILED
            and self.error_code == 'legacy_untrusted'
            and self.purpose is None
            and self.transaction_id is None
            and self.delivery_id is None
        )
        if legacy:
            return
        if not self.locker_id or not self.purpose or not self.expires_at:
            raise ValidationError('A live locker command requires a locker, purpose, and expiry.')

        transaction_purposes = {
            self.Purpose.MARKETPLACE_DEPOSIT,
            self.Purpose.MARKETPLACE_RETRIEVE,
        }
        delivery_purposes = {
            self.Purpose.INBOUND_DEPOSIT,
            self.Purpose.INBOUND_RETRIEVE,
            self.Purpose.STORAGE_DEPOSIT,
            self.Purpose.STORAGE_RETRIEVE,
        }
        if self.purpose in transaction_purposes:
            if not self.transaction_id or self.delivery_id:
                raise ValidationError('Marketplace commands require exactly one transaction context.')
            if self.transaction.locker_id != self.locker_id or self.locker.type != Locker.LockerType.MARKETPLACE:
                raise ValidationError('Marketplace command locker does not match its transaction.')
            return
        if self.purpose not in delivery_purposes or self.transaction_id or not self.delivery_id:
            raise ValidationError('Delivery commands require exactly one delivery context.')

        if self.purpose in {self.Purpose.INBOUND_DEPOSIT, self.Purpose.INBOUND_RETRIEVE}:
            if self.delivery.locker_id != self.locker_id or self.locker.type != Locker.LockerType.INBOUND:
                raise ValidationError('Inbound command locker does not match its delivery.')
        elif (
            self.delivery.storage_locker_id != self.locker_id
            or self.locker.type != Locker.LockerType.STORAGE
        ):
            raise ValidationError('Storage command locker does not match its delivery.')


class Delivery(models.Model):
    class DeliveryStatus(models.TextChoices):
        PENDING = 'PENDING', 'Pending verification'
        VERIFIED = 'VERIFIED', 'Reserved for inbound deposit'
        DEPOSITED = 'DEPOSITED', 'Parcel awaiting transfer to storage'
        COMPLETED = 'COMPLETED', 'Stored for recipient'
        FAILED = 'FAILED', 'Failed'

    receipt_number = models.CharField(max_length=255, unique=True, help_text='Receipt number')
    courier = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, limit_choices_to={'role': 'COURIER'})
    locker = models.ForeignKey(
        Locker,
        on_delete=models.PROTECT,
        limit_choices_to={'type': 'INBOUND'},
        related_name='inbound_deliveries',
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='deliveries_as_owner',
    )
    storage_locker = models.ForeignKey(
        Locker,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='stored_deliveries',
    )
    status = models.CharField(max_length=20, choices=DeliveryStatus.choices, default=DeliveryStatus.PENDING)
    retrieved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class Package(models.Model):
    class PackageStatus(models.TextChoices):
        ACTIVE = 'ACTIVE', 'Active'
        COMPLETED = 'COMPLETED', 'Completed'

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='packages',
    )
    name = models.CharField(max_length=120)
    tracking_number = models.CharField(max_length=120)
    courier = models.CharField(max_length=60, blank=True)
    order_date = models.DateField(null=True, blank=True)
    status = models.CharField(
        max_length=20,
        choices=PackageStatus.choices,
        default=PackageStatus.ACTIVE,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        unique_together = ('owner', 'tracking_number')

    def __str__(self):
        return f'{self.name} ({self.tracking_number})'
