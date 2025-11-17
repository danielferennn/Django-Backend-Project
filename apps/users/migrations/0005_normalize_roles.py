from django.db import migrations


def normalize_roles(apps, schema_editor):
    User = apps.get_model('users', 'User')
    roles = ['owner', 'buyer', 'seller', 'courier', 'admin']
    for role in roles:
        User.objects.filter(role=role).update(role=role.upper())


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0004_alter_user_options_user_first_name_user_last_name_and_more'),
    ]

    operations = [
        migrations.RunPython(normalize_roles, migrations.RunPython.noop),
    ]
