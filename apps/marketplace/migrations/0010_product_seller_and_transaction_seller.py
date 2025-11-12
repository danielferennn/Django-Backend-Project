from django.conf import settings
from django.db import migrations, models


def assign_sellers(apps, schema_editor):
    Product = apps.get_model('marketplace', 'Product')
    Transaction = apps.get_model('marketplace', 'Transaction')

    products = Product.objects.select_related('store__owner').filter(seller__isnull=True)
    for product in products:
        owner = getattr(product.store, 'owner', None)
        if owner:
            product.seller = owner
            product.save(update_fields=['seller'])

    transactions = Transaction.objects.select_related('product__seller', 'product__store__owner')
    for tx in transactions:
        seller = getattr(tx.product, 'seller', None) or getattr(tx.product.store, 'owner', None)
        if seller and tx.seller_id != seller.id:
            tx.seller = seller
            tx.save(update_fields=['seller'])


class Migration(migrations.Migration):

    dependencies = [
        ('marketplace', '0009_product_is_active'),
    ]

    operations = [
        migrations.AddField(
            model_name='product',
            name='seller',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.PROTECT,
                related_name='marketplace_products',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name='transaction',
            name='seller',
            field=models.ForeignKey(
                on_delete=models.PROTECT,
                related_name='transactions_as_seller',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.RunPython(assign_sellers, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='product',
            name='seller',
            field=models.ForeignKey(
                on_delete=models.PROTECT,
                related_name='marketplace_products',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
