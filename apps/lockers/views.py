from django.shortcuts import get_object_or_404

from drf_spectacular.utils import extend_schema
from rest_framework import generics, permissions, serializers, status
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Delivery, LockerLog, LockerRequest
from .permissions import IsCourierUser
from .serializers import (
    LockerCommandReconciliationSerializer,
    LockerLogSerializer,
    LockerRequestSerializer,
)
from .services import (
    DeviceCommandNotFound,
    InvalidLockerWorkflow,
    LockerUnavailable,
    ReconciliationDenied,
    reconcile_locker_command,
    request_delivery_inbound,
    request_delivery_retrieval,
    request_delivery_storage,
    request_delivery_transfer,
)


class ReceiptNumberSerializer(serializers.Serializer):
    """Schema-only request body for inbound delivery verification (no runtime use)."""

    receipt_number = serializers.CharField(help_text='Courier delivery receipt number to verify.')

class LockerCommandErrorSerializer(serializers.Serializer):
    """Reusable locker error body.

    ``_command_response`` returns ``{'error': ...}`` for handled failures while
    DRF returns ``{'detail': ...}`` for permission/not-found; both keys are
    optional so one schema covers either shape.
    """

    error = serializers.CharField(required=False)
    detail = serializers.CharField(required=False)

def _command_response(request, operation, **kwargs):
    try:
        command = operation(**kwargs)
    except PermissionError as exc:
        raise PermissionDenied(str(exc)) from exc
    except Delivery.DoesNotExist:
        return Response({'error': 'Delivery not found.'}, status=status.HTTP_404_NOT_FOUND)
    except LockerUnavailable:
        return Response({'error': 'No suitable locker is available.'}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
    except InvalidLockerWorkflow:
        return Response({'error': 'The delivery is not in a state that permits this operation.'}, status=status.HTTP_409_CONFLICT)
    return Response(
        LockerRequestSerializer(command, context={'request': request}).data,
        status=status.HTTP_202_ACCEPTED,
    )


class VerifyDeliveryView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsCourierUser]

    @extend_schema(
        request=ReceiptNumberSerializer,
        responses={
            202: LockerRequestSerializer,
            400: LockerCommandErrorSerializer,
            403: LockerCommandErrorSerializer,
            404: LockerCommandErrorSerializer,
            409: LockerCommandErrorSerializer,
            503: LockerCommandErrorSerializer,
        },
        description=(
            'Courier verifies an inbound delivery by receipt number and receives '
            'the queued locker command (MQTT dispatch). Courier-only.'
        ),
    )
    def post(self, request, *args, **kwargs):
        receipt_number = request.data.get('receipt_number')
        if not isinstance(receipt_number, str) or not receipt_number.strip():
            return Response({'error': 'Receipt number is required.'}, status=status.HTTP_400_BAD_REQUEST)
        delivery = get_object_or_404(
            Delivery,
            receipt_number=receipt_number.strip(),
            courier=request.user,
        )
        return _command_response(
            request,
            request_delivery_inbound,
            delivery_id=delivery.pk,
            courier=request.user,
        )


class DeliveryTransferView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsCourierUser]

    @extend_schema(
        request=None,
        responses={
            202: LockerRequestSerializer,
            403: LockerCommandErrorSerializer,
            404: LockerCommandErrorSerializer,
            409: LockerCommandErrorSerializer,
            503: LockerCommandErrorSerializer,
        },
        description='Courier transfers an inbound delivery to its storage locker; returns the queued command.',
    )
    def post(self, request, delivery_id):
        return _command_response(
            request,
            request_delivery_transfer,
            delivery_id=delivery_id,
            courier=request.user,
        )


class DeliveryStorageView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsCourierUser]

    @extend_schema(
        request=None,
        responses={
            202: LockerRequestSerializer,
            403: LockerCommandErrorSerializer,
            404: LockerCommandErrorSerializer,
            409: LockerCommandErrorSerializer,
            503: LockerCommandErrorSerializer,
        },
        description='Courier stores an inbound delivery in its assigned storage locker; returns the queued command.',
    )
    def post(self, request, delivery_id):
        return _command_response(
            request,
            request_delivery_storage,
            delivery_id=delivery_id,
            courier=request.user,
        )


class DeliveryRetrieveView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        request=None,
        responses={
            202: LockerRequestSerializer,
            403: LockerCommandErrorSerializer,
            404: LockerCommandErrorSerializer,
            409: LockerCommandErrorSerializer,
            503: LockerCommandErrorSerializer,
        },
        description='Owner retrieves their stored delivery; returns the queued locker command.',
    )
    def post(self, request, delivery_id):
        return _command_response(
            request,
            request_delivery_retrieval,
            delivery_id=delivery_id,
            owner=request.user,
        )


class LockerCommandStatusView(generics.RetrieveAPIView):
    serializer_class = LockerRequestSerializer
    permission_classes = [permissions.IsAuthenticated]
    lookup_field = 'command_id'
    lookup_url_kwarg = 'command_id'
    queryset = LockerRequest.objects.select_related('locker', 'transaction', 'delivery')

    def get_object(self):
        command = get_object_or_404(self.get_queryset(), command_id=self.kwargs['command_id'])
        user = self.request.user
        permitted = user.is_staff or command.requested_by_id == user.pk
        if command.transaction_id:
            permitted = permitted or user.pk in {command.transaction.buyer_id, command.transaction.seller_id}
        if command.delivery_id:
            permitted = permitted or user.pk in {command.delivery.owner_id, command.delivery.courier_id}
        if not permitted:
            raise PermissionDenied('You are not a participant in this locker command.')
        return command


class LockerCommandReconciliationView(APIView):
    permission_classes = [permissions.IsAdminUser]

    @extend_schema(request=LockerCommandReconciliationSerializer, responses=LockerRequestSerializer)
    def post(self, request, command_id):
        serializer = LockerCommandReconciliationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            command = reconcile_locker_command(
                command_id=command_id,
                actor=request.user,
                **serializer.validated_data,
            )
        except DeviceCommandNotFound:
            return Response({'error': 'Locker command not found.'}, status=status.HTTP_404_NOT_FOUND)
        except ReconciliationDenied:
            return Response(
                {'error': 'This command cannot be reconciled with the selected decision.'},
                status=status.HTTP_409_CONFLICT,
            )
        return Response(LockerRequestSerializer(command).data, status=status.HTTP_200_OK)


class LockerLogListView(generics.ListAPIView):
    serializer_class = LockerLogSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        queryset = LockerLog.objects.select_related('locker', 'user', 'command')
        if self.request.user.is_staff:
            return queryset
        return queryset.filter(user=self.request.user)
