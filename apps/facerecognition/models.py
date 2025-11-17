from django.db import models
import os
import uuid
from django.core.exceptions import ValidationError

from apps.users.models import User
# Create your models here.

def upload_image_training2(instance,filename):
    return os.path.join('imagetraining',str(instance.user.first_name+instance.user.last_name),filename)
def upload_image_access_user(instance,filename):
    print(instance)
    return os.path.join('tracking',filename)
class Datawajahnew(models.Model):
    """Face data model - hanya untuk User dengan role OWNER"""

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='face_data',
        limit_choices_to={'role': User.Role.OWNER},
        help_text="Must be a user with OWNER role"
    )

    image_user = models.ImageField(
        upload_to=upload_image_training2,
        blank=True,
        null=True,
        help_text="Face image for training"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)



    def clean(self):
        """Validasi bahwa user harus memiliki role OWNER"""
        super().clean()
        if self.user.role != User.Role.OWNER:
            raise ValidationError({
                'user': 'Face data can only be created for users with OWNER role.'
            })

    def save(self, *args, **kwargs):
        # Jalankan validasi sebelum save
        self.full_clean()
        super().save(*args, **kwargs)

    class Meta:
        verbose_name = 'Data Wajah'
        verbose_name_plural = 'Data Wajah'
class Logsmartaccess2(models.Model):
    log_id=models.CharField(default=uuid.uuid4,primary_key=True,editable=False,max_length=255)
    id_face_user=models.ForeignKey(User,on_delete=models.CASCADE,to_field='face_id',related_name='log_access_user',null=True)
    image=models.ImageField(upload_to=upload_image_access_user,default='',blank=True,null=True)
    access_time=models.DateTimeField(auto_now_add=True)
    status=models.CharField(max_length=255)
