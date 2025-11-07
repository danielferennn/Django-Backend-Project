from django.contrib.auth.models import AbstractUser
from django.db import models

class User(AbstractUser):
    class Role(models.TextChoices):
        OWNER = 'OWNER', 'Owner'
        COURIER = 'COURIER', 'Courier'
        SELLER = 'SELLER', 'Seller'
        BUYER = 'BUYER', 'Buyer'
        ADMIN = 'ADMIN', 'Admin'

    first_name = None
    last_name = None
    
    email = models.EmailField(unique=True)
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.BUYER)
    face_id = models.CharField(max_length=32, unique=True, null=True, blank=True)
    
    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['username']
