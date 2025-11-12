from django.conf import settings
from drf_spectacular.utils import extend_schema
from rest_framework import permissions, status
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from .serializers import IoTEventSerializer, IoTIngestSerializer


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


def _require_device_token(request) -> None:
    expected = getattr(settings, 'SMARTLOCKER_DEVICE_TOKEN', None)
    if not expected:
        return
    provided = request.headers.get('X-Device-Token')
    if provided != expected:
        raise PermissionDenied('Invalid device token')
