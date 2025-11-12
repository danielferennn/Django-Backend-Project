from django.db import migrations, models


STATUS_MAP = {
    'COMPLETED': 'DELIVERED',
    'FAILED': 'REGISTERED',
}


def forwards(apps, schema_editor):
    PackageEntry = apps.get_model('package_center', 'PackageEntry')
    for source, target in STATUS_MAP.items():
        PackageEntry.objects.filter(status=source).update(status=target)


def backwards(apps, schema_editor):
    PackageEntry = apps.get_model('package_center', 'PackageEntry')
    # Default previously collapsed statuses back to FAILED to highlight manual review.
    PackageEntry.objects.filter(status='REGISTERED').update(status='FAILED')


class Migration(migrations.Migration):
    dependencies = [
        ('package_center', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
        migrations.AlterField(
            model_name='packageentry',
            name='status',
            field=models.CharField(
                choices=[
                    ('REGISTERED', 'Registered'),
                    ('IN_TRANSIT', 'In Transit'),
                    ('DELIVERED', 'Delivered'),
                ],
                default='REGISTERED',
                max_length=20,
            ),
        ),
    ]
