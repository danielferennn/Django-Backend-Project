from django.db import models
from django.conf import settings

class Store(models.Model):
    owner = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='store')
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)

class Product(models.Model):
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name='products')
    name = models.CharField(max_length=255)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    stock = models.PositiveIntegerField(default=0)
    description = models.TextField()
    image = models.ImageField(upload_to='products/', blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class Transaction(models.Model):
    class TransactionStatus(models.TextChoices):
        PENDING = 'PENDING', 'Pending Payment'
        PAID = 'PAID', 'Paid'
        ESCROW = 'ESCROW', 'Escrow'
        AWAITING_PICKUP = 'AWAITING_PICKUP', 'Awaiting Pickup'
        RELEASED = 'RELEASED', 'Escrow Released'
        COMPLETED = 'COMPLETED', 'Completed'
        FAILED = 'FAILED', 'Failed'
        REJECTED = 'REJECTED', 'Rejected'

    buyer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='purchases')
    seller = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='sales')
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField()
    total_price = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.CharField(max_length=20, choices=TransactionStatus.choices, default=TransactionStatus.PENDING)
    buyer_full_name = models.CharField(max_length=255, blank=True)
    shipping_address = models.TextField(blank=True)
    buyer_phone_number = models.CharField(max_length=32, blank=True)
    payment_gateway_id = models.CharField(max_length=255, blank=True, null=True)
    qris_payload = models.TextField(blank=True, null=True)
    qris_image = models.ImageField(upload_to='qris_images/', blank=True, null=True)
    payment_proof = models.ImageField(upload_to='payment_proofs/', blank=True, null=True)
    payment_proof_uploaded_at = models.DateTimeField(blank=True, null=True)
    payment_expires_at = models.DateTimeField(blank=True, null=True)
    paid_at = models.DateTimeField(blank=True, null=True)
    otp = models.CharField(max_length=6, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True, null=True)
    payment_proof = models.FileField(upload_to='payment_proofs/', blank=True, null=True)
    payment_proof_uploaded_at = models.DateTimeField(blank=True, null=True)
    paid_at = models.DateTimeField(blank=True, null=True)
    payment_expires_at = models.DateTimeField(blank=True, null=True)
    qris_payload = models.TextField(blank=True)
    qris_image = models.CharField(max_length=255, blank=True)
    buyer_full_name = models.CharField(max_length=255, blank=True)
    shipping_address = models.TextField(blank=True)
    buyer_phone_number = models.CharField(max_length=32, blank=True)
    otp_code = models.CharField(max_length=6, blank=True, db_column='otp')

    class Meta:
        ordering = ['-created_at']
