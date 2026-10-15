import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('marketplace', '0002_alter_transaction_options_and_more'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            # No database operations. The earlier ``0002_product_image`` node
            # already created the ``image`` column, and ``created_at`` /
            # ``updated_at`` are physically added later by
            # ``0008_fix_product_media_fields``. The original
            # ``DROP COLUMN ... CASCADE`` statements destroyed populated
            # ``image`` values (and any pre-existing timestamps) for every
            # database upgrading through this node, so they were removed.
            # The state-only field definitions are kept so the migration graph
            # and the resulting model state are unchanged.
            database_operations=[],
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
