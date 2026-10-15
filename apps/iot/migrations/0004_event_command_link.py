import uuid

import django.db.models.deletion
from django.db import migrations, models


def assign_event_ids(apps, schema_editor):
    IoTEvent = apps.get_model('iot', 'IoTEvent')
    alias = schema_editor.connection.alias
    for event in IoTEvent.objects.using(alias).filter(event_id__isnull=True).iterator():
        event.event_id = uuid.uuid4()
        event.save(using=alias, update_fields=['event_id'])


class Migration(migrations.Migration):
    dependencies = [
        ('iot', '0003_alter_iotevent_event_type'),
        ('lockers', '0007_command_context'),
    ]

    operations = [
        migrations.AddField(
            model_name='iotevent',
            name='event_id',
            field=models.UUIDField(editable=False, null=True),
        ),
        migrations.AddField(
            model_name='iotevent',
            name='command',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='device_events',
                to='lockers.lockerrequest',
            ),
        ),
        migrations.RunPython(assign_event_ids, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='iotevent',
            name='event_id',
            field=models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
        ),
        migrations.AlterField(
            model_name='iotevent',
            name='event_type',
            field=models.CharField(
                choices=[
                    ('GENERIC', 'Generic'),
                    ('DEVICE', 'Device event'),
                    ('LOCKER_OPENED', 'Locker opened'),
                    ('ITEM_DETECTED', 'Item detected'),
                    ('ITEM_REMOVED', 'Item removed'),
                    ('COMMAND_FAILED', 'Command failed'),
                ],
                default='GENERIC',
                max_length=50,
            ),
        ),
    ]
