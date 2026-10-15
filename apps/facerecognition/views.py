import mimetypes

from django.core.files.base import ContentFile
from django.db import transaction
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiParameter, OpenApiTypes, extend_schema
from rest_framework import permissions, serializers, status
from rest_framework.exceptions import PermissionDenied
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.iot.authentication import require_device_token
from apps.users.models import User

from . import models, serializer
from .services import (
    FaceRecognitionUnavailable,
    InvalidFaceImage,
    decode_camera_image,
    recognize_faces,
    rebuild_face_model,
    render_recognition_image,
    validate_face_image,
)

MAX_TRAINING_IMAGES = 10

# Shared schema-only parameter: the device ingest endpoints authenticate with a
# manual header check (not DRF auth), so it must be documented explicitly.
DEVICE_TOKEN_HEADER = OpenApiParameter(
    name='X-Device-Token',
    type=str,
    location=OpenApiParameter.HEADER,
    required=True,
    description='Shared device credential; a missing/incorrect value yields 403 device_auth_failed.',
)

class FaceTrainingUploadRequestSerializer(serializers.Serializer):
    """Schema-only mirror of the multipart upload payload (no runtime use)."""

    image_list = serializers.ListField(
        child=serializers.ImageField(),
        help_text='One or more training images (1-10 per request).',
    )
    username = serializers.CharField(
        required=False,
        help_text='Target owner username; staff may upload for another owner.',
    )

class DeviceRecognitionUploadRequestSerializer(serializers.Serializer):
    """Schema-only mirror of the device ingest multipart payload (no runtime use)."""

    image = serializers.ImageField(help_text='Single camera frame to recognize.')

class FaceTrainingRecordSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    image_url = serializers.CharField(allow_null=True)
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()

class FaceTrainingRegistrationResponseSerializer(serializers.Serializer):
    status = serializers.CharField()
    message = serializers.CharField()
    data = FaceTrainingRecordSerializer(many=True)

class FaceTrainingListResponseSerializer(serializers.Serializer):
    status = serializers.CharField()
    data = FaceTrainingRecordSerializer(many=True)

class FaceAccessLogSerializer(serializers.Serializer):
    log_id = serializers.CharField()
    id_face_user = serializers.CharField(allow_null=True)
    image_url = serializers.CharField(allow_null=True)
    access_time = serializers.DateTimeField()
    status = serializers.CharField()

class FaceAccessLogListResponseSerializer(serializers.Serializer):
    status = serializers.CharField()
    data = FaceAccessLogSerializer(many=True)

class DeviceRecognitionResponseSerializer(serializers.Serializer):
    """Device ingest response: one authorization decision per submitted image."""

    log_id = serializers.CharField()
    status = serializers.CharField()
    confidence = serializers.FloatField(allow_null=True)

class FaceErrorSerializer(serializers.Serializer):
    """Body shape for ``Response({'error': ...})`` responses."""

    error = serializers.CharField()

class FaceDetailSerializer(serializers.Serializer):
    """Body shape for DRF ``PermissionDenied``/``Http404`` responses."""

    detail = serializers.CharField()

def _face_owner(request, username=None):
    target = request.user
    if username:
        target = get_object_or_404(User, username=username)
        if not request.user.is_staff and target.pk != request.user.pk:
            raise PermissionDenied('Face data can only be accessed by its owner.')
    if target.role != User.Role.OWNER:
        raise PermissionDenied('Face data is available only to owner accounts.')
    if not target.face_id or not target.face_id.isdigit():
        raise PermissionDenied('This account is not configured for face recognition.')
    return target

def _check_image_owner(request, owner):
    if not request.user.is_staff and owner.pk != request.user.pk:
        raise PermissionDenied('Face data can only be accessed by its owner.')

def _private_file_response(image):
    """Stream a private upload through an authenticated, non-cacheable response."""
    if not image:
        raise Http404
    try:
        stream = image.open('rb')
    except FileNotFoundError as exc:
        raise Http404 from exc
    response = FileResponse(
        stream,
        content_type=mimetypes.guess_type(image.name)[0] or 'application/octet-stream',
    )
    response['Cache-Control'] = 'private, no-store'
    response['X-Content-Type-Options'] = 'nosniff'
    return response

def _owner_label_map():
    """Active OWNER labels eligible for authorization, keyed by face id integer.

    Inactive or downgraded owners are excluded so their previously trained label
    fails closed instead of being reported as authorized.
    """
    owners = (
        User.objects.filter(
            role=User.Role.OWNER,
            is_active=True,
            face_id__isnull=False,
        )
        .exclude(face_id='')
    )
    return {
        int(owner.face_id): owner.face_id
        for owner in owners
        if owner.face_id.isdigit()
    }

