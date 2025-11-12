from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('marketplace', '0007_store_location_alter_transaction_status'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    sql="""
                        ALTER TABLE marketplace_product
                        ADD COLUMN IF NOT EXISTS image varchar(100);
                    """,
                ),
                migrations.RunSQL(
                    sql="""
                        ALTER TABLE marketplace_product
                        ADD COLUMN IF NOT EXISTS created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP;
                    """,
                ),
                migrations.RunSQL(
                    sql="""
                        ALTER TABLE marketplace_product
                        ADD COLUMN IF NOT EXISTS updated_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP;
                    """,
                ),
            ],
            state_operations=[],
        ),
    ]
