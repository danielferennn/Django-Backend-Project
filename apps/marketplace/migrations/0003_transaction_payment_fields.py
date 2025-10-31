from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('marketplace', '0002_product_image'),
    ]

    operations = [
        migrations.AddField(
            model_name='transaction',
            name='paid_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='transaction',
            name='payment_expires_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='transaction',
            name='payment_proof',
            field=models.ImageField(blank=True, null=True, upload_to='payment_proofs/'),
        ),
        migrations.AddField(
            model_name='transaction',
            name='payment_proof_uploaded_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='transaction',
            name='qris_image',
            field=models.ImageField(blank=True, null=True, upload_to='qris_images/'),
        ),
        migrations.AddField(
            model_name='transaction',
            name='qris_payload',
            field=models.TextField(blank=True, null=True),
        ),
    ]
