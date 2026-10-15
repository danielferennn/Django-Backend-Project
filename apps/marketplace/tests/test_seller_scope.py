from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from apps.marketplace.models import Store, Product, Transaction
from apps.marketplace.tests.test_manual_commerce import PrivateMediaIsolationMixin, make_image_upload
from apps.notifications.models import Notification


@override_settings(MANUAL_PAYMENT_INSTRUCTIONS='Transfer to test account 1234')
class MarketplaceSellerScopeTests(PrivateMediaIsolationMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.User = get_user_model()
        self.seller_a = self.User.objects.create_user(
            email='seller_a@example.com',
            username='seller_a',
            password='pass123',
            role=self.User.Role.OWNER,
            first_name='Seller',
            last_name='A',
        )
        self.seller_b = self.User.objects.create_user(
            email='seller_b@example.com',
            username='seller_b',
            password='pass123',
            role=self.User.Role.OWNER,
            first_name='Seller',
            last_name='B',
        )
        self.buyer = self.User.objects.create_user(
            email='buyer@example.com',
            username='buyer_user',
            password='pass123',
            role=self.User.Role.BUYER,
        )

        self.store_a = Store.objects.create(owner=self.seller_a, name='A Store')
        self.product = Product.objects.create(
            store=self.store_a,
            seller=self.seller_a,
            name='Widget',
            price=Decimal('100.00'),
            stock=5,
            description='A useful widget',
        )

        self.buyer_client = APIClient()
        self.buyer_client.force_authenticate(self.buyer)

        self.seller_a_client = APIClient()
        self.seller_a_client.force_authenticate(self.seller_a)

        self.seller_b_client = APIClient()
        self.seller_b_client.force_authenticate(self.seller_b)

        Notification.objects.all().delete()

    def _extract_ids(self, data):
        if isinstance(data, list):
            return [item['id'] for item in data]
        if isinstance(data, dict):
            results = data.get('results')
            if isinstance(results, list):
                return [item['id'] for item in results]
        return []

    def test_seller_scoping_and_notifications(self):
        create_url = reverse('create-transaction')
        response = self.buyer_client.post(
            create_url,
            {'product_id': self.product.id, 'quantity': 1},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data['manual_payment_instructions'], 'Transfer to test account 1234')
        transaction = Transaction.objects.get(id=response.data['id'])
        self.assertEqual(transaction.seller, self.seller_a)
        self.assertEqual(transaction.status, Transaction.TransactionStatus.PENDING)

        upload_url = reverse('transaction-payment-proof', args=[transaction.id])
        proof_resp = self.buyer_client.post(
            upload_url, {'payment_proof': make_image_upload()}, format='multipart'
        )
        self.assertEqual(proof_resp.status_code, status.HTTP_200_OK)
        transaction.refresh_from_db()
        self.assertEqual(transaction.status, Transaction.TransactionStatus.NEED_VERIFICATION)

        list_url = '/api/v1/marketplace/transactions/?role=seller&status=NEED_VERIFICATION'
        seller_a_resp = self.seller_a_client.get(list_url)
        self.assertEqual(seller_a_resp.status_code, status.HTTP_200_OK)
        self.assertIn(transaction.id, self._extract_ids(seller_a_resp.data))

        seller_b_resp = self.seller_b_client.get(list_url)
        self.assertEqual(seller_b_resp.status_code, status.HTTP_200_OK)
        self.assertNotIn(transaction.id, self._extract_ids(seller_b_resp.data))
        self.assertEqual(Notification.objects.filter(user=self.seller_a).count(), 1)
        self.assertEqual(Notification.objects.filter(user=self.seller_b).count(), 0)

        approve_url = reverse('transaction-approve', args=[transaction.id])
        seller_b_approve = self.seller_b_client.post(approve_url)
        self.assertEqual(seller_b_approve.status_code, status.HTTP_403_FORBIDDEN)

        seller_a_approve = self.seller_a_client.post(approve_url)
        self.assertEqual(seller_a_approve.status_code, status.HTTP_200_OK)
        transaction.refresh_from_db()
        self.assertEqual(transaction.status, Transaction.TransactionStatus.PAYMENT_VERIFIED)
