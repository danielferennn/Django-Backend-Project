"""Permanent tests for the biometric (face-recognition) lifecycle.

Covers authentication/authorization, device-token enforcement, private image
access, upload boundaries, throttling and the enrol/delete rebuild lifecycle.
OpenCV-dependent paths are patched so the suite is deterministic without real
face images, and private-media writes are redirected to a temporary directory.
"""

import io
import os
import shutil
import stat
import tempfile
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image
from rest_framework import status
from rest_framework.test import APIClient
from rest_framework.throttling import ScopedRateThrottle

from smartlocker.storage import private_media_storage

from . import models, services, views

User = get_user_model()

DEVICE_TOKEN = 'device-token-that-is-at-least-32-chars-long'

def _png_bytes(size=(4, 4)):
    buffer = io.BytesIO()
    Image.new('RGB', size, (10, 20, 30)).save(buffer, format='PNG')
    return buffer.getvalue()

def _image(name='face.jpg', content=None):
    return SimpleUploadedFile(
        name, _png_bytes() if content is None else content, content_type='image/png',
    )

def _oversized_image(name='big.png'):
    """A decodable PNG padded past MAX_FACE_IMAGE_BYTES.

    Pillow still decodes the leading image, so the only boundary that can
    reject this upload is the explicit size check.
    """
    padding = b'\x00' * (services.MAX_FACE_IMAGE_BYTES + 1 - len(_png_bytes()))
    return SimpleUploadedFile(name, _png_bytes() + padding, content_type='image/png')

