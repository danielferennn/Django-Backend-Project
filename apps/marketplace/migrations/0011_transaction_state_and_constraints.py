import django.db.models.deletion
from django.db import migrations, models
from django.db.models import Q


def validate_legacy_rows(apps, schema_editor):
    Product = apps.get_model('marketplace', 'Product')
    Transaction = apps.get_model('marketplace', 'Transaction')
    alias = schema_editor.connection.alias
    invalid_prices = list(
        Product.objects.using(alias).filter(price__lt=0).values_list('pk', flat=True)[:100]
    )
    invalid_quantities = list(
        Transaction.objects.using(alias).filter(quantity__lt=1).values_list('pk', flat=True)[:100]
    )
    invalid_totals = list(
        Transaction.objects.using(alias).filter(total_price__lt=0).values_list('pk', flat=True)[:100]
    )
    unbound_pickups = list(
        Transaction.objects.using(alias)
        .filter(status='AWAITING_PICKUP')
        .values_list('pk', flat=True)[:100]
    )
    if invalid_prices or invalid_quantities or invalid_totals or unbound_pickups:
        raise RuntimeError(
            'Cannot migrate unsafe marketplace rows; repair or physically reconcile legacy rows first. '
            f'Negative product price IDs (first 100): {invalid_prices}; '
            f'invalid transaction quantity IDs (first 100): {invalid_quantities}; '
            f'negative transaction total IDs (first 100): {invalid_totals}; '
            'legacy AWAITING_PICKUP transaction IDs without trusted locker attribution '
            f'(first 100): {unbound_pickups}.'

        )

def map_legacy_statuses(apps, schema_editor):
    Transaction = apps.get_model('marketplace', 'Transaction')
    alias = schema_editor.connection.alias
    Transaction.objects.using(alias).filter(status='PAID').update(status='PAYMENT_VERIFIED')
    Transaction.objects.using(alias).filter(status='ESCROW').update(status='PAYMENT_VERIFIED')
    Transaction.objects.using(alias).filter(status='RELEASED').update(status='COMPLETED')


class Migration(migrations.Migration):
    dependencies = [
        ('lockers', '0006_domain_links'),
        ('marketplace', '0010_merge_20250811_1041'),
    ]

    operations = [
        migrations.RunPython(validate_legacy_rows, migrations.RunPython.noop),
        migrations.AddField(
            model_name='transaction',
            name='locker',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='pickup_transactions',
                to='lockers.locker',
            ),
        ),
        migrations.AddField(
            model_name='transaction',
            name='deposited_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='transaction',
            name='otp_hash',
            field=models.CharField(blank=True, default='', max_length=128),
        ),
        migrations.AlterField(
            model_name='transaction',
            name='otp_hash',
            field=models.CharField(blank=True, max_length=128),
        ),
        migrations.AddField(
            model_name='transaction',
            name='otp_expires_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='transaction',
            name='otp_consumed_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='transaction',
            name='otp_failed_attempts',
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='transaction',
            name='inventory_released_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(map_legacy_statuses, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='transaction',
            name='status',
            field=models.CharField(
                choices=[
                    ('PENDING', 'Pending payment'),
                    ('NEED_VERIFICATION', 'Needs payment verification'),
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
        migrations.RemoveField(model_name='transaction', name='payment_gateway_id'),
        migrations.RemoveField(model_name='transaction', name='qris_payload'),
        migrations.RemoveField(model_name='transaction', name='qris_image'),
        migrations.RemoveField(model_name='transaction', name='payment_expires_at'),
        migrations.RemoveField(model_name='transaction', name='otp'),
        migrations.AddConstraint(
            model_name='product',
            constraint=models.CheckConstraint(
                condition=Q(price__gte=0),
                name='product_price_nonnegative',
            ),
        ),
        migrations.AddConstraint(
            model_name='transaction',
            constraint=models.CheckConstraint(
                condition=Q(quantity__gte=1),
                name='transaction_quantity_positive',
            ),
        ),
        migrations.AddConstraint(
            model_name='transaction',
            constraint=models.CheckConstraint(
                condition=Q(total_price__gte=0),
                name='transaction_total_nonnegative',
            ),
        ),
        migrations.AddConstraint(
            model_name='transaction',
            constraint=models.CheckConstraint(
                condition=Q(otp_failed_attempts__lte=5),
                name='transaction_otp_attempts_lte_five',
            ),
        ),
    ]
