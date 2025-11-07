import random
import string
from datetime import timedelta

from django.db import models, transaction as db_transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import generics, permissions, status, viewsets
from rest_framework.exceptions import PermissionDenied
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from drf_spectacular.utils import OpenApiResponse, extend_schema

from .models import Store, Product, Transaction
from .serializers import (
    PaymentProofUploadSerializer,
    ProductSerializer,
    StoreSerializer,
    TransactionSerializer,
    TransactionShippingSerializer,
)
from .permissions import IsStoreOwner
from .services import PaymentGatewayService
from apps.lockers.models import Locker
from apps.lockers.services import BlynkAPIService
from apps.notifications.tasks import push_notification_task

class MyStoreView(generics.RetrieveUpdateAPIView):
    serializer_class = StoreSerializer
    permission_classes = [permissions.IsAuthenticated, IsStoreOwner]
    
    def get_object(self):
        store, created = Store.objects.get_or_create(owner=self.request.user, defaults={'name': f"{self.request.user.username}'s Store"})
        self.check_object_permissions(self.request, store)
        return store

class ProductViewSet(viewsets.ModelViewSet):
    queryset = Product.objects.all()
    serializer_class = ProductSerializer
    permission_classes = [permissions.IsAuthenticated, IsStoreOwner]
    parser_classes = [MultiPartParser, FormParser]

    def get_queryset(self):
        user = self.request.user
        if not user.is_authenticated:
            return Product.objects.none()

        if getattr(user, 'role', None) == User.Role.BUYER:
            return Product.objects.all()

        if getattr(user, 'role', None) in {User.Role.OWNER, User.Role.SELLER}:
            return Product.objects.filter(store__owner=user)

        return Product.objects.all()

    def perform_create(self, serializer):
        store, _ = Store.objects.get_or_create(
            owner=self.request.user,
            defaults={'name': f"{self.request.user.username}'s Store"}
        )
        serializer.save(store=store)

    def update(self, request, *args, **kwargs):
        return self._update(request, *args, **kwargs, partial=False)

    def partial_update(self, request, *args, **kwargs):
        return self._update(request, *args, **kwargs, partial=True)

    def _update(self, request, *args, partial=False, **kwargs):
        instance = self.get_object()
        data = request.data.copy()
        delete_previous = False
        if 'image' in data and not data.get('image'):
            if instance.image:
                instance.image.delete(save=False)
            data['image'] = None
        elif request.FILES.get('image') and instance.image:
            delete_previous = True

        serializer = self.get_serializer(instance, data=data, partial=partial)
        serializer.is_valid(raise_exception=True)
        if delete_previous:
            instance.image.delete(save=False)
        self.perform_update(serializer)

        if getattr(instance, '_prefetched_objects_cache', None):
            instance._prefetched_objects_cache = {}

        return Response(serializer.data)


class TransactionViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = TransactionSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        queryset = Transaction.objects.select_related(
            'buyer', 'seller', 'product', 'product__store'
        )

        if getattr(user, 'role', None) == User.Role.BUYER:
            return queryset.filter(buyer=user).order_by('-created_at')
        if getattr(user, 'role', None) in {User.Role.OWNER, User.Role.SELLER}:
            return queryset.filter(seller=user).order_by('-created_at')
        return queryset.none()


