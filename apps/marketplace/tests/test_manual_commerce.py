import os
import shutil
import tempfile
from decimal import Decimal
from io import BytesIO
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import Http404
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image
from rest_framework import serializers, status
from rest_framework.test import APIClient

from apps.marketplace.models import Product, Store, Transaction
from apps.marketplace.serializers import MAX_PAYMENT_PROOF_DIMENSION, PaymentProofUploadSerializer
from apps.marketplace.views import _allowed_proof_mime
from smartlocker.storage import private_media_storage

_PRIVATE_STORAGE_CACHED_ATTRIBUTES = ('base_location', 'location')

def make_image_upload(name='proof.jpg', *, size=(8, 8), fmt='JPEG', color=(180, 40, 40), content_type='image/jpeg'):
    """Build a real, decodable in-memory image upload for ImageField validation."""
    buffer = BytesIO()
    Image.new('RGB', size, color).save(buffer, format=fmt)
    return SimpleUploadedFile(name, buffer.getvalue(), content_type=content_type)

def iter_strings(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from iter_strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from iter_strings(item)
    elif isinstance(value, str):
        yield value

class PrivateMediaIsolationMixin:
    """Redirect the shared private-storage singleton to a throwaway directory.

    ``private_media_storage`` binds its location at import time and caches
    ``base_location``/``location`` as ``cached_property`` values, so
    ``override_settings(PRIVATE_MEDIA_ROOT=...)`` alone would not isolate writes.
    """

    def setUp(self):
        super().setUp()
        directory = tempfile.mkdtemp(prefix='marketplace-private-')
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        original_location = private_media_storage._location
        cached = {
            attribute: private_media_storage.__dict__.pop(attribute)
            for attribute in _PRIVATE_STORAGE_CACHED_ATTRIBUTES
            if attribute in private_media_storage.__dict__
        }
        private_media_storage._location = directory

        def restore():
            private_media_storage._location = original_location
            for attribute in _PRIVATE_STORAGE_CACHED_ATTRIBUTES:
                private_media_storage.__dict__.pop(attribute, None)
            private_media_storage.__dict__.update(cached)

        self.addCleanup(restore)
        self.private_media_root = directory

@override_settings(MANUAL_PAYMENT_INSTRUCTIONS='Transfer to test account 1234')
class ManualCommerceTests(PrivateMediaIsolationMixin, TestCase):
    def setUp(self):
        super().setUp()
        User = get_user_model()
        self.seller = User.objects.create_user(
            username='seller',
            email='seller@example.com',
            password='test-password',
            role=User.Role.SELLER,
        )
        self.buyer = User.objects.create_user(
            username='buyer',
            email='buyer@example.com',
            password='test-password',
            role=User.Role.BUYER,
        )
        self.other_buyer = User.objects.create_user(
            username='other-buyer',
            email='other-buyer@example.com',
            password='test-password',
            role=User.Role.BUYER,
        )
        self.other_seller = User.objects.create_user(
            username='other-seller',
            email='other-seller@example.com',
            password='test-password',
            role=User.Role.SELLER,
        )
        self.staff = User.objects.create_user(
            username='staff',
            email='staff@example.com',
            password='test-password',
            role=User.Role.ADMIN,
            is_staff=True,
        )
        store = Store.objects.create(owner=self.seller, name='Seller store')
        self.product = Product.objects.create(
            store=store,
            seller=self.seller,
            name='Parcel',
            price=Decimal('25.00'),
            stock=3,
            description='Test parcel',
        )
        self.buyer_client = APIClient()
        self.buyer_client.force_authenticate(self.buyer)
        self.other_buyer_client = APIClient()
        self.other_buyer_client.force_authenticate(self.other_buyer)
        self.seller_client = APIClient()
        self.seller_client.force_authenticate(self.seller)
        self.other_seller_client = APIClient()
        self.other_seller_client.force_authenticate(self.other_seller)
        self.staff_client = APIClient()
        self.staff_client.force_authenticate(self.staff)
        self.create_url = reverse('create-transaction')

    def create_purchase(self, quantity=1):
        return self.buyer_client.post(
            self.create_url,
            {'product_id': self.product.pk, 'quantity': quantity},
            format='json',
        )

    def test_purchase_reserves_stock_and_returns_configured_instructions(self):
        response = self.create_purchase(quantity=2)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data['manual_payment_instructions'], 'Transfer to test account 1234')
        purchase = Transaction.objects.get(pk=response.data['id'])
        self.assertEqual(purchase.status, Transaction.TransactionStatus.PENDING)
        self.assertEqual(purchase.total_price, Decimal('50.00'))
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 1)

    @override_settings(MANUAL_PAYMENT_INSTRUCTIONS='')
    def test_missing_instructions_fails_before_reserving_stock(self):
        response = self.create_purchase()

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertEqual(Transaction.objects.count(), 0)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 3)

    def test_invalid_quantities_do_not_create_transactions_or_change_stock(self):
        for quantity in (0, -1, 'not-an-integer'):
            with self.subTest(quantity=quantity):
                response = self.create_purchase(quantity=quantity)
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        self.assertEqual(Transaction.objects.count(), 0)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 3)

    def test_only_buyer_can_submit_proof_and_proof_is_single_submission(self):
        response = self.create_purchase()
        purchase_id = response.data['id']
        url = reverse('transaction-payment-proof', args=[purchase_id])
        proof = lambda: make_image_upload()

        unauthorized = self.other_buyer_client.post(url, {'payment_proof': proof()}, format='multipart')
        self.assertEqual(unauthorized.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(
            Transaction.objects.get(pk=purchase_id).status,
            Transaction.TransactionStatus.PENDING,
        )

        submitted = self.buyer_client.post(url, {'payment_proof': proof()}, format='multipart')
        self.assertEqual(submitted.status_code, status.HTTP_200_OK, submitted.data)
        self.assertEqual(submitted.data['status'], Transaction.TransactionStatus.NEED_VERIFICATION)

        duplicate = self.buyer_client.post(url, {'payment_proof': proof()}, format='multipart')
        self.assertEqual(duplicate.status_code, status.HTTP_409_CONFLICT)

    def test_rejected_payment_releases_reserved_inventory_once(self):
        response = self.create_purchase(quantity=2)
        purchase = Transaction.objects.get(pk=response.data['id'])
        upload_url = reverse('transaction-payment-proof', args=[purchase.pk])
        self.buyer_client.post(
            upload_url,
            {'payment_proof': make_image_upload()},
            format='multipart',
        )
        reject_url = reverse('transaction-reject', args=[purchase.pk])

        rejected = self.seller_client.post(reject_url)
        self.assertEqual(rejected.status_code, status.HTTP_200_OK, rejected.data)
        purchase.refresh_from_db()
        self.assertEqual(purchase.status, Transaction.TransactionStatus.REJECTED)
        self.assertIsNotNone(purchase.inventory_released_at)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 3)

        repeated = self.seller_client.post(reject_url)
        self.assertEqual(repeated.status_code, status.HTTP_200_OK)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 3)

    def _upload_proof(self, purchase_id, upload=None):
        url = reverse('transaction-payment-proof', args=[purchase_id])
        return self.buyer_client.post(
            url,
            {'payment_proof': upload if upload is not None else make_image_upload()},
            format='multipart',
        )

    def _private_files(self):
        files = []
        for root, _dirs, names in os.walk(self.private_media_root):
            files.extend(os.path.join(root, name) for name in names)
        return files

    def test_proof_upload_is_stored_privately_and_never_publicly_exposed(self):
        response = self.create_purchase()
        purchase_id = response.data['id']

        uploaded = self._upload_proof(purchase_id)
        self.assertEqual(uploaded.status_code, status.HTTP_200_OK, uploaded.data)
        purchase = Transaction.objects.get(pk=purchase_id)
        self.assertEqual(purchase.status, Transaction.TransactionStatus.NEED_VERIFICATION)
        self.assertTrue(purchase.payment_proof.name.startswith('payment_proofs/'))
        # The bytes really landed in the private root, not a public media dir.
        self.assertTrue(
            os.path.exists(os.path.join(self.private_media_root, purchase.payment_proof.name)),
            self._private_files(),
        )
        # The private backend refuses to mint a public URL at all.
        with self.assertRaises(ValueError):
            purchase.payment_proof.storage.url(purchase.payment_proof.name)

        stored_name = purchase.payment_proof.name
        detail_url = reverse('transaction-detail', args=[purchase_id])
        list_url = reverse('transaction-list')
        for label, client in (('buyer', self.buyer_client), ('seller', self.seller_client)):
            for endpoint in (detail_url, list_url):
                with self.subTest(role=label, endpoint=endpoint):
                    payload = client.get(endpoint)
                    self.assertEqual(payload.status_code, status.HTTP_200_OK, payload.data)
                    body = payload.data if isinstance(payload.data, list) else [payload.data]
                    self.assertNotIn('payment_proof', body[0])
                    self.assertNotIn('payment_proof_url', body[0])
                    strings = list(iter_strings(payload.data))
                    self.assertNotIn(stored_name, strings)
                    for value in strings:
                        self.assertNotIn('/media/', value)
                        self.assertNotIn('private_media', value)

    def test_invalid_proof_uploads_are_rejected_without_state_change(self):
        response = self.create_purchase()
        purchase_id = response.data['id']

        invalid_uploads = {
            'non_image': SimpleUploadedFile('proof.jpg', b'not-an-image', content_type='image/jpeg'),
            'oversized': make_image_upload(
                name='proof.bmp', size=(1400, 1400), fmt='BMP', content_type='image/bmp'
            ),
        }
        for label, upload in invalid_uploads.items():
            with self.subTest(case=label):
                rejected = self._upload_proof(purchase_id, upload)
                self.assertEqual(rejected.status_code, status.HTTP_400_BAD_REQUEST, rejected.data)

                purchase = Transaction.objects.get(pk=purchase_id)
                self.assertEqual(purchase.status, Transaction.TransactionStatus.PENDING)
                self.assertFalse(purchase.payment_proof)
                self.assertIsNone(purchase.payment_proof_uploaded_at)

        # Nothing was persisted and no private file was written.
        self.assertEqual(self._private_files(), [])

    def test_oversized_proof_is_rejected_and_a_valid_retry_still_succeeds(self):
        response = self.create_purchase()
        purchase_id = response.data['id']

        oversized = self._upload_proof(
            purchase_id,
            make_image_upload(name='proof.bmp', size=(1400, 1400), fmt='BMP', content_type='image/bmp'),
        )
        self.assertEqual(oversized.status_code, status.HTTP_400_BAD_REQUEST, oversized.data)
        self.assertEqual(
            Transaction.objects.get(pk=purchase_id).status,
            Transaction.TransactionStatus.PENDING,
        )

        retried = self._upload_proof(purchase_id)
        self.assertEqual(retried.status_code, status.HTTP_200_OK, retried.data)
        self.assertEqual(
            Transaction.objects.get(pk=purchase_id).status,
            Transaction.TransactionStatus.NEED_VERIFICATION,
        )

    def test_proof_dimension_boundary_rejects_extremes_and_accepts_normal_sizes(self):
        # A tiny encoded file can still declare a huge canvas; the per-side cap
        # must reject it while a file whose width *or* height crosses the
        # boundary is caught.  4097x1 stays a few dozen bytes, so no large pixel
        # buffer is ever allocated by the test.
        response = self.create_purchase()
        purchase_id = response.data['id']

        over_width = make_image_upload(
            name='wide.png', size=(MAX_PAYMENT_PROOF_DIMENSION + 1, 1), fmt='PNG', content_type='image/png'
        )
        over_height = make_image_upload(
            name='tall.png', size=(1, MAX_PAYMENT_PROOF_DIMENSION + 1), fmt='PNG', content_type='image/png'
        )
        for label, upload in (('width', over_width), ('height', over_height)):
            with self.subTest(boundary=label):
                rejected = self._upload_proof(purchase_id, upload)
                self.assertEqual(rejected.status_code, status.HTTP_400_BAD_REQUEST, rejected.data)

        purchase = Transaction.objects.get(pk=purchase_id)
        self.assertEqual(purchase.status, Transaction.TransactionStatus.PENDING)
        self.assertFalse(purchase.payment_proof)
        self.assertIsNone(purchase.payment_proof_uploaded_at)
        self.assertEqual(self._private_files(), [])

        # Exactly at the cap (and ordinary photos) still pass, and the retry
        # proves a rejected upload left no residual state behind.
        at_limit = make_image_upload(
            name='limit.png', size=(MAX_PAYMENT_PROOF_DIMENSION, 1), fmt='PNG', content_type='image/png'
        )
        accepted = self._upload_proof(purchase_id, at_limit)
        self.assertEqual(accepted.status_code, status.HTTP_200_OK, accepted.data)

    def test_proof_dimension_check_reads_metadata_without_decoding_pixels(self):
        # Pillow parses the header lazily: ``Image.open`` yields ``size`` without
        # expanding pixels, so a boundary-crossing image is rejected without the
        # serializer ever loading (or allocating) its pixel buffer.
        upload = make_image_upload(
            name='wide.png', size=(MAX_PAYMENT_PROOF_DIMENSION + 1, 1), fmt='PNG', content_type='image/png'
        )
        with mock.patch.object(Image.Image, 'load', side_effect=AssertionError('pixels decoded')):
            with self.assertRaises(serializers.ValidationError) as caught:
                PaymentProofUploadSerializer().validate_payment_proof(upload)
        self.assertIn(str(MAX_PAYMENT_PROOF_DIMENSION), str(caught.exception.detail))

    def test_proof_dimension_check_preserves_upload_position(self):
        upload = make_image_upload()
        upload.seek(0)
        serializer = PaymentProofUploadSerializer()

        serializer.validate_payment_proof(upload)
        self.assertEqual(upload.tell(), 0)

        upload.seek(4)
        serializer.validate_payment_proof(upload)
        self.assertEqual(upload.tell(), 4)

    def test_proof_dimension_validation_error_is_a_field_level_message(self):
        upload = make_image_upload(
            name='wide.png', size=(MAX_PAYMENT_PROOF_DIMENSION + 1, 1), fmt='PNG', content_type='image/png'
        )
        serializer = PaymentProofUploadSerializer(data={'payment_proof': upload})

        self.assertFalse(serializer.is_valid())
        self.assertIn('payment_proof', serializer.errors)
        message = ' '.join(str(item) for item in serializer.errors['payment_proof'])
        self.assertIn('dimensions', message.lower())

    def test_payment_proof_download_streams_private_bytes_to_seller_only(self):
        response = self.create_purchase()
        purchase_id = response.data['id']
        upload = make_image_upload(name='proof.png', size=(8, 8), fmt='PNG', content_type='image/png')
        proof_bytes = upload.read()
        upload.seek(0)
        self.assertEqual(self._upload_proof(purchase_id, upload).status_code, status.HTTP_200_OK)
        url = reverse('transaction-payment-proof', args=[purchase_id])

        # The buyer and an unrelated seller must never read the proof.
        for label, client in (('buyer', self.buyer_client), ('foreign-seller', self.other_seller_client)):
            with self.subTest(role=label):
                denied = client.get(url)
                self.assertIn(
                    denied.status_code,
                    (status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND),
                    denied.data,
                )

        allowed = self.seller_client.get(url)
        self.assertEqual(allowed.status_code, status.HTTP_200_OK)
        self.assertEqual(allowed['Content-Type'], 'image/png')
        self.assertEqual(allowed['Cache-Control'], 'private, no-store')
        self.assertEqual(allowed['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(allowed['Content-Disposition'], 'inline; filename="payment-proof"')
        # The stored storage name must not leak through any response header.
        purchase = Transaction.objects.get(pk=purchase_id)
        for header, value in allowed.items():
            self.assertNotIn(purchase.payment_proof.name, value)
        self.assertEqual(b''.join(allowed.streaming_content), proof_bytes)

    def test_proof_upload_rejects_formats_the_viewer_cannot_serve(self):
        # The upload allowlist and the download mapping are the same, so a
        # decodable but unsupported format (BMP/GIF/TIFF) is a clear 400 rather
        # than being stored unreadable.
        response = self.create_purchase()
        purchase_id = response.data['id']

        unsupported = {
            'bmp': ('BMP', 'image/bmp'),
            'gif': ('GIF', 'image/gif'),
            'tiff': ('TIFF', 'image/tiff'),
        }
        for label, (fmt, content_type) in unsupported.items():
            with self.subTest(format=label):
                upload = make_image_upload(
                    name=f'proof.{label}', size=(8, 8), fmt=fmt, content_type=content_type
                )
                rejected = self._upload_proof(purchase_id, upload)
                self.assertEqual(rejected.status_code, status.HTTP_400_BAD_REQUEST, rejected.data)
                message = ' '.join(str(item) for item in rejected.data['payment_proof'])
                self.assertIn('JPEG', message)

        self.assertEqual(self._private_files(), [])
        self.assertEqual(
            Transaction.objects.get(pk=purchase_id).status,
            Transaction.TransactionStatus.PENDING,
        )

    def test_payment_proof_download_denied_for_downgraded_seller(self):
        response = self.create_purchase()
        purchase_id = response.data['id']
        self._upload_proof(purchase_id)
        url = reverse('transaction-payment-proof', args=[purchase_id])

        # Sanity: the owning seller can read it.
        self.assertEqual(self.seller_client.get(url).status_code, status.HTTP_200_OK)

        # After a role downgrade the same account is no longer a seller and must
        # lose access even though it still owns the transaction.
        self.seller.role = get_user_model().Role.BUYER
        self.seller.save(update_fields=['role'])
        denied = self.seller_client.get(url)
        self.assertEqual(denied.status_code, status.HTTP_403_FORBIDDEN)

    def test_payment_proof_download_without_proof_is_not_found(self):
        response = self.create_purchase()
        url = reverse('transaction-payment-proof', args=[response.data['id']])

        missing = self.seller_client.get(url)
        self.assertEqual(missing.status_code, status.HTTP_404_NOT_FOUND)

    def test_payment_proof_download_with_dangling_stored_file_is_not_found(self):
        response = self.create_purchase()
        purchase_id = response.data['id']
        self._upload_proof(purchase_id)
        purchase = Transaction.objects.get(pk=purchase_id)
        os.remove(os.path.join(self.private_media_root, purchase.payment_proof.name))

        url = reverse('transaction-payment-proof', args=[purchase_id])
        dangling = self.seller_client.get(url)
        self.assertEqual(dangling.status_code, status.HTTP_404_NOT_FOUND)

    def test_payment_proof_download_is_allowed_for_staff_and_requires_authentication(self):
        response = self.create_purchase()
        purchase_id = response.data['id']
        self._upload_proof(purchase_id)
        url = reverse('transaction-payment-proof', args=[purchase_id])

        staff = self.staff_client.get(url)
        self.assertEqual(staff.status_code, status.HTTP_200_OK)
        self.assertEqual(staff['Content-Type'], 'image/jpeg')

        anonymous = APIClient().get(url)
        self.assertIn(
            anonymous.status_code,
            (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN),
        )

    def test_allowed_proof_mime_closes_handle_when_header_is_rejected(self):
        # A non-image payload must be rejected as 404 *and* have its handle
        # closed, so a rejected read cannot leak a file descriptor.
        handle = BytesIO(b'not-an-image')
        closed = False
        original_close = handle.close

        def record_close():
            nonlocal closed
            closed = True
            original_close()

        handle.close = record_close
        with self.assertRaises(Http404):
            _allowed_proof_mime(handle)
        self.assertTrue(closed)

    def test_allowed_proof_mime_closes_handle_for_disallowed_image_format(self):
        # A decodable but disallowed format (BMP) is a 404, and the handle is
        # closed rather than left dangling.
        buffer = BytesIO()
        Image.new('RGB', (4, 4), (1, 2, 3)).save(buffer, format='BMP')
        handle = BytesIO(buffer.getvalue())
        closed = False
        original_close = handle.close

        def record_close():
            nonlocal closed
            closed = True
            original_close()

        handle.close = record_close
        with self.assertRaises(Http404):
            _allowed_proof_mime(handle)
        self.assertTrue(closed)

    def test_allowed_proof_mime_rewinds_and_keeps_handle_open_on_success(self):
        buffer = BytesIO()
        Image.new('RGB', (4, 4), (1, 2, 3)).save(buffer, format='JPEG')
        handle = BytesIO(buffer.getvalue())
        try:
            self.assertEqual(_allowed_proof_mime(handle), 'image/jpeg')
            # Rewound so the whole file streams, and still open for FileResponse.
            self.assertEqual(handle.tell(), 0)
            self.assertFalse(handle.closed)
            self.assertEqual(handle.read(), buffer.getvalue())
        finally:
            handle.close()
