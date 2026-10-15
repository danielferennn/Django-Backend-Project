import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('lockers', '0005_lockerrequest'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterField(
            model_name='locker',
            name='status',
            field=models.CharField(
                choices=[
                    ('AVAILABLE', 'Available'),
                    ('OCCUPIED', 'Occupied'),
                    ('RESERVED', 'Reserved'),
                    ('MAINTENANCE', 'Maintenance'),
                ],
                default='AVAILABLE',
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name='lockerlog',
            name='locker',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name='logs',
                to='lockers.locker',
            ),
        ),
        migrations.AlterField(
            model_name='lockerlog',
            name='action',
            field=models.CharField(
                choices=[
                    ('OPEN', 'Open'),
                    ('CLOSE', 'Close'),
                    ('DEPOSIT', 'Deposit'),
                    ('RETRIEVE', 'Retrieve'),
                    ('RECONCILE', 'Reconcile'),
                ],
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='delivery',
            name='owner',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='deliveries_as_owner',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name='delivery',
            name='storage_locker',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='stored_deliveries',
                to='lockers.locker',
            ),
        ),
        migrations.AddField(
            model_name='delivery',
            name='retrieved_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name='delivery',
            name='locker',
            field=models.ForeignKey(
                limit_choices_to={'type': 'INBOUND'},
                on_delete=django.db.models.deletion.PROTECT,
                related_name='inbound_deliveries',
                to='lockers.locker',
            ),
        ),
        migrations.AlterField(
            model_name='delivery',
            name='status',
            field=models.CharField(
                choices=[
                    ('PENDING', 'Pending verification'),
                    ('VERIFIED', 'Reserved for inbound deposit'),
                    ('DEPOSITED', 'Parcel awaiting transfer to storage'),
                    ('COMPLETED', 'Stored for recipient'),
                    ('FAILED', 'Failed'),
                ],
                default='PENDING',
                max_length=20,
            ),
        ),
    ]
