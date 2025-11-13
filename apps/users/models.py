from django.contrib.auth.models import AbstractUser
from django.db import models

# def generate ini nambahin dari punya beliau, terus buat class user diubah menurut perubahan mas fahmi, versi yang lama ku comment

def generate_uid2():
    first_digit=secrets.choice('123456789')
    random_digit=''.join(secrets.choice(string.digits) for i in range(5))
    return first_digit+random_digit

class User(AbstractUser):

    class Role(models.TextChoices):

        OWNER = 'OWNER', 'Owner'
        COURIER = 'COURIER', 'Courier'
        SELLER = 'SELLER', 'Seller'
        BUYER = 'BUYER', 'Buyer'
        ADMIN = 'ADMIN', 'Admin'

    # Override first_name and last_name - hanya untuk OWNER
    # Tidak set None, tetapi override dengan CharField baru
    first_name = models.CharField(
        max_length=150,
        blank=True,
        default='',
        help_text="First name (required for OWNER role)"
    )
    last_name = models.CharField(
        max_length=150,
        blank=True,
        default='',
        help_text="Last name (required for OWNER role)"
    )

    # Custom fields
    email = models.EmailField(unique=True)
    role = models.CharField(
        max_length=10,
        choices=Role.choices,
        default=Role.BUYER
    )

    # ID unik untuk face recognition (hanya untuk OWNER)
    face_id = models.CharField(
        max_length=10,
        unique=True,
        null=True,
        blank=True,
        editable=False,
        help_text="Unique ID for face recognition (OWNER only)"
    )

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['username']

    def clean(self):
        """Validasi bahwa OWNER harus memiliki first_name dan last_name"""
        super().clean()
        if self.role == self.Role.OWNER:
            if not self.first_name or not self.last_name:
                raise ValidationError({
                    'first_name': 'First name is required for OWNER role.',
                    'last_name': 'Last name is required for OWNER role.'
                })

    def save(self, *args, **kwargs):
        # Generate face_id hanya untuk OWNER
        if self.role == self.Role.OWNER and not self.face_id:
            self.face_id = generate_uid2()
        # Hapus face_id jika role berubah bukan OWNER
        elif self.role != self.Role.OWNER:
            self.face_id = None
            # Clear first_name dan last_name jika bukan OWNER
            self.first_name = ''
            self.last_name = ''

        super().save(*args, **kwargs)

    def __str__(self):
        if self.role == self.Role.OWNER and self.first_name and self.last_name:
            return f"{self.first_name} {self.last_name} ({self.get_role_display()})"
        return f"{self.username} ({self.get_role_display()})"

    class Meta:
        verbose_name = 'User'
        verbose_name_plural = 'Users'

    # ROLE_BUYER = 'buyer'
    # ROLE_OWNER = 'owner'
    # ROLE_COURIER = 'courier'
    # ROLE_ADMIN = 'admin'

    # ROLE_CHOICES = (
    #     (ROLE_BUYER, 'Buyer'),
    #     (ROLE_OWNER, 'Owner'),
    # )
    # EXTRA_ROLE_CHOICES = (
    #     (ROLE_COURIER, 'Courier'),
    #     (ROLE_ADMIN, 'Admin'),
    # )

    # LEGACY_ROLE_MAP = {
    #     'BUYER': ROLE_BUYER,
    #     'OWNER': ROLE_OWNER,
    #     'SELLER': ROLE_OWNER,
    #     'COURIER': ROLE_COURIER,
    #     'ADMIN': ROLE_ADMIN,
    # }

    # first_name = None
    # last_name = None

    # email = models.EmailField(unique=True)
    # role = models.CharField(
    #     max_length=20,
    #     choices=ROLE_CHOICES + EXTRA_ROLE_CHOICES,
    #     default=ROLE_BUYER,
    # )
    # face_id = models.CharField(max_length=32, unique=True, null=True, blank=True)

    # USERNAME_FIELD = 'email'
    # REQUIRED_FIELDS = ['username']

    # def save(self, *args, **kwargs):
    #     normalized = self.LEGACY_ROLE_MAP.get(self.role, self.role)
    #     if normalized in dict(self.ROLE_CHOICES + self.EXTRA_ROLE_CHOICES):
    #         self.role = normalized
    #     super().save(*args, **kwargs)
