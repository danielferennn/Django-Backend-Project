from django.db import migrations, models


def _downgrade_face_match(_apps, _schema_editor):
    # Nothing to do when rolling back; the old choice will be restored
    # by the AlterField operation in reverse.
    return


def _upgrade_face_match(apps, _schema_editor):
    IoTEvent = apps.get_model('iot', 'IoTEvent')
    IoTEvent.objects.filter(event_type='FACE_MATCH').update(event_type='GENERIC')


class Migration(migrations.Migration):

    dependencies = [
        ('iot', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(_upgrade_face_match, _downgrade_face_match),
        migrations.AlterField(
            model_name='iotevent',
            name='event_type',
            field=models.CharField(
                choices=[('GENERIC', 'Generic'), ('DEVICE', 'Device Event')],
                default='GENERIC',
                max_length=50,
            ),
        ),
    ]