def _declared_png_bytes(width, height):
    """A tiny valid PNG that *declares* an arbitrary canvas size.

    Only the header is needed to trip the dimension guard, which is exactly the
    "decompression bomb" shape: a few dozen bytes that would expand to a huge
    buffer if handed to a decoder.
    """
    import struct
    import zlib

    def chunk(tag, data):
        return (
            struct.pack('>I', len(data)) + tag + data
            + struct.pack('>I', zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0)
    return (
        b'\x89PNG\r\n\x1a\n'
        + chunk(b'IHDR', ihdr)
        + chunk(b'IDAT', zlib.compress(b'\x00' * 8))
        + chunk(b'IEND', b'')
    )

def _bomb_image(width, height, name='bomb.png'):
    return SimpleUploadedFile(
        name, _declared_png_bytes(width, height), content_type='image/png',
    )

class FaceTestMixin:
    """Shared owner/staff fixtures plus an isolated private-media root."""

    def setUp(self):
        super().setUp()
        self.media_root = tempfile.mkdtemp(prefix='face-private-')
        self.addCleanup(shutil.rmtree, self.media_root, True)

        # ``private_media_storage`` captures its location at import time, so it
        # cannot be relocated through ``override_settings``; point it at the
        # temporary directory directly and restore afterwards.
        self._original_location = private_media_storage._location
        private_media_storage._location = self.media_root
        private_media_storage.__dict__.pop('base_location', None)
        private_media_storage.__dict__.pop('location', None)

        def _restore_storage():
            private_media_storage._location = self._original_location
            private_media_storage.__dict__.pop('base_location', None)
            private_media_storage.__dict__.pop('location', None)

        self.addCleanup(_restore_storage)

        self._model_path_patch = mock.patch.object(
            services, 'FACE_MODEL_PATH', Path(self.media_root) / 'biometrics' / 'lbph_model.xml',
        )
        self._model_path_patch.start()
        self.addCleanup(self._model_path_patch.stop)

        self.owner = User.objects.create_user(
            username='owner', email='owner@example.test', password='safe-password',
            role=User.Role.OWNER, first_name='Owner', last_name='One',
        )
        self.other_owner = User.objects.create_user(
            username='other', email='other@example.test', password='safe-password',
            role=User.Role.OWNER, first_name='Other', last_name='Two',
        )
        self.staff = User.objects.create_user(
            username='staff', email='staff@example.test', password='safe-password',
            is_staff=True,
        )
        self.client = APIClient()

    def _record(self, owner=None, name='training.jpg', content=b'face'):
        return models.Datawajahnew.objects.create(
            user=owner or self.owner,
            image_user=_image(name, content),
        )

class FaceTrainingEnrollmentTests(FaceTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.url = reverse('createimagetrainingusernew')

    def _post(self, user=None, images=None, username=None):
        if user is not None:
            self.client.force_authenticate(user)
        data = {'image_list': images if images is not None else [_image()]}
        if username is not None:
            data['username'] = username
        return self.client.post(self.url, data, format='multipart')

    def test_enrollment_requires_authentication(self):
        response = self._post(images=[_image()])
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertFalse(models.Datawajahnew.objects.exists())

    def test_enrollment_rejects_foreign_user(self):
        with mock.patch.object(views, 'validate_face_image'), \
                mock.patch.object(views, 'rebuild_face_model', return_value=1):
            response = self._post(user=self.other_owner, username=self.owner.username)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(models.Datawajahnew.objects.filter(user=self.owner).exists())

    def test_staff_may_enroll_another_owner(self):
        with mock.patch.object(views, 'validate_face_image'), \
                mock.patch.object(views, 'rebuild_face_model', return_value=1):
            response = self._post(user=self.staff, username=self.owner.username)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(models.Datawajahnew.objects.filter(user=self.owner).exists())

    def test_enrollment_rejects_non_owner_account(self):
        buyer = User.objects.create_user(
            username='buyer', email='buyer@example.test', password='safe-password',
        )
        with mock.patch.object(views, 'validate_face_image'), \
                mock.patch.object(views, 'rebuild_face_model', return_value=1):
            response = self._post(user=buyer)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_enrollment_rejects_empty_and_oversized_batches(self):
        with mock.patch.object(views, 'rebuild_face_model', return_value=1):
            empty = self._post(user=self.owner, images=[])
            self.assertEqual(empty.status_code, status.HTTP_400_BAD_REQUEST)
            too_many = self._post(
                user=self.owner,
                images=[_image(name=f'f{i}.jpg') for i in range(11)],
            )
            self.assertEqual(too_many.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(models.Datawajahnew.objects.exists())

    def test_upload_boundary_rejects_oversized_image(self):
        response = self._post(user=self.owner, images=[_oversized_image()])
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(models.Datawajahnew.objects.exists())

    def test_upload_rejects_decompression_bomb_dimensions(self):
        # 50000x50000 declared in a few dozen bytes: must be rejected before any
        # OpenCV buffer is allocated (validate_face_image is NOT patched here).
        bomb = _bomb_image(50_000, 50_000)
        self.assertLess(bomb.size, 1024)
        response = self._post(user=self.owner, images=[bomb])
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(models.Datawajahnew.objects.exists())

    def test_dimension_guard_boundaries(self):
        services._guard_dimensions(services.MAX_IMAGE_DIMENSION, 1)  # allowed
        with self.assertRaises(services.InvalidFaceImage):
            services._guard_dimensions(services.MAX_IMAGE_DIMENSION + 1, 1)
        with self.assertRaises(services.InvalidFaceImage):
            services._guard_dimensions(1, services.MAX_IMAGE_DIMENSION + 1)
        with self.assertRaises(services.InvalidFaceImage):
            services._guard_dimensions(0, 10)
        # Each side within the cap, but the product exceeds the pixel budget.
        side = int(services.MAX_IMAGE_PIXELS ** 0.5) + 1
        self.assertLessEqual(side, services.MAX_IMAGE_DIMENSION)
        with self.assertRaises(services.InvalidFaceImage):
            services._guard_dimensions(side, side)

    def test_upload_rejects_excessive_pixel_count(self):
        side = int(services.MAX_IMAGE_PIXELS ** 0.5) + 1
        response = self._post(user=self.owner, images=[_bomb_image(side, side)])
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(models.Datawajahnew.objects.exists())

    def test_enrollment_returns_503_when_opencv_unavailable(self):
        with mock.patch.object(
            views, 'validate_face_image',
            side_effect=services.FaceRecognitionUnavailable('unavailable'),
        ):
            response = self._post(user=self.owner)
        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertFalse(models.Datawajahnew.objects.exists())

    def test_enrollment_permission_failure_response_leaks_no_path(self):
        with mock.patch.object(views, 'validate_face_image'), \
                mock.patch.object(
                    views, 'rebuild_face_model',
                    side_effect=services.ModelPermissionError('x'),
                ):
            response = self._post(user=self.owner)
        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        body = response.data['error']
        self.assertNotIn(self.media_root, body)
        self.assertNotIn('biometrics', body)
        self.assertNotIn('/tmp', body)
        self.assertFalse(models.Datawajahnew.objects.exists())

    def test_failed_rebuild_cleans_up_uploaded_files(self):
        with mock.patch.object(views, 'validate_face_image'), \
                mock.patch.object(views, 'rebuild_face_model', return_value=0):
            response = self._post(user=self.owner)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(models.Datawajahnew.objects.exists())
        training_dir = Path(self.media_root) / 'face-training'
        leftovers = list(training_dir.rglob('*')) if training_dir.exists() else []
        self.assertEqual([p for p in leftovers if p.is_file()], [])

    def test_enrollment_sanitises_filename_and_uses_owner_private_path(self):
        with mock.patch.object(views, 'validate_face_image'), \
                mock.patch.object(views, 'rebuild_face_model', return_value=1):
            response = self._post(user=self.owner, images=[_image('../../evil name.jpg')])
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        record = models.Datawajahnew.objects.get()
        self.assertTrue(record.image_user.name.startswith(f'face-training/{self.owner.pk}/'))
        self.assertNotIn('..', record.image_user.name)
        self.assertNotIn('evil name', record.image_user.name)

    def test_total_per_owner_cap_rejects_second_batch(self):
        # Fill the owner up to the cap across separate requests.
        with mock.patch.object(views, 'validate_face_image'), \
                mock.patch.object(views, 'rebuild_face_model', return_value=1):
            for _ in range(views.MAX_TRAINING_IMAGES):
                response = self._post(user=self.owner, images=[_image()])
                self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(models.Datawajahnew.objects.filter(user=self.owner).count(),
                         views.MAX_TRAINING_IMAGES)

        before_ids = set(models.Datawajahnew.objects.values_list('pk', flat=True))
        with mock.patch.object(views, 'validate_face_image') as validate, \
                mock.patch.object(views, 'rebuild_face_model') as rebuild:
            response = self._post(user=self.owner, images=[_image(name='extra.jpg')])
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        # Nothing persisted and the model was not touched.
        self.assertEqual(set(models.Datawajahnew.objects.values_list('pk', flat=True)), before_ids)
        rebuild.assert_not_called()

    def test_total_cap_counts_only_the_target_owner(self):
        with mock.patch.object(views, 'validate_face_image'), \
                mock.patch.object(views, 'rebuild_face_model', return_value=1):
            for _ in range(views.MAX_TRAINING_IMAGES):
                self._post(user=self.owner, images=[_image()])
            # A different owner is unaffected by the first owner's records.
            response = self._post(user=self.other_owner, images=[_image()])
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_enrollment_rechecks_owner_eligibility_under_lock(self):
        # Downgrading the owner between request auth and the insert must be
        # rejected rather than writing records for an ineligible account.
        with mock.patch.object(views, 'validate_face_image'), \
                mock.patch.object(views, 'rebuild_face_model', return_value=1), \
                mock.patch.object(
                    views.User.objects, 'select_for_update',
                ) as select_for_update:
            User.objects.filter(pk=self.owner.pk).update(role=User.Role.BUYER)
            locked = User.objects.get(pk=self.owner.pk)
            manager = mock.Mock()
            manager.get.return_value = locked
            select_for_update.return_value = manager
            response = self._post(user=self.owner, images=[_image()])
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(models.Datawajahnew.objects.exists())

class FaceTrainingThrottleTests(FaceTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.addCleanup(cache.clear)
        self.url = reverse('createimagetrainingusernew')

    def test_enrollment_is_rate_limited(self):
        self.client.force_authenticate(self.owner)
        with mock.patch.object(views, 'validate_face_image'), \
                mock.patch.object(views, 'rebuild_face_model', return_value=1), \
                mock.patch.dict(
                    ScopedRateThrottle.THROTTLE_RATES, {'face_training': '1/hour'},
                ):
            first = self.client.post(self.url, {'image_list': [_image()]}, format='multipart')
            second = self.client.post(self.url, {'image_list': [_image()]}, format='multipart')
        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        self.assertEqual(second.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

    def test_enrollment_view_uses_face_training_scope(self):
        view = views.Createimagetrainingusernew()
        self.assertEqual(view.throttle_scope, 'face_training')
        self.assertEqual(
            view.throttle_classes, [ScopedRateThrottle],
        )

class FacePrivateImageAccessTests(FaceTestMixin, TestCase):
    def test_training_image_requires_authentication(self):
        record = self._record()
        url = reverse('face-training-image', kwargs={'pk': record.pk})
        self.assertEqual(self.client.get(url).status_code, status.HTTP_401_UNAUTHORIZED)

    def test_training_image_denied_to_foreign_owner(self):
        record = self._record()
        self.client.force_authenticate(self.other_owner)
        url = reverse('face-training-image', kwargs={'pk': record.pk})
        self.assertEqual(self.client.get(url).status_code, status.HTTP_403_FORBIDDEN)

    def test_training_image_served_to_owner_via_authenticated_endpoint(self):
        record = self._record(content=b'secret-face-bytes')
        self.client.force_authenticate(self.owner)
        url = reverse('face-training-image', kwargs={'pk': record.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(b''.join(response.streaming_content), b'secret-face-bytes')
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        self.assertEqual(response['X-Content-Type-Options'], 'nosniff')

    def test_serializer_never_exposes_public_storage_url(self):
        record = self._record()
        self.client.force_authenticate(self.owner)
        response = self.client.get(reverse('getuserimageexists'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        image_url = response.data['data'][0]['image_url']
        self.assertNotIn('/media/', image_url)
        self.assertIn(reverse('face-training-image', kwargs={'pk': record.pk}), image_url)

    def test_access_log_image_scoped_to_owner_and_staff(self):
        log = models.Logsmartaccess2.objects.create(
            id_face_user=self.owner, status='Authorized',
        )
        log.image.save('access.jpg', _image(content=b'log-bytes'), save=True)
        url = reverse('face-access-log-image', kwargs={'log_id': log.log_id})

        self.client.force_authenticate(self.other_owner)
        self.assertEqual(self.client.get(url).status_code, status.HTTP_403_FORBIDDEN)

        self.client.force_authenticate(self.owner)
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(b''.join(response.streaming_content), b'log-bytes')

    def test_unknown_face_log_visible_only_to_staff(self):
        log = models.Logsmartaccess2.objects.create(id_face_user=None, status='Unauthorized')
        log.image.save('access.jpg', _image(content=b'unknown-bytes'), save=True)
        url = reverse('face-access-log-image', kwargs={'log_id': log.log_id})

        self.client.force_authenticate(self.owner)
        self.assertEqual(self.client.get(url).status_code, status.HTTP_403_FORBIDDEN)

        self.client.force_authenticate(self.staff)
        self.assertEqual(self.client.get(url).status_code, status.HTTP_200_OK)

    def test_legacy_non_uuid_log_id_is_resolvable(self):
        # ``log_id`` is a CharField(255) with historical rows that predate the
        # UUID default, so the route must accept any string id, not just UUIDs.
        log = models.Logsmartaccess2.objects.create(
            log_id='legacy-log-0001', id_face_user=self.owner, status='Authorized',
        )
        log.image.save('access.jpg', _image(content=b'legacy-bytes'), save=True)
        url = reverse('face-access-log-image', kwargs={'log_id': 'legacy-log-0001'})

        self.client.force_authenticate(self.owner)
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(b''.join(response.streaming_content), b'legacy-bytes')

        # Foreign owners remain denied for legacy ids too.
        self.client.force_authenticate(self.other_owner)
        self.assertEqual(self.client.get(url).status_code, status.HTTP_403_FORBIDDEN)

class FaceTrainingLifecycleTests(FaceTestMixin, TestCase):
    def test_delete_rebuilds_model_and_removes_record(self):
        record = self._record()
        services.FACE_MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
        services.FACE_MODEL_PATH.write_bytes(b'model')

        self.client.force_authenticate(self.owner)
        with mock.patch.object(views, 'rebuild_face_model', return_value=0) as rebuild:
            response = self.client.delete(
                reverse('face-training-image', kwargs={'pk': record.pk}),
            )
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        rebuild.assert_called_once()
        self.assertFalse(models.Datawajahnew.objects.filter(pk=record.pk).exists())

    def test_delete_denied_to_foreign_owner(self):
        record = self._record()
        self.client.force_authenticate(self.other_owner)
        response = self.client.delete(
            reverse('face-training-image', kwargs={'pk': record.pk}),
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertTrue(models.Datawajahnew.objects.filter(pk=record.pk).exists())

    def test_rebuild_without_records_removes_stale_model(self):
        services.FACE_MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
        services.FACE_MODEL_PATH.write_bytes(b'stale')
        self.assertEqual(services.rebuild_face_model(), 0)
        self.assertFalse(services.FACE_MODEL_PATH.exists())

class _FakeRecognizer:
    """Minimal stand-in for ``cv2.face.LBPHFaceRecognizer``.

    Emulates OpenCV writing the model with umask-derived (loose) permissions so
    the surrounding hardening can be asserted.
    """

    def __init__(self, payload=b'model', loose_mode=0o644):
        self.payload = payload
        self.loose_mode = loose_mode

    def save(self, path):
        with open(path, 'wb') as handle:
            handle.write(self.payload)
        os.chmod(path, self.loose_mode)

class FaceModelPermissionTests(FaceTestMixin, TestCase):
    def _mode(self, path):
        return stat.S_IMODE(os.stat(path).st_mode)

    def test_model_directory_is_created_private(self):
        self.assertFalse(services.FACE_MODEL_PATH.parent.exists())
        services._ensure_model_directory()
        self.assertEqual(self._mode(services.FACE_MODEL_PATH.parent), services.MODEL_DIR_MODE)

    def test_existing_loose_directory_is_tightened(self):
        directory = services.FACE_MODEL_PATH.parent
        directory.mkdir(parents=True, exist_ok=True)
        os.chmod(directory, 0o755)
        services._ensure_model_directory()
        self.assertEqual(self._mode(directory), services.MODEL_DIR_MODE)

    def test_saved_model_is_private_and_leaves_no_temp_file(self):
        services._ensure_model_directory()
        services._save_model_atomically(_FakeRecognizer())
        self.assertEqual(self._mode(services.FACE_MODEL_PATH), services.MODEL_FILE_MODE)
        self.assertEqual(services.FACE_MODEL_PATH.read_bytes(), b'model')
        leftovers = [p.name for p in services.FACE_MODEL_PATH.parent.iterdir() if p.name.endswith('.tmp.xml')]
        self.assertEqual(leftovers, [])

    def test_temp_file_is_tightened_before_atomic_replace(self):
        observed = {}
        real_replace = os.replace

        def spy_replace(source, destination):
            observed['source_mode'] = self._mode(source)
            real_replace(source, destination)

        services._ensure_model_directory()
        with mock.patch.object(services.os, 'replace', side_effect=spy_replace):
            services._save_model_atomically(_FakeRecognizer())
        # The file handed to os.replace must already be private.
        self.assertEqual(observed['source_mode'], services.MODEL_FILE_MODE)

    def test_rebuild_applies_private_permissions(self):
        self._record()
        with mock.patch.object(services, '_require_dependencies'), \
                mock.patch.object(services, '_cascade', return_value=object()), \
                mock.patch.object(services, '_load_face_sample', return_value='sample'), \
                mock.patch.object(services, '_train_recognizer', return_value=_FakeRecognizer()):
            count = services.rebuild_face_model()
        self.assertEqual(count, 1)
        self.assertEqual(self._mode(services.FACE_MODEL_PATH.parent), services.MODEL_DIR_MODE)
        self.assertEqual(self._mode(services.FACE_MODEL_PATH), services.MODEL_FILE_MODE)

    def test_chmod_failure_fails_closed(self):
        services._ensure_model_directory()
        with mock.patch.object(services.os, 'chmod', side_effect=OSError('read-only fs')):
            with self.assertRaises(services.ModelPermissionError):
                services._chmod(services.FACE_MODEL_PATH, services.MODEL_FILE_MODE)

    def test_verification_rejects_over_permissive_mode(self):
        path = Path(self.media_root) / 'loose'
        path.write_bytes(b'x')
        os.chmod(path, 0o644)
        with self.assertRaises(services.ModelPermissionError):
            services._verify_private_mode(path, services.MODEL_FILE_MODE)

    def test_model_permission_error_is_a_service_unavailable(self):
        # Fail-closed must surface as 503, not a 500 or silent success.
        self.assertTrue(issubclass(services.ModelPermissionError, services.FaceRecognitionUnavailable))

    def test_permission_error_messages_do_not_leak_paths(self):
        services._ensure_model_directory()
        with mock.patch.object(services.os, 'chmod', side_effect=OSError('read-only fs')):
            with self.assertRaises(services.ModelPermissionError) as chmod_ctx:
                services._chmod(services.FACE_MODEL_PATH, services.MODEL_FILE_MODE)
        message = str(chmod_ctx.exception)
        self.assertNotIn(self.media_root, message)
        self.assertNotIn('biometrics', message)
        self.assertNotIn(str(services.FACE_MODEL_PATH), message)

        loose = Path(self.media_root) / 'loose'
        loose.write_bytes(b'x')
        os.chmod(loose, 0o644)
        with self.assertRaises(services.ModelPermissionError) as verify_ctx:
            services._verify_private_mode(loose, services.MODEL_FILE_MODE)
        verify_message = str(verify_ctx.exception)
        self.assertNotIn(self.media_root, verify_message)
        self.assertNotIn(str(loose), verify_message)

    def test_rebuild_fails_closed_when_permissions_cannot_be_applied(self):
        self._record()
        with mock.patch.object(services, '_require_dependencies'), \
                mock.patch.object(services, '_cascade', return_value=object()), \
                mock.patch.object(services, '_load_face_sample', return_value='sample'), \
                mock.patch.object(services, '_train_recognizer', return_value=_FakeRecognizer()), \
                mock.patch.object(services.os, 'chmod', side_effect=OSError('read-only fs')):
            with self.assertRaises(services.FaceRecognitionUnavailable):
                services.rebuild_face_model()

class StoredTrainingRecordGuardTests(FaceTestMixin, TestCase):
    """Stored records must be bounded before any OpenCV allocation."""

    def _rebuild_with_real_loader(self):
        """Run rebuild_face_model with the real _load_face_sample path."""
        with mock.patch.object(services, '_require_dependencies'), \
                mock.patch.object(services, '_cascade', return_value=object()), \
                mock.patch.object(services, '_train_recognizer', return_value=_FakeRecognizer()):
            return services.rebuild_face_model()

    def test_stored_bomb_image_is_skipped_without_opencv_decode(self):
        self._record(content=_declared_png_bytes(50_000, 50_000))
        with mock.patch.object(services.cv2, 'imdecode') as imdecode:
            count = self._rebuild_with_real_loader()
        self.assertEqual(count, 0)          # nothing trainable -> model removed
        imdecode.assert_not_called()         # bomb never reached the decoder
        self.assertFalse(services.FACE_MODEL_PATH.exists())

    def test_oversized_stored_file_is_skipped_without_reading(self):
        # A stored file larger than the upload cap must be skipped by size alone.
        record = self._record()
        with mock.patch.object(
            type(record.image_user), 'size',
            new_callable=mock.PropertyMock, return_value=services.MAX_FACE_IMAGE_BYTES + 1,
        ), mock.patch.object(services.cv2, 'imdecode') as imdecode:
            sample = services._load_face_sample(record, cascade=object())
        self.assertIsNone(sample)
        imdecode.assert_not_called()

    def test_valid_stored_record_still_trains(self):
        # Sanity: the new guards must not reject a normal record.
        record = self._record(content=_png_bytes())
        with mock.patch.object(services, '_detect_single_face', return_value='sample'):
            sample = services._load_face_sample(record, cascade=object())
        self.assertEqual(sample, 'sample')

class FaceModelEligibilityTests(FaceTestMixin, TestCase):
    def test_training_records_exclude_inactive_and_non_owner(self):
        active = self._record(owner=self.owner)
        inactive = self._record(owner=self.other_owner)
        User.objects.filter(pk=self.other_owner.pk).update(is_active=False)
        eligible = [record.pk for record in services._training_records()]
        self.assertEqual(eligible, [active.pk])
        self.assertNotIn(inactive.pk, eligible)

    def test_owner_label_map_excludes_inactive_owners(self):
        User.objects.filter(pk=self.owner.pk).update(is_active=False)
        label_map = views._owner_label_map()
        self.assertNotIn(int(self.owner.face_id), label_map)
        self.assertIn(int(self.other_owner.face_id), label_map)

@override_settings(SMARTLOCKER_DEVICE_TOKEN=DEVICE_TOKEN)
class DeviceRecognitionIngestTests(FaceTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.url = reverse('createlogusersmartnew')

    def test_ingest_requires_device_token(self):
        response = self.client.post(self.url, {'image': _image()}, format='multipart')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(models.Logsmartaccess2.objects.exists())

    def test_ingest_rejects_wrong_device_token(self):
        response = self.client.post(
            self.url, {'image': _image()}, format='multipart', HTTP_X_DEVICE_TOKEN='wrong',
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_ingest_rejects_missing_image(self):
        response = self.client.post(
            self.url, {}, format='multipart', HTTP_X_DEVICE_TOKEN=DEVICE_TOKEN,
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_ingest_rejects_oversized_image(self):
        response = self.client.post(
            self.url, {'image': _oversized_image()}, format='multipart',
            HTTP_X_DEVICE_TOKEN=DEVICE_TOKEN,
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_ingest_rejects_decompression_bomb(self):
        response = self.client.post(
            self.url, {'image': _bomb_image(50_000, 50_000)}, format='multipart',
            HTTP_X_DEVICE_TOKEN=DEVICE_TOKEN,
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(models.Logsmartaccess2.objects.exists())

class OwnerEligibilityReconciliationTests(FaceTestMixin, TestCase):
    """The user lifecycle must not leave a stale labelled model on disk."""

    def test_deactivation_triggers_rebuild(self):
        self._record()
        with mock.patch(
            'apps.facerecognition.services.reconcile_face_model_for_user',
        ) as reconcile:
            with self.captureOnCommitCallbacks(execute=True):
                self.owner.is_active = False
                self.owner.save(update_fields=['is_active'])
        reconcile.assert_called_once()

    def test_role_downgrade_triggers_rebuild(self):
        self._record()
        with mock.patch(
            'apps.facerecognition.services.reconcile_face_model_for_user',
        ) as reconcile:
            with self.captureOnCommitCallbacks(execute=True):
                self.owner.role = User.Role.BUYER
                self.owner.save(update_fields=['role'])
        reconcile.assert_called_once()

    def test_reactivation_triggers_rebuild(self):
        self._record()
        User.objects.filter(pk=self.owner.pk).update(is_active=False)
        self.owner.refresh_from_db()
        with mock.patch(
            'apps.facerecognition.services.reconcile_face_model_for_user',
        ) as reconcile:
            with self.captureOnCommitCallbacks(execute=True):
                self.owner.is_active = True
                self.owner.save(update_fields=['is_active'])
        reconcile.assert_called_once()

    def test_unrelated_edit_does_not_trigger_rebuild(self):
        self._record()
        with mock.patch(
            'apps.facerecognition.services.reconcile_face_model_for_user',
        ) as reconcile:
            with self.captureOnCommitCallbacks(execute=True):
                self.owner.first_name = 'Renamed'
                self.owner.save(update_fields=['first_name'])
        reconcile.assert_not_called()

    def test_ineligible_change_without_records_does_not_rebuild(self):
        with mock.patch(
            'apps.facerecognition.services.reconcile_face_model_for_user',
        ) as reconcile:
            with self.captureOnCommitCallbacks(execute=True):
                self.owner.is_active = False
                self.owner.save(update_fields=['is_active'])
        reconcile.assert_not_called()

    def test_full_save_without_eligibility_change_does_not_rebuild(self):
        self._record()
        with mock.patch(
            'apps.facerecognition.services.reconcile_face_model_for_user',
        ) as reconcile:
            with self.captureOnCommitCallbacks(execute=True):
                self.owner.first_name = 'Renamed'
                self.owner.save()  # no update_fields -> full save
        reconcile.assert_not_called()

    def test_failed_rebuild_does_not_break_user_save(self):
        self._record()
        with mock.patch(
            'apps.facerecognition.services.reconcile_face_model_for_user',
            side_effect=services.FaceRecognitionUnavailable('no model'),
        ):
            with self.captureOnCommitCallbacks(execute=True):
                self.owner.is_active = False
                self.owner.save(update_fields=['is_active'])  # must not raise
        self.owner.refresh_from_db()
        self.assertFalse(self.owner.is_active)

    def test_raw_oserror_during_rebuild_does_not_break_user_save(self):
        # A rebuild can also fail with a raw OSError/PermissionError (lock,
        # mkdir, os.replace); the on_commit callback must swallow it too.
        self._record()
        with mock.patch(
            'apps.facerecognition.services.reconcile_face_model_for_user',
            side_effect=PermissionError('read-only fs'),
        ):
            with self.captureOnCommitCallbacks(execute=True):
                self.owner.role = User.Role.BUYER
                self.owner.save(update_fields=['role'])  # must not raise
        self.owner.refresh_from_db()
        self.assertEqual(self.owner.role, User.Role.BUYER)

class AdminUserDeletionBiometricTests(FaceTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        from django.contrib import admin

        from apps.users.admin import SmartLockerUserAdmin

        self.admin_view = SmartLockerUserAdmin(User, admin.site)

    def _request(self, user):
        request = type('R', (), {})()
        request.user = user
        return request

    def test_delete_model_purges_private_images_and_rebuilds(self):
        record = self._record()
        storage = models.Datawajahnew._meta.get_field('image_user').storage
        self.assertTrue(storage.exists(record.image_user.name))

        with mock.patch('apps.facerecognition.services.rebuild_face_model') as rebuild:
            with self.captureOnCommitCallbacks(execute=True):
                self.admin_view.delete_model(self._request(self.staff), self.owner)

        self.assertFalse(User.objects.filter(pk=self.owner.pk).exists())
        self.assertFalse(storage.exists(record.image_user.name))
        rebuild.assert_called_once()

    def test_delete_queryset_purges_every_owners_images(self):
        first = self._record(owner=self.owner)
        second = self._record(owner=self.other_owner, name='other.jpg')
        storage = models.Datawajahnew._meta.get_field('image_user').storage
        queryset = User.objects.filter(pk__in=[self.owner.pk, self.other_owner.pk])

        with mock.patch('apps.facerecognition.services.rebuild_face_model') as rebuild:
            with self.captureOnCommitCallbacks(execute=True):
                self.admin_view.delete_queryset(self._request(self.staff), queryset)

        self.assertFalse(storage.exists(first.image_user.name))
        self.assertFalse(storage.exists(second.image_user.name))
        rebuild.assert_called_once()

    def test_purge_failure_is_logged_and_rebuild_still_attempted(self):
        # Deletion already committed; a purge failure must not raise nor stop
        # the model rebuild (recognition stays fail-closed either way).
        self._record()
        with mock.patch(
            'apps.facerecognition.services.purge_training_files',
            side_effect=OSError('storage offline'),
        ), mock.patch('apps.facerecognition.services.rebuild_face_model') as rebuild:
            with self.captureOnCommitCallbacks(execute=True):
                self.admin_view.delete_model(self._request(self.staff), self.owner)

        self.assertFalse(User.objects.filter(pk=self.owner.pk).exists())
        rebuild.assert_called_once()

    def test_rebuild_failure_after_delete_is_swallowed(self):
        self._record()
        with mock.patch(
            'apps.facerecognition.services.rebuild_face_model',
            side_effect=services.FaceRecognitionUnavailable('no model'),
        ):
            with self.captureOnCommitCallbacks(execute=True):
                self.admin_view.delete_model(self._request(self.staff), self.owner)
        self.assertFalse(User.objects.filter(pk=self.owner.pk).exists())

    def test_raw_oserror_after_delete_is_swallowed(self):
        # A rebuild raising a raw OSError must not surface after the delete has
        # committed (nor obscure that the deletion succeeded).
        self._record()
        with mock.patch(
            'apps.facerecognition.services.rebuild_face_model',
            side_effect=PermissionError('read-only fs'),
        ):
            with self.captureOnCommitCallbacks(execute=True):
                self.admin_view.delete_model(self._request(self.staff), self.owner)
        self.assertFalse(User.objects.filter(pk=self.owner.pk).exists())

    def test_delete_without_training_files_does_not_rebuild(self):
        # Deleting a user with no biometrics must not schedule a rebuild.
        buyer = User.objects.create_user(
            username='buyer-del', email='buyer-del@example.test', password='safe-password',
        )
        with mock.patch('apps.facerecognition.services.rebuild_face_model') as rebuild, \
                mock.patch('apps.facerecognition.services.purge_training_files') as purge:
            with self.captureOnCommitCallbacks(execute=True):
                self.admin_view.delete_model(self._request(self.staff), buyer)
        self.assertFalse(User.objects.filter(pk=buyer.pk).exists())
        rebuild.assert_not_called()
        purge.assert_not_called()

