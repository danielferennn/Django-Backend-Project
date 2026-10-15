from django.conf import settings
from django.db import transaction as db_transaction
from django.utils import timezone

from apps.notifications.services import create_notifications

from .models import Product, Transaction


class PaymentInstructionsUnavailable(Exception):
    pass


class ProductUnavailable(Exception):
    pass


class StockUnavailable(Exception):
    pass


class InvalidTransactionState(Exception):
    pass


def create_manual_purchase(*, buyer, product_id, quantity, buyer_full_name='', shipping_address='', buyer_phone_number=''):
    instructions = settings.MANUAL_PAYMENT_INSTRUCTIONS.strip()
    if not instructions:
        raise PaymentInstructionsUnavailable
    if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity < 1:
        raise ValueError('Quantity must be a positive integer.')

    with db_transaction.atomic():
        product = (
            Product.objects.select_for_update(of=('self',))
            .filter(pk=product_id, is_active=True)
            .first()
        )
        if product is None:
            raise ProductUnavailable
        if product.stock < quantity:
            raise StockUnavailable

        product.stock -= quantity
        product.save(update_fields=['stock', 'updated_at'])
        purchase = Transaction.objects.create(
            buyer=buyer,
            seller=product.seller,
            product=product,
            quantity=quantity,
            total_price=product.price * quantity,
            buyer_full_name=buyer_full_name,
            shipping_address=shipping_address,
            buyer_phone_number=buyer_phone_number,
        )

    return purchase, instructions


def submit_payment_proof(*, transaction_id, buyer, payment_proof):
    with db_transaction.atomic():
        purchase = (
            Transaction.objects.select_for_update(of=('self',))
            .select_related('product', 'seller')
            .get(pk=transaction_id)
        )
        if purchase.buyer_id != buyer.pk:
            raise PermissionError('Only the buyer can upload payment proof.')
        if purchase.status != Transaction.TransactionStatus.PENDING:
            raise InvalidTransactionState

        purchase.payment_proof = payment_proof
        purchase.payment_proof_uploaded_at = timezone.now()
        purchase.status = Transaction.TransactionStatus.NEED_VERIFICATION
        purchase.save(update_fields=['payment_proof', 'payment_proof_uploaded_at', 'status', 'updated_at'])
        create_notifications(
            user_ids=[purchase.seller_id],
            title='Payment proof uploaded',
            body=f"Payment proof for '{purchase.product.name}' is awaiting verification.",
        )
    return purchase


def approve_manual_payment(*, transaction_id):
    with db_transaction.atomic():
        purchase = (
            Transaction.objects.select_for_update(of=('self',))
            .select_related('product')
            .get(pk=transaction_id)
        )
        if purchase.status != Transaction.TransactionStatus.NEED_VERIFICATION:
            raise InvalidTransactionState

        purchase.status = Transaction.TransactionStatus.PAYMENT_VERIFIED
        purchase.paid_at = timezone.now()
        purchase.save(update_fields=['status', 'paid_at', 'updated_at'])
        create_notifications(
            user_ids=[purchase.buyer_id],
            title='Payment approved',
            body=f"Payment for '{purchase.product.name}' was verified.",
        )
    return purchase


def reject_manual_payment(*, transaction_id):
    with db_transaction.atomic():
        purchase = (
            Transaction.objects.select_for_update(of=('self',))
            .select_related('product')
            .get(pk=transaction_id)
        )
        if purchase.status == Transaction.TransactionStatus.REJECTED:
            return purchase
        if purchase.status != Transaction.TransactionStatus.NEED_VERIFICATION:
            raise InvalidTransactionState

        if purchase.inventory_released_at is None:
            product = Product.objects.select_for_update(of=('self',)).get(pk=purchase.product_id)
            product.stock += purchase.quantity
            product.save(update_fields=['stock', 'updated_at'])
            purchase.inventory_released_at = timezone.now()

        purchase.status = Transaction.TransactionStatus.REJECTED
        purchase.save(update_fields=['status', 'inventory_released_at', 'updated_at'])
        create_notifications(
            user_ids=[purchase.buyer_id],
            title='Payment rejected',
            body=f"Payment proof for '{purchase.product.name}' was rejected.",
        )
    return purchase
