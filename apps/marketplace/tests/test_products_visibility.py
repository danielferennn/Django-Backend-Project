import shutil
import tempfile
from decimal import Decimal
from io import BytesIO

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image as PILImage
from rest_framework import status
from rest_framework.test import APIClient

from apps.marketplace.models import Product, Store


class ProductVisibilityTests(TestCase):
    def setUp(self):
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
            username='buyer',
            password='pass123',
            role=self.User.Role.BUYER,
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

    def test_product_creation_rejects_negative_price(self):
        response = self.seller_b_client.post(
            self.products_url,
            {
                'name': 'Invalid Product',
                'price': '-0.01',
                'stock': 1,
                'description': 'Negative price must not reach the database.',
            },
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(Product.objects.filter(name='Invalid Product').exists())

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
    def test_seller_role_can_create_and_list_own_products(self):
        seller = self.User.objects.create_user(
            email='seller_role@example.com',
            username='seller_role',
            password='pass123',
            role=self.User.Role.SELLER,
        )
        client = APIClient()
        client.force_authenticate(seller)
        created = client.post(
            self.products_url,
            {'name': 'Seller Role Product', 'price': '25.00', 'stock': 2, 'description': 'Owned product'},
            format='json',
        )
        self.assertEqual(created.status_code, status.HTTP_201_CREATED, created.data)
        product = Product.objects.get(pk=created.data['id'])
        self.assertEqual(product.seller, seller)

        listed = client.get(self.my_products_url)
        self.assertEqual(listed.status_code, status.HTTP_200_OK)
        self.assertEqual(self._extract_ids(listed.data), [product.pk])


class ProductImageUpdateAuthorizationTests(TestCase):
    """Regression coverage for product-image authorization and file lifecycle.

    A product's stored image must never be deleted or replaced unless the
    update is performed by the owner *and* the update is validated and saved.
    """

    def setUp(self):
        self.User = get_user_model()

        # Store product images under an isolated temporary MEDIA_ROOT so the
        # assertions can check on-disk presence without touching real media.
        self._tmp_media = tempfile.mkdtemp(prefix='product-image-tests-')
        override = override_settings(MEDIA_ROOT=self._tmp_media)
        override.enable()
        self.addCleanup(override.disable)
        self.addCleanup(shutil.rmtree, self._tmp_media, True)

        self.owner = self.User.objects.create_user(
            email='img_owner@example.com',
            username='img_owner',
            password='pass123',
            role=self.User.Role.OWNER,
        )
        self.other_seller = self.User.objects.create_user(
            email='img_other@example.com',
            username='img_other',
            password='pass123',
            role=self.User.Role.OWNER,
        )
        self.store = Store.objects.create(owner=self.owner, name='Image Store')
        self.other_store = Store.objects.create(owner=self.other_seller, name='Other Store')

        self.product = Product.objects.create(
            store=self.store,
            seller=self.owner,
            name='Product With Image',
            price=Decimal('10.00'),
            stock=5,
            description='Has an image',
            image=self._image_upload('original.png', (255, 0, 0)),
            is_active=True,
        )
        self.product.refresh_from_db()
        self.original_name = self.product.image.name
        self.assertIsNotNone(self.original_name)

        self.owner_client = APIClient()
        self.owner_client.force_authenticate(self.owner)
        self.other_client = APIClient()
        self.other_client.force_authenticate(self.other_seller)
        self.url = reverse('product-detail', args=[self.product.id])

        # A product whose store owner differs from its seller. The store owner
        # passes ``IsStoreOwner`` object permission, so this is the case that
        # reaches the ``_ensure_owner`` gate inside ``_update``.
        self.foreign_product = Product.objects.create(
            store=self.other_store,
            seller=self.owner,
            name='Foreign Product With Image',
            price=Decimal('20.00'),
            stock=4,
            description='Listed in another store',
            image=self._image_upload('foreign-original.png', (128, 128, 0)),
            is_active=True,
        )
        self.foreign_product.refresh_from_db()
        self.foreign_original_name = self.foreign_product.image.name
        self.foreign_url = reverse('product-detail', args=[self.foreign_product.id])

    def _image_upload(self, name, color):
        buffer = BytesIO()
        PILImage.new('RGB', (2, 2), color).save(buffer, format='PNG')
        return SimpleUploadedFile(name, buffer.getvalue(), content_type='image/png')

    def _image_is_stored(self):
        self.product.refresh_from_db()
        return bool(self.product.image) and self.product.image.storage.exists(self.product.image.name)

    def test_other_seller_cannot_clear_owner_image_and_file_remains(self):
        response = self.other_client.patch(self.url, {'image': ''}, format='json')

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.product.refresh_from_db()
        self.assertEqual(self.product.image.name, self.original_name)
        self.assertTrue(self._image_is_stored())

    def test_other_seller_cannot_replace_owner_image_and_file_remains(self):
        response = self.other_client.patch(
            self.url,
            {'image': self._image_upload('attacker.png', (0, 0, 255))},
            format='multipart',
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.product.refresh_from_db()
        self.assertEqual(self.product.image.name, self.original_name)
        self.assertTrue(self._image_is_stored())

    def test_store_owner_who_is_not_seller_cannot_clear_image(self):
        # ``IsStoreOwner`` lets this request through to ``_update``, where
        # ``_ensure_owner`` must reject it *before* any file is deleted.
        response = self.other_client.patch(self.foreign_url, {'image': ''}, format='json')

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.foreign_product.refresh_from_db()
        self.assertEqual(self.foreign_product.image.name, self.foreign_original_name)
        self.assertTrue(
            self.foreign_product.image.storage.exists(self.foreign_original_name)
        )

    def test_store_owner_who_is_not_seller_cannot_replace_image(self):
        response = self.other_client.patch(
            self.foreign_url,
            {'image': self._image_upload('foreign-attacker.png', (0, 0, 255))},
            format='multipart',
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.foreign_product.refresh_from_db()
        self.assertEqual(self.foreign_product.image.name, self.foreign_original_name)
        self.assertTrue(
            self.foreign_product.image.storage.exists(self.foreign_original_name)
        )

    def test_failed_update_does_not_delete_existing_image(self):
        # Clearing the image together with an invalid field must fail validation
        # without destroying the stored file.
        response = self.owner_client.patch(
            self.url,
            {'image': '', 'price': '-1.00'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.product.refresh_from_db()
        self.assertEqual(self.product.image.name, self.original_name)
        self.assertTrue(self._image_is_stored())

    def test_failed_replacement_does_not_delete_existing_image(self):
        response = self.owner_client.patch(
            self.url,
            {'image': self._image_upload('replacement.png', (0, 255, 0)), 'price': '-1.00'},
            format='multipart',
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.product.refresh_from_db()
        self.assertEqual(self.product.image.name, self.original_name)
        self.assertTrue(self._image_is_stored())

    def test_owner_replacement_removes_previous_file_after_success(self):
        response = self.owner_client.patch(
            self.url,
            {'image': self._image_upload('success.png', (0, 255, 0))},
            format='multipart',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.product.refresh_from_db()
        self.assertNotEqual(self.product.image.name, self.original_name)
        self.assertTrue(self._image_is_stored())
        self.assertFalse(self.product.image.storage.exists(self.original_name))

    def test_owner_clear_removes_file_after_success(self):
        response = self.owner_client.patch(self.url, {'image': ''}, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.product.refresh_from_db()
        self.assertFalse(self.product.image)
        self.assertFalse(Product.objects.get(pk=self.product.pk).image.storage.exists(self.original_name))