class CreateTransactionView(generics.CreateAPIView):
    serializer_class = TransactionSerializer
    permission_classes = [permissions.IsAuthenticated]

    def create(self, request, *args, **kwargs):
        product = get_object_or_404(Product, id=request.data.get('product_id'))
        quantity = int(request.data.get('quantity', 1))
        
        if product.stock < quantity:
            return Response({"error": "Not enough stock."}, status=status.HTTP_400_BAD_REQUEST)

        buyer_full_name = request.data.get('buyer_full_name', '')
        shipping_address = request.data.get('shipping_address', '')
        buyer_phone_number = request.data.get('buyer_phone_number', '')

        with db_transaction.atomic():
            product.stock -= quantity
            product.save()
            transaction = Transaction.objects.create(
                buyer=request.user, seller=product.store.owner, product=product,
                quantity=quantity, total_price=product.price * quantity,
                buyer_full_name=buyer_full_name, shipping_address=shipping_address,
                buyer_phone_number=buyer_phone_number
            )

        pg_service = PaymentGatewayService()
        success, gateway_data = pg_service.create_payment(
            transaction_id=transaction.id, amount=transaction.total_price,
            customer_details={'email': request.user.email, 'name': request.user.username}
        )
        if not success:
            return Response({"error": "Failed to create payment session."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        transaction.payment_gateway_id = gateway_data.get('payment_reference')
        transaction.qris_payload = gateway_data.get('qris_payload')
        transaction.payment_expires_at = gateway_data.get('expires_at')
        transaction.save(update_fields=['payment_gateway_id', 'qris_payload', 'payment_expires_at'])

        serializer = self.get_serializer(transaction, context={'request': request})
        response_data = serializer.data
        response_data['payment_url'] = gateway_data.get('payment_url')
        expires_at = gateway_data.get('expires_at')
        response_data['expires_at'] = expires_at.isoformat() if expires_at else None
        return Response(response_data, status=status.HTTP_201_CREATED)

@extend_schema(exclude=True)
class PaymentWebhookView(APIView):
    permission_classes = [permissions.AllowAny]
    def post(self, request, *args, **kwargs):
        transaction = get_object_or_404(Transaction, id=request.data.get('transaction_id'))
        if request.data.get('payment_status') == 'success' and transaction.status == Transaction.TransactionStatus.PENDING:
            transaction.status = Transaction.TransactionStatus.ESCROW
            transaction.save()
            push_notification_task.delay(
                user_id=transaction.seller.id,
                title="Payment Confirmed",
                body=f"Pembayaran untuk '{transaction.product.name}' berhasil."
            )
            return Response(status=status.HTTP_200_OK)
        return Response(status=status.HTTP_400_BAD_REQUEST)

@extend_schema(exclude=True)
class SellerDepositItemView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    def post(self, request, *args, **kwargs):
        transaction = get_object_or_404(Transaction, id=request.data.get('transaction_id'))
        if request.user != transaction.seller or transaction.status != Transaction.TransactionStatus.ESCROW:
            return Response({"error": "Action not allowed."}, status=status.HTTP_403_FORBIDDEN)
        
        locker = Locker.objects.filter(type=Locker.LockerType.MARKETPLACE, status=Locker.LockerStatus.AVAILABLE).first()
        if not locker:
            return Response({"error": "No available marketplace locker."}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

        blynk_service = BlynkAPIService(token=locker.blynk_device_token)
        success, _ = blynk_service.set_virtual_pin(pin=locker.blynk_vpin_control, value=1)
        if success:
            locker.status = Locker.LockerStatus.OCCUPIED
            locker.save()
            return Response({"message": "Locker is open. Please deposit the item."}, status=status.HTTP_200_OK)
        return Response({"error": "Failed to open locker."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@extend_schema(exclude=True)
class ConfirmMarketplaceDepositWebhookView(APIView):
    permission_classes = [permissions.AllowAny]
    def post(self, request, *args, **kwargs):
        transaction = Transaction.objects.filter(status=Transaction.TransactionStatus.ESCROW).first()
        if transaction:
            otp = transaction.otp or f"{random.randint(0, 999999):06d}"
            transaction.status = Transaction.TransactionStatus.AWAITING_PICKUP
            transaction.save()
            otp = "654321"
            push_notification_task.delay(
                user_id=transaction.buyer.id,
                title="Locker Ready",
                body=f"Barang '{transaction.product.name}' siap diambil. Kode OTP: {otp}"
            )
            return Response(status=status.HTTP_200_OK)
        return Response(status=status.HTTP_204_NO_CONTENT)

@extend_schema(exclude=True)
class BuyerRetrieveItemView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    def post(self, request, *args, **kwargs):
        transaction = get_object_or_404(Transaction, id=request.data.get('transaction_id'))
        if request.user != transaction.buyer or transaction.status != Transaction.TransactionStatus.AWAITING_PICKUP:
            return Response({"error": "Action not allowed."}, status=status.HTTP_403_FORBIDDEN)
        if transaction.otp != request.data.get('otp'):
            return Response({"error": "Action not allowed or invalid OTP."}, status=status.HTTP_403_FORBIDDEN)

        locker = Locker.objects.filter(type=Locker.LockerType.MARKETPLACE, status=Locker.LockerStatus.OCCUPIED).first()
        blynk_service = BlynkAPIService(token=locker.blynk_device_token)
        success, _ = blynk_service.set_virtual_pin(pin=locker.blynk_vpin_control, value=1)
        if success:
            return Response({"message": "Locker is open. Please retrieve your item."}, status=status.HTTP_200_OK)
        return Response({"error": "Failed to open locker."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@extend_schema(exclude=True)
class ConfirmMarketplaceRetrievalWebhookView(APIView):
    permission_classes = [permissions.AllowAny]
    def post(self, request, *args, **kwargs):
        transaction = Transaction.objects.filter(status=Transaction.TransactionStatus.AWAITING_PICKUP).first()
        if transaction:
            pg_service = PaymentGatewayService()
            success, _ = pg_service.release_escrow(transaction.id)
            if success:
                transaction.status = Transaction.TransactionStatus.RELEASED
                transaction.otp = None
                transaction.save(update_fields=['status', 'otp'])
                locker = Locker.objects.filter(type=Locker.LockerType.MARKETPLACE, status=Locker.LockerStatus.OCCUPIED).first()
                if locker:
                    locker.status = Locker.LockerStatus.AVAILABLE
                    locker.save()
                push_notification_task.delay(
                    user_id=transaction.seller.id,
                    title="Escrow Released",
                    body=f"Dana untuk '{transaction.product.name}' telah dilepaskan."
                )
                return Response(status=status.HTTP_200_OK)
        return Response(status=status.HTTP_204_NO_CONTENT)


def _ensure_seller(user, transaction: Transaction):
    if user.is_staff:
        return
    if transaction.seller != user:
        raise PermissionDenied("Only the seller can perform this action.")


def _ensure_participant(user, transaction: Transaction):
    if user.is_staff:
        return
    if transaction.seller != user and transaction.buyer != user:
        raise PermissionDenied("You are not part of this transaction.")


class TransactionListView(generics.ListAPIView):
    serializer_class = TransactionSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        qs = Transaction.objects.select_related('buyer', 'seller', 'product')
        if user.is_staff:
            return qs
        role = self.request.query_params.get('role')
        if role == 'seller':
            return qs.filter(seller=user)
        if role == 'buyer':
            return qs.filter(buyer=user)
        return qs.filter(models.Q(seller=user) | models.Q(buyer=user))


class TransactionDetailView(generics.RetrieveAPIView):
    serializer_class = TransactionSerializer
    permission_classes = [permissions.IsAuthenticated]
    queryset = Transaction.objects.select_related('buyer', 'seller', 'product')

    def get_object(self):
        transaction = super().get_object()
        _ensure_participant(self.request.user, transaction)
        return transaction


class TransactionApproveView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(request=None, responses=TransactionSerializer)
    def post(self, request, pk):
        transaction = get_object_or_404(Transaction, pk=pk)
        _ensure_seller(request.user, transaction)
        transaction.status = Transaction.TransactionStatus.ESCROW
        transaction.save(update_fields=['status', 'updated_at'])
        push_notification_task.delay(
            user_id=transaction.buyer.id,
            title="Order Approved",
            body=f"Pesanan '{transaction.product.name}' telah disetujui seller."
        )
        serializer = TransactionSerializer(transaction, context={'request': request})
        return Response(serializer.data)


class TransactionRejectView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(request=None, responses=TransactionSerializer)
    def post(self, request, pk):
        transaction = get_object_or_404(Transaction, pk=pk)
        _ensure_seller(request.user, transaction)
        transaction.status = Transaction.TransactionStatus.REJECTED
        transaction.save(update_fields=['status', 'updated_at'])
        push_notification_task.delay(
            user_id=transaction.buyer.id,
            title="Order Rejected",
            body=f"Pesanan '{transaction.product.name}' ditolak seller."
        )
        serializer = TransactionSerializer(transaction, context={'request': request})
        return Response(serializer.data)


class TransactionPaymentProofUploadView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    @extend_schema(request=PaymentProofUploadSerializer, responses=TransactionSerializer)
    def post(self, request, pk):
        transaction = get_object_or_404(Transaction, pk=pk)
        _ensure_participant(request.user, transaction)
        if request.user != transaction.buyer and not request.user.is_staff:
            raise PermissionDenied("Only the buyer can upload payment proof.")

        payment_file = request.FILES.get('payment_proof')
        if not payment_file:
            return Response({'error': 'payment_proof file is required.'}, status=status.HTTP_400_BAD_REQUEST)

        transaction.payment_proof = payment_file
        now = timezone.now()
        transaction.payment_proof_uploaded_at = now
        transaction.paid_at = now
        transaction.status = Transaction.TransactionStatus.PAID
        transaction.save(update_fields=['payment_proof', 'payment_proof_uploaded_at', 'paid_at', 'status', 'updated_at'])

        push_notification_task.delay(
            user_id=transaction.seller.id,
            title="Payment Proof Uploaded",
            body=f"Bukti pembayaran untuk '{transaction.product.name}' telah diunggah."
        )

        serializer = TransactionSerializer(transaction, context={'request': request})
        return Response(serializer.data)


class TransactionGenerateOtpView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(request=None, responses=TransactionSerializer)
    def post(self, request, pk):
        transaction = get_object_or_404(Transaction, pk=pk)
        _ensure_seller(request.user, transaction)
        otp = ''.join(random.choices(string.digits, k=6))
        transaction.otp_code = otp
        transaction.status = Transaction.TransactionStatus.AWAITING_PICKUP
        transaction.payment_expires_at = timezone.now() + timedelta(hours=6)
        transaction.save(update_fields=['otp_code', 'status', 'payment_expires_at', 'updated_at'])

        push_notification_task.delay(
            user_id=transaction.buyer.id,
            title="Pickup OTP",
            body=f"Kode OTP pengambilan untuk '{transaction.product.name}': {otp}"
        )

        serializer = TransactionSerializer(transaction, context={'request': request})
        return Response(serializer.data)


class TransactionShippingUpdateView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(request=TransactionShippingSerializer, responses=TransactionSerializer)
    def post(self, request, pk):
        transaction = get_object_or_404(Transaction, pk=pk)
        _ensure_participant(request.user, transaction)
        serializer = TransactionShippingSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        for field, value in serializer.validated_data.items():
            setattr(transaction, field, value)
        transaction.save(update_fields=['buyer_full_name', 'shipping_address', 'buyer_phone_number', 'updated_at'])

        push_notification_task.delay(
            user_id=transaction.seller.id,
            title="Shipping Info Updated",
            body=f"Informasi pengiriman untuk transaksi '{transaction.product.name}' telah diperbarui."
        )

        return Response(TransactionSerializer(transaction, context={'request': request}).data)
