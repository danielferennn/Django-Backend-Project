from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.marketplace.models import Store, Product, Transaction
from apps.notifications.models import Notification


class MarketplaceSellerScopeTests(TestCase):
    def setUp(self):
        self.User = get_user_model()
        owner_role = getattr(self.User, 'ROLE_OWNER', self.User.Role.OWNER)
        buyer_role = getattr(self.User, 'ROLE_BUYER', self.User.Role.BUYER)
        self.seller_a = self.User.objects.create_user(
            email='seller_a@example.com',
            username='seller_a',
            password='pass123',
            role=owner_role,
        )
        self.seller_b = self.User.objects.create_user(
            email='seller_b@example.com',
            username='seller_b',
            password='pass123',
            role=owner_role,
        )
        self.buyer = self.User.objects.create_user(
            email='buyer@example.com',
            username='buyer_user',
            password='pass123',
            role=buyer_role,
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
        payment_data = {
            'payment_reference': 'ref-123',
            'qris_payload': 'payload',
            'expires_at': timezone.now(),
            'payment_url': 'https://example.com/pay/ref-123',
        }

        with patch('apps.marketplace.views.PaymentGatewayService') as pg_mock:
            pg_instance = pg_mock.return_value
            pg_instance.create_payment.return_value = (True, payment_data)
            response = self.buyer_client.post(
                create_url,
                {'product_id': self.product.id, 'quantity': 1},
                format='json',
            )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        transaction = Transaction.objects.get(id=response.data['id'])
        self.assertEqual(transaction.seller, self.seller_a)

        transaction.status = Transaction.TransactionStatus.NEED_VERIFICATION
        transaction.save(update_fields=['status'])

        list_url = '/api/v1/marketplace/transactions/?role=seller&status=PENDING_VERIFICATION'
        seller_a_resp = self.seller_a_client.get(list_url)
        self.assertEqual(seller_a_resp.status_code, status.HTTP_200_OK)
        self.assertIn(transaction.id, self._extract_ids(seller_a_resp.data))

        seller_b_resp = self.seller_b_client.get(list_url)
        self.assertEqual(seller_b_resp.status_code, status.HTTP_200_OK)
        self.assertNotIn(transaction.id, self._extract_ids(seller_b_resp.data))

        upload_url = reverse('transaction-payment-proof', args=[transaction.id])
        proof_file = SimpleUploadedFile('proof.jpg', b'filecontent', content_type='image/jpeg')
        proof_resp = self.buyer_client.post(upload_url, {'payment_proof': proof_file}, format='multipart')
        self.assertEqual(proof_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(Notification.objects.filter(user=self.seller_a).count(), 1)
        self.assertEqual(Notification.objects.filter(user=self.seller_b).count(), 0)

        approve_url = reverse('transaction-approve', args=[transaction.id])
        seller_b_approve = self.seller_b_client.post(approve_url)
        self.assertEqual(seller_b_approve.status_code, status.HTTP_403_FORBIDDEN)

        seller_a_approve = self.seller_a_client.post(approve_url)
        self.assertEqual(seller_a_approve.status_code, status.HTTP_200_OK)
