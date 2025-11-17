from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from apps.marketplace.models import Product, Store


class ProductVisibilityTests(TestCase):
    def setUp(self):
        self.User = get_user_model()
        self.owner_role = getattr(self.User, 'ROLE_OWNER', self.User.Role.OWNER)
        self.buyer_role = getattr(self.User, 'ROLE_BUYER', self.User.Role.BUYER)

        self.seller_a = self.User.objects.create_user(
            email='seller_a@example.com',
            username='seller_a',
            password='pass123',
            role=self.owner_role,
        )
        self.seller_b = self.User.objects.create_user(
            email='seller_b@example.com',
            username='seller_b',
            password='pass123',
            role=self.owner_role,
        )
        self.buyer = self.User.objects.create_user(
            email='buyer@example.com',
            username='buyer',
            password='pass123',
            role=self.buyer_role,
        )

        self.store_a = Store.objects.create(owner=self.seller_a, name='Store A')
        self.store_b = Store.objects.create(owner=self.seller_b, name='Store B')

        self.product_active_a = Product.objects.create(
            store=self.store_a,
            seller=self.seller_a,
            name='Active A',
            price=Decimal('100.00'),
            stock=10,
            description='Active product A',
            is_active=True,
        )
        self.product_inactive_a = Product.objects.create(
            store=self.store_a,
            seller=self.seller_a,
            name='Inactive A',
            price=Decimal('100.00'),
            stock=10,
            description='Inactive product A',
            is_active=False,
        )
        self.product_active_b = Product.objects.create(
            store=self.store_b,
            seller=self.seller_b,
            name='Active B',
            price=Decimal('150.00'),
            stock=8,
            description='Active product B',
            is_active=True,
        )

        self.buyer_client = APIClient()
        self.buyer_client.force_authenticate(self.buyer)

        self.seller_a_client = APIClient()
        self.seller_a_client.force_authenticate(self.seller_a)

        self.seller_b_client = APIClient()
        self.seller_b_client.force_authenticate(self.seller_b)

        self.products_url = '/api/v1/marketplace/products/'
        self.my_products_url = '/api/v1/marketplace/my-products/'

    def _extract_ids(self, data):
        if isinstance(data, list):
            return [item['id'] for item in data]
        if isinstance(data, dict):
            results = data.get('results')
            if isinstance(results, list):
                return [item['id'] for item in results]
        return []

    def test_buyer_lists_only_active_public_products(self):
        response = self.buyer_client.get(self.products_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = self._extract_ids(response.data)
        self.assertIn(self.product_active_a.id, ids)
        self.assertIn(self.product_active_b.id, ids)
        self.assertNotIn(self.product_inactive_a.id, ids)

    def test_seller_my_products_lists_only_own(self):
        response = self.seller_a_client.get(self.my_products_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = self._extract_ids(response.data)
        self.assertIn(self.product_active_a.id, ids)
        self.assertIn(self.product_inactive_a.id, ids)
        self.assertNotIn(self.product_active_b.id, ids)

    def test_seller_cannot_update_others_product(self):
        url = reverse('product-detail', args=[self.product_active_a.id])
        response = self.seller_b_client.patch(
            url,
            {'name': 'Hacked Name'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.product_active_a.refresh_from_db()
        self.assertEqual(self.product_active_a.name, 'Active A')

    def test_create_product_sets_owner_to_request_user(self):
        response = self.seller_b_client.post(
            self.products_url,
            {
                'name': 'New Product',
                'price': '200.00',
                'stock': 3,
                'description': 'New product desc',
            },
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        created = Product.objects.get(id=response.data['id'])
        self.assertEqual(created.seller, self.seller_b)
        self.assertEqual(created.store.owner, self.seller_b)

    def test_new_seller_product_visible_to_buyer(self):
        response = self.seller_a_client.post(
            self.products_url,
            {
                'name': 'Fresh Product',
                'price': '123.00',
                'stock': 2,
                'description': 'Visible to buyer',
            },
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        product_id = response.data['id']

        my_products_resp = self.seller_a_client.get(self.my_products_url)
        self.assertEqual(my_products_resp.status_code, status.HTTP_200_OK)
        self.assertIn(product_id, self._extract_ids(my_products_resp.data))

        buyer_resp = self.buyer_client.get(self.products_url)
        self.assertEqual(buyer_resp.status_code, status.HTTP_200_OK)
        self.assertIn(product_id, self._extract_ids(buyer_resp.data))

    def test_public_list_does_not_filter_by_current_user(self):
        response_a = self.seller_a_client.post(
            self.products_url,
            {
                'name': 'Seller A Product',
                'price': '50.00',
                'stock': 1,
                'description': 'A product',
            },
            format='json',
        )
        response_b = self.seller_b_client.post(
            self.products_url,
            {
                'name': 'Seller B Product',
                'price': '60.00',
                'stock': 1,
                'description': 'B product',
            },
            format='json',
        )
        self.assertEqual(response_a.status_code, status.HTTP_201_CREATED, response_a.data)
        self.assertEqual(response_b.status_code, status.HTTP_201_CREATED, response_b.data)

        buyer_resp = self.buyer_client.get(self.products_url)
        self.assertEqual(buyer_resp.status_code, status.HTTP_200_OK)
        ids = self._extract_ids(buyer_resp.data)
        self.assertIn(response_a.data['id'], ids)
        self.assertIn(response_b.data['id'], ids)
