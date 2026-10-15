from drf_spectacular.utils import OpenApiParameter, OpenApiTypes, extend_schema
from rest_framework import permissions, serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.lockers.services import DeviceCommandNotFound, DeviceEventConflict, InvalidDeviceEvent, process_device_event

from .authentication import require_device_token
from .serializers import IoTEventSerializer, IoTIngestSerializer

# Schema-only parameter: this endpoint authenticates with a manual header check
# (not DRF auth), so the required credential must be documented explicitly.
DEVICE_TOKEN_HEADER = OpenApiParameter(
    name='X-Device-Token',
    type=str,
    location=OpenApiParameter.HEADER,
    required=True,
    description='Shared device credential; a missing/incorrect value yields 403 device_auth_failed.',
)

class IoTErrorSerializer(serializers.Serializer):
    """Schema-only body for ``Response({'error': ...})`` outcomes (no runtime use)."""

    error = serializers.CharField()

class IoTDetailSerializer(serializers.Serializer):
    """Schema-only body for DRF ``PermissionDenied`` outcomes (no runtime use)."""

    detail = serializers.CharField()

class DeviceEventIngestView(APIView):
    authentication_classes = []
    permission_classes = [permissions.AllowAny]

    @extend_schema(
        request=IoTIngestSerializer,
        parameters=[DEVICE_TOKEN_HEADER],
        responses={
            201: IoTEventSerializer,
            200: IoTEventSerializer,
            400: OpenApiTypes.OBJECT,
            403: IoTDetailSerializer,
            404: IoTErrorSerializer,
            409: OpenApiTypes.OBJECT,
        },
        description=(
            'Device event ingest. Requires the X-Device-Token header. Idempotent '
            'by event_id; a late event past expiry returns 409 with '
            'reconciliation_required.'
        ),
    )
    def post(self, request, *args, **kwargs):
        require_device_token(request)
        serializer = IoTIngestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            event, duplicate, reconciliation_required = process_device_event(
                **serializer.validated_data,
            )
        except DeviceCommandNotFound:
            return Response({'error': 'Locker command not found.'}, status=status.HTTP_404_NOT_FOUND)
        except DeviceEventConflict:
            return Response({'error': 'Event ID was already used for different event data.'}, status=status.HTTP_409_CONFLICT)
        except InvalidDeviceEvent:
            return Response({'error': 'Event is incompatible with the locker command state.'}, status=status.HTTP_409_CONFLICT)

        output = IoTEventSerializer(event).data
        if reconciliation_required:
            output['reconciliation_required'] = True
            return Response(output, status=status.HTTP_409_CONFLICT)
        return Response(output, status=status.HTTP_200_OK if duplicate else status.HTTP_201_CREATED)
