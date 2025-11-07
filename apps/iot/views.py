from django.conf import settings
from django.contrib.auth import get_user_model
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import permissions, status
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.notifications.tasks import push_notification_task

from .models import IoTEvent
from .serializers import (
    FaceMatchResponseSerializer,
    FaceMatchSerializer,
    IoTEventSerializer,
    IoTIngestSerializer,
)


class DeviceEventIngestView(APIView):
    permission_classes = [permissions.AllowAny]

    @extend_schema(request=IoTIngestSerializer, responses=IoTEventSerializer)
    def post(self, request, *args, **kwargs):
        _require_device_token(request)
        serializer = IoTIngestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        event = serializer.save()
        output = IoTEventSerializer(event, context={'request': request})
        return Response(output.data, status=status.HTTP_201_CREATED)


class FaceMatchIngestView(APIView):
    permission_classes = [permissions.AllowAny]

    @extend_schema(
        request=FaceMatchSerializer,
        responses=OpenApiResponse(response=FaceMatchResponseSerializer, description='Face match ingestion result'),
    )
    def post(self, request, *args, **kwargs):
        _require_device_token(request)
        serializer = FaceMatchSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        confidence = float(serializer.validated_data['confidence'])
        payload = {
            'face_id': serializer.validated_data.get('face_id'),
            'confidence': confidence,
            'timestamp': serializer.validated_data['timestamp'].isoformat(),
            'image_url': serializer.validated_data.get('image_url'),
            'metadata': serializer.validated_data.get('metadata'),
        }

        user = None
        User = get_user_model()
        explicit_user_id = serializer.validated_data.get('user_id')
        if explicit_user_id is not None:
            user = User.objects.filter(id=explicit_user_id).first()
        elif payload.get('face_id'):
            user = User.objects.filter(face_id=payload['face_id']).first()

        event = IoTEvent.objects.create(
            user=user,
            event_type=IoTEvent.EventType.FACE_MATCH,
            payload={k: v for k, v in payload.items() if v is not None},
        )

        if user:
            push_notification_task.delay(
                user_id=user.id,
                title="Face Match Success",
                body=f"Wajah Anda terdeteksi dengan keyakinan {confidence:.1f}%"
            )
        else:
            # Notify superusers for unknown face events
            admin_ids = User.objects.filter(is_superuser=True).values_list('id', flat=True)
            for admin_id in admin_ids:
                push_notification_task.delay(
                    user_id=admin_id,
                    title="Unknown Face Detected",
                    body="Sistem mendeteksi wajah yang belum dikenali."
                )

        return Response({
            'status': 'face_match_logged',
            'event_id': event.id,
            'user_id': user.id if user else None,
        }, status=status.HTTP_201_CREATED)


def _require_device_token(request) -> None:
    expected = getattr(settings, 'SMARTLOCKER_DEVICE_TOKEN', None)
    if not expected:
        return
    provided = request.headers.get('X-Device-Token')
    if provided != expected:
        raise PermissionDenied('Invalid device token')
