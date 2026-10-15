import uuid

import django.db.models.deletion
from django.db import migrations, models
from django.db.models import Q


def migrate_legacy_requests(apps, schema_editor):
    Locker = apps.get_model('lockers', 'Locker')
    LockerRequest = apps.get_model('lockers', 'LockerRequest')
    alias = schema_editor.connection.alias

    for request in LockerRequest.objects.using(alias).all().iterator():
        locker_id = (
            Locker.objects.using(alias)
            .filter(number=request.locker_number)
            .values_list('pk', flat=True)
            .first()
        )
        request.command_id = uuid.uuid4()
        request.locker_id = locker_id
        request.transaction_id = None
        request.delivery_id = None
        request.purpose = None
        request.status = 'FAILED'
        request.expires_at = request.created_at
        request.published_at = None
        request.completed_at = None
        request.error_code = 'legacy_untrusted'
        request.legacy_metadata = {
            'locker_number': request.locker_number,
            'fulfilled': request.fulfilled,
        }
        request.save(
            using=alias,
            update_fields=[
                'command_id',
                'locker_id',
                'transaction_id',
                'delivery_id',
                'purpose',
                'status',
                'expires_at',
                'published_at',
                'completed_at',
                'error_code',
                'legacy_metadata',
            ],
        )


class Migration(migrations.Migration):
    dependencies = [
        ('lockers', '0006_domain_links'),
        ('marketplace', '0011_transaction_state_and_constraints'),
    ]

    operations = [
        migrations.AddField(
            model_name='lockerrequest',
            name='command_id',
            field=models.UUIDField(editable=False, null=True),
        ),
        migrations.AddField(
            model_name='lockerrequest',
            name='locker',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='commands',
                to='lockers.locker',
            ),
        ),
        migrations.AddField(
            model_name='lockerrequest',
            name='transaction',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='locker_commands',
                to='marketplace.transaction',
            ),
        ),
        migrations.AddField(
            model_name='lockerrequest',
            name='delivery',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='locker_commands',
                to='lockers.delivery',
            ),
        ),
        migrations.AddField(
            model_name='lockerrequest',
            name='purpose',
            field=models.CharField(
                blank=True,
                choices=[
                    ('INBOUND_DEPOSIT', 'Inbound deposit'),
                    ('INBOUND_RETRIEVE', 'Inbound retrieval'),
                    ('STORAGE_DEPOSIT', 'Storage deposit'),
                    ('STORAGE_RETRIEVE', 'Storage retrieval'),
                    ('MARKETPLACE_DEPOSIT', 'Marketplace deposit'),
                    ('MARKETPLACE_RETRIEVE', 'Marketplace retrieval'),
                ],
                max_length=32,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name='lockerrequest',
            name='status',
            field=models.CharField(
                choices=[
                    ('PENDING', 'Pending'),
                    ('SENT', 'Sent'),
                    ('OPENED', 'Opened'),
                    ('COMPLETED', 'Completed'),
                    ('FAILED', 'Failed'),
                    ('UNKNOWN', 'Unknown'),
                ],
                default='PENDING',
                max_length=12,
            ),
        ),
        migrations.AddField(
            model_name='lockerrequest',
            name='expires_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='lockerrequest',
            name='published_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='lockerrequest',
            name='completed_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='lockerrequest',
            name='error_code',
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.AddField(
            model_name='lockerrequest',
            name='legacy_metadata',
            field=models.JSONField(default=dict, editable=False),
        ),
        migrations.RunPython(migrate_legacy_requests, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='lockerrequest',
            name='command_id',
            field=models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
        ),
        migrations.RemoveField(model_name='lockerrequest', name='locker_number'),
        migrations.RemoveField(model_name='lockerrequest', name='fulfilled'),
        migrations.AddConstraint(
            model_name='lockerrequest',
            constraint=models.UniqueConstraint(
                condition=Q(locker__isnull=False, status__in=('PENDING', 'SENT', 'OPENED', 'UNKNOWN')),
                fields=('locker',),
                name='uniq_active_locker_command',
            ),
        ),
        migrations.AddIndex(
            model_name='lockerrequest',
            index=models.Index(
                fields=['transaction', 'status'],
                name='locker_cmd_tx_status_idx',
            ),
        ),
        migrations.AddIndex(
            model_name='lockerrequest',
            index=models.Index(
                fields=['delivery', 'status'],
                name='locker_cmd_delivery_status_idx',
            ),
        ),
        migrations.AddField(
            model_name='lockerlog',
            name='command',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='logs',
                to='lockers.lockerrequest',
            ),
        ),
        migrations.AddField(
            model_name='lockerlog',
            name='transaction',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='locker_logs',
                to='marketplace.transaction',
            ),
        ),
        migrations.AddField(
            model_name='lockerlog',
            name='delivery',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='locker_logs',
                to='lockers.delivery',
            ),
        ),
    ]
