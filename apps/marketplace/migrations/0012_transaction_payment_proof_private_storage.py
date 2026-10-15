import django.db.models.deletion
import smartlocker.storage
from django.db import migrations, models


class Migration(migrations.Migration):
    """Align migration state with the current Transaction model.

    * ``payment_proof`` is moved onto the shared private storage backend. The
      column itself is unchanged (an ``ImageField`` is a varchar in PostgreSQL)
      and storage is a Python-side concern, so existing file names/rows are kept
      as-is. Only ``AlterField`` is used; no ``RemoveField``/``AddField`` pair.
    * ``locker.related_name`` and the ``status`` choice labels are corrected to
      match ``apps/marketplace/models.py`` so ``makemigrations`` reports no
      further changes.
    """

    dependencies = [
        ('marketplace', '0011_transaction_state_and_constraints'),
    ]

    operations = [
        migrations.AlterField(
            model_name='transaction',
            name='locker',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='marketplace_transactions',
                to='lockers.locker',
            ),
        ),
        migrations.AlterField(
            model_name='transaction',
            name='payment_proof',
            field=models.ImageField(
                blank=True,
                null=True,
                storage=smartlocker.storage.PrivateMediaStorage(),
                upload_to='payment_proofs/',
            ),
        ),
        migrations.AlterField(
            model_name='transaction',
            name='status',
            field=models.CharField(
                choices=[
                    ('PENDING', 'Pending manual payment'),
                    ('NEED_VERIFICATION', 'Payment proof awaiting verification'),
                    ('PAYMENT_VERIFIED', 'Payment verified'),
                    ('AWAITING_PICKUP', 'Awaiting pickup'),
                    ('COMPLETED', 'Completed'),
                    ('REJECTED', 'Rejected'),
                    ('FAILED', 'Failed'),
                ],
                default='PENDING',
                max_length=20,
            ),
        ),
    ]
