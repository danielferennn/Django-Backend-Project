import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('marketplace', '0002_alter_transaction_options_and_more'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    sql="ALTER TABLE marketplace_product DROP COLUMN IF EXISTS image CASCADE;",
                ),
                migrations.RunSQL(
                    sql="ALTER TABLE marketplace_product DROP COLUMN IF EXISTS created_at CASCADE;",
                ),
                migrations.RunSQL(
                    sql="ALTER TABLE marketplace_product DROP COLUMN IF EXISTS updated_at CASCADE;",
                ),
            ],
            state_operations=[
                migrations.AddField(
                    model_name='product',
                    name='image',
                    field=models.ImageField(blank=True, null=True, upload_to='products/'),
                ),
                migrations.AddField(
                    model_name='product',
                    name='created_at',
                    field=models.DateTimeField(auto_now_add=True, default=django.utils.timezone.now),
                    preserve_default=False,
                ),
                migrations.AddField(
                    model_name='product',
                    name='updated_at',
                    field=models.DateTimeField(auto_now=True),
                ),
            ],
        ),
    ]
