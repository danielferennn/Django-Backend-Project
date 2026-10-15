from django.db import migrations


class Migration(migrations.Migration):
    """Restore the merge node referenced by 0011_transaction_state_and_constraints.

    The migration graph declared ``0010_merge_20250811_1041`` as a parent but the
    file was absent, so ``MigrationLoader`` raised ``NodeNotFoundError`` for the
    whole project. This node is a no-op, which keeps it harmless both for fresh
    databases and for databases where it was already recorded as applied.
    """

    dependencies = [
        ('marketplace', '0010_product_seller_and_transaction_seller'),
    ]

    operations = []