class Createimagetrainingusernew(APIView):
    parser_classes = [MultiPartParser, FormParser]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'face_training'

    @extend_schema(
        request=FaceTrainingUploadRequestSerializer,
        responses={
            201: FaceTrainingRegistrationResponseSerializer,
            400: OpenApiTypes.OBJECT,
            403: FaceDetailSerializer,
            404: FaceDetailSerializer,
            503: FaceErrorSerializer,
        },
        description=(
            'Register 1-10 face training images for an owner account and rebuild '
            'the private recognition model. Owner-only; staff may target another '
            'owner via the username field.'
        ),
    )
    def post(self, request):
        target = _face_owner(request, request.data.get('username'))
        images = request.FILES.getlist('image_list')
        if not images or len(images) > MAX_TRAINING_IMAGES:
            return Response(
                {'error': f'Upload between 1 and {MAX_TRAINING_IMAGES} training images.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        uploads = []
        for image in images:
            upload_serializer = serializer.FaceImageUploadSerializer(data={'image_user': image})
            if not upload_serializer.is_valid():
                return Response(upload_serializer.errors, status=status.HTTP_400_BAD_REQUEST)
            try:
                validate_face_image(upload_serializer.validated_data['image_user'])
            except FaceRecognitionUnavailable as exc:
                return Response({'error': str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
            except InvalidFaceImage as exc:
                return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
            uploads.append(upload_serializer.validated_data['image_user'])

        created = []
        try:
            with transaction.atomic():
                owner = self._lock_owner(target)
                existing = models.Datawajahnew.objects.filter(user=owner).count()
                if existing + len(uploads) > MAX_TRAINING_IMAGES:
                    return Response(
                        {
                            'error': (
                                f'A maximum of {MAX_TRAINING_IMAGES} training images per '
                                'owner is allowed; delete existing images before uploading more.'
                            ),
                        },
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                for image in uploads:
                    record = models.Datawajahnew(user=owner, image_user=image)
                    record.save()
                    created.append(record)
                if rebuild_face_model() < 1:
                    raise InvalidFaceImage('No valid owner face data is available for training.')
        except FaceRecognitionUnavailable as exc:
            self._cleanup(created)
            return Response({'error': str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        except InvalidFaceImage as exc:
            self._cleanup(created)
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception:
            self._cleanup(created)
            raise

        data = serializer.Imagedatawajahserializernew(
            created,
            many=True,
            context={'request': request},
        )
        return Response(
            {'status': 'success', 'message': 'Face training data registered.', 'data': data.data},
            status=status.HTTP_201_CREATED,
        )

    @staticmethod
    def _lock_owner(target):
        """Lock the owner row and re-verify OWNER/active eligibility.

        The batch cap must be enforced against a stable count, and a concurrent
        downgrade/deactivation must not slip a write through between the initial
        check and the insert, so the owner row is locked and re-checked here.
        """
        owner = User.objects.select_for_update().get(pk=target.pk)
        if owner.role != User.Role.OWNER or not owner.is_active:
            raise PermissionDenied('Face data is available only to active owner accounts.')
        return owner

    @staticmethod
    def _cleanup(records):
        for record in records:
            if record.image_user:
                record.image_user.storage.delete(record.image_user.name)

class Getimageexistsuser(APIView):
    @extend_schema(
        parameters=[
            OpenApiParameter(
                name='username',
                type=str,
                location=OpenApiParameter.QUERY,
                required=False,
                description='Owner username to inspect; staff may target another owner.',
            ),
        ],
        responses={
            200: FaceTrainingListResponseSerializer,
            403: FaceDetailSerializer,
            404: OpenApiTypes.OBJECT,
        },
        description='List the registered face training images for an owner account.',
    )
    def get(self, request):
        target = _face_owner(request, request.query_params.get('username'))
        records = models.Datawajahnew.objects.filter(user=target).order_by('created_at', 'pk')
        if not records.exists():
            return Response({'error': 'No face images are registered.'}, status=status.HTTP_404_NOT_FOUND)
        data = serializer.Imagedatawajahserializernew(
            records,
            many=True,
            context={'request': request},
        )
        return Response({'status': 'success', 'data': data.data}, status=status.HTTP_200_OK)

class FaceTrainingImageView(APIView):
    def get_record(self, request, pk):
        record = get_object_or_404(models.Datawajahnew.objects.select_related('user'), pk=pk)
        _check_image_owner(request, record.user)
        return record

    @extend_schema(
        responses={(200, 'image/*'): OpenApiTypes.BINARY, 403: FaceDetailSerializer, 404: FaceDetailSerializer},
        description=(
            'Stream a private face-training image. Owner-only (or staff); served '
            'with Cache-Control: private, no-store and no public storage URL.'
        ),
    )
    def get(self, request, pk):
        return _private_file_response(self.get_record(request, pk).image_user)

    @extend_schema(
        responses={
            204: None,
            403: FaceDetailSerializer,
            404: FaceDetailSerializer,
            409: FaceErrorSerializer,
            503: FaceErrorSerializer,
        },
        description=(
            'Delete a private face-training image and rebuild the recognition '
            'model. Owner-only (or staff).'
        ),
    )
    def delete(self, request, pk):
        self.get_record(request, pk)
        try:
            with transaction.atomic():
                record = models.Datawajahnew.objects.select_for_update().select_related('user').get(pk=pk)
                _check_image_owner(request, record.user)
                stored_name = record.image_user.name
                storage = record.image_user.storage
                record.delete()
                rebuild_face_model()
        except FaceRecognitionUnavailable as exc:
            return Response({'error': str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        except InvalidFaceImage as exc:
            return Response({'error': str(exc)}, status=status.HTTP_409_CONFLICT)
        if stored_name:
            storage.delete(stored_name)
        return Response(status=status.HTTP_204_NO_CONTENT)

class Createlogusersmartnew(APIView):
    authentication_classes = []
    permission_classes = [permissions.AllowAny]
    parser_classes = [MultiPartParser, FormParser]

    @extend_schema(
        request=DeviceRecognitionUploadRequestSerializer,
        parameters=[DEVICE_TOKEN_HEADER],
        responses={
            200: DeviceRecognitionResponseSerializer,
            400: OpenApiTypes.OBJECT,
            403: FaceDetailSerializer,
            503: FaceErrorSerializer,
        },
        description=(
            'Device face-recognition ingest. Requires the X-Device-Token header '
            '(403 device_auth_failed otherwise). Returns the authorization '
            'decision and access-log id for the submitted camera frame.'
        ),
    )
    def post(self, request):
        require_device_token(request)
        image_file = request.FILES.get('image')
        if image_file is None:
            return Response({'error': 'An image file is required.'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            image = decode_camera_image(image_file)
            results = recognize_faces(image, _owner_label_map())
        except FaceRecognitionUnavailable as exc:
            return Response({'error': str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        except InvalidFaceImage as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        authorized = len(results) == 1 and results[0]['authorized']
        face_id = str(results[0]['face_id']) if authorized else None
        confidence = results[0]['confidence'] if len(results) == 1 else None

        try:
            image_bytes = render_recognition_image(image, results, authorized)
        except (FaceRecognitionUnavailable, InvalidFaceImage) as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        log_serializer = serializer.Logsmartaccesserializernew(data={
            'id_face_user': face_id,
            'status': 'Authorized' if authorized else 'Unauthorized',
        })
        if not log_serializer.is_valid():
            return Response(log_serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        log = log_serializer.save(image=ContentFile(image_bytes, name='access.jpg'))
        return Response(
            {
                'log_id': log.log_id,
                'status': log.status,
                'confidence': confidence,
            },
            status=status.HTTP_200_OK,
        )

class Getuserlogsmartnews(APIView):
    @extend_schema(
        parameters=[
            OpenApiParameter(
                name='username',
                type=str,
                location=OpenApiParameter.QUERY,
                required=False,
                description='Owner username to inspect; staff may target another owner.',
            ),
        ],
        responses={
            200: FaceAccessLogListResponseSerializer,
            403: FaceDetailSerializer,
            404: FaceDetailSerializer,
        },
        description='List face access logs for an owner account.',
    )
    def get(self, request):
        target = _face_owner(request, request.query_params.get('username'))
        records = models.Logsmartaccess2.objects.filter(id_face_user_id=target.face_id).order_by('-access_time')
        data = serializer.Logsmartaccesserializernew(
            records,
            many=True,
            context={'request': request},
        )
        return Response({'status': 'success', 'data': data.data}, status=status.HTTP_200_OK)

class FaceAccessLogImageView(APIView):
    @extend_schema(
        responses={(200, 'image/*'): OpenApiTypes.BINARY, 403: FaceDetailSerializer, 404: FaceDetailSerializer},
        description=(
            'Stream a private face access-log image. Owner-only (or staff); '
            'unknown-face logs are staff-only. Served with Cache-Control: '
            'private, no-store.'
        ),
    )
    def get(self, request, log_id):
        record = get_object_or_404(models.Logsmartaccess2.objects.select_related('id_face_user'), log_id=log_id)
        if record.id_face_user_id is None:
            if not request.user.is_staff:
                raise PermissionDenied('Unknown-face logs are available only to staff.')
        else:
            _check_image_owner(request, record.id_face_user)
        return _private_file_response(record.image)
