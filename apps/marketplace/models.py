from smartlocker.storage import private_media_storage
from django.conf import settings
from django.db import models
from django.db.models import Q


class Store(models.Model):
    owner = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='store',
    )
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    location = models.CharField(max_length=255, blank=True)


class Product(models.Model):
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name='products')
    seller = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='marketplace_products',
    )
    name = models.CharField(max_length=255)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    stock = models.PositiveIntegerField(default=0)
    description = models.TextField()
    image = models.ImageField(upload_to='products/', blank=True, null=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(price__gte=0), name='product_price_nonnegative'),
        ]


class Transaction(models.Model):
    class TransactionStatus(models.TextChoices):
        PENDING = 'PENDING', 'Pending manual payment'
        NEED_VERIFICATION = 'NEED_VERIFICATION', 'Payment proof awaiting verification'
        PAYMENT_VERIFIED = 'PAYMENT_VERIFIED', 'Payment verified'
        AWAITING_PICKUP = 'AWAITING_PICKUP', 'Awaiting pickup'
        COMPLETED = 'COMPLETED', 'Completed'
        REJECTED = 'REJECTED', 'Rejected'
        FAILED = 'FAILED', 'Failed'

    buyer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='purchases')
    seller = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='transactions_as_seller',
    )
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField()
    total_price = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.CharField(max_length=20, choices=TransactionStatus.choices, default=TransactionStatus.PENDING)
    buyer_full_name = models.CharField(max_length=255, blank=True)
    shipping_address = models.TextField(blank=True)
    buyer_phone_number = models.CharField(max_length=32, blank=True)
    payment_proof = models.ImageField(
        storage=private_media_storage,
        upload_to='payment_proofs/',
        blank=True,
        null=True,
    )
    payment_proof_uploaded_at = models.DateTimeField(blank=True, null=True)
    paid_at = models.DateTimeField(blank=True, null=True)
    locker = models.ForeignKey(
        'lockers.Locker',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='marketplace_transactions',
    )
    deposited_at = models.DateTimeField(null=True, blank=True)
    otp_hash = models.CharField(max_length=128, blank=True)
    otp_expires_at = models.DateTimeField(null=True, blank=True)
    otp_consumed_at = models.DateTimeField(null=True, blank=True)
    otp_failed_attempts = models.PositiveSmallIntegerField(default=0)
    inventory_released_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True, null=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.CheckConstraint(condition=Q(quantity__gte=1), name='transaction_quantity_positive'),
            models.CheckConstraint(condition=Q(total_price__gte=0), name='transaction_total_nonnegative'),
            models.CheckConstraint(condition=Q(otp_failed_attempts__lte=5), name='transaction_otp_attempts_lte_five'),
        ]
