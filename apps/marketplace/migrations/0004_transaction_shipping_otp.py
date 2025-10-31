from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('marketplace', '0003_transaction_payment_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='transaction',
            name='buyer_full_name',
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name='transaction',
            name='buyer_phone_number',
            field=models.CharField(blank=True, max_length=32),
        ),
        migrations.AddField(
            model_name='transaction',
            name='otp',
            field=models.CharField(blank=True, max_length=6, null=True),
        ),
        migrations.AddField(
            model_name='transaction',
            name='shipping_address',
            field=models.TextField(blank=True),
        ),
    ]
