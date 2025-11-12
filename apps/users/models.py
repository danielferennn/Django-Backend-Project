from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    ROLE_BUYER = 'buyer'
    ROLE_OWNER = 'owner'
    ROLE_COURIER = 'courier'
    ROLE_ADMIN = 'admin'

    ROLE_CHOICES = (
        (ROLE_BUYER, 'Buyer'),
        (ROLE_OWNER, 'Owner'),
    )
    EXTRA_ROLE_CHOICES = (
        (ROLE_COURIER, 'Courier'),
        (ROLE_ADMIN, 'Admin'),
    )

    LEGACY_ROLE_MAP = {
        'BUYER': ROLE_BUYER,
        'OWNER': ROLE_OWNER,
        'SELLER': ROLE_OWNER,
        'COURIER': ROLE_COURIER,
        'ADMIN': ROLE_ADMIN,
    }

    first_name = None
    last_name = None

    email = models.EmailField(unique=True)
    role = models.CharField(
        max_length=20,
        choices=ROLE_CHOICES + EXTRA_ROLE_CHOICES,
        default=ROLE_BUYER,
    )
    face_id = models.CharField(max_length=32, unique=True, null=True, blank=True)

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['username']

    def save(self, *args, **kwargs):
        normalized = self.LEGACY_ROLE_MAP.get(self.role, self.role)
        if normalized in dict(self.ROLE_CHOICES + self.EXTRA_ROLE_CHOICES):
            self.role = normalized
        super().save(*args, **kwargs)
