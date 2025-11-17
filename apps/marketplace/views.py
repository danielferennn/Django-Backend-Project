import random
import string
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import models, transaction as db_transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import generics, permissions, status, viewsets
from rest_framework.exceptions import PermissionDenied
from rest_framework.parsers import FormParser, MultiPartParser, JSONParser
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
from apps.users.permissions import IsOwner, IsBuyer

User = get_user_model()

TRANSACTION_STATUS_ALIASES = {
    'PENDING_VERIFICATION': Transaction.TransactionStatus.NEED_VERIFICATION,
    'NEED_VERIFICATION': Transaction.TransactionStatus.NEED_VERIFICATION,
    'ESCROW': Transaction.TransactionStatus.ESCROW,
    'COMPLETED': Transaction.TransactionStatus.COMPLETED,
    'PENDING': Transaction.TransactionStatus.PENDING,
    'AWAITING_PICKUP': Transaction.TransactionStatus.AWAITING_PICKUP,
    'RELEASED': Transaction.TransactionStatus.RELEASED,
}


def _filter_transactions_by_status(queryset, request):
    status_param = request.query_params.get('status')
    if not status_param:
        return queryset

    normalized_statuses = []
    for raw_value in status_param.split(','):
        candidate = raw_value.strip().upper()
        if not candidate:
            continue
        mapped = TRANSACTION_STATUS_ALIASES.get(candidate, candidate)
        if mapped in Transaction.TransactionStatus.values:
            normalized_statuses.append(mapped)

    if not normalized_statuses:
        return queryset
    return queryset.filter(status__in=normalized_statuses)

class MyStoreView(generics.RetrieveUpdateAPIView):
    serializer_class = StoreSerializer
    permission_classes = [permissions.IsAuthenticated, IsOwner, IsStoreOwner]
    
    def get_object(self):
        store, created = Store.objects.get_or_create(owner=self.request.user, defaults={'name': f"{self.request.user.username}'s Store"})
        self.check_object_permissions(self.request, store)
        return store


class StoreListView(generics.ListAPIView):
    serializer_class = StoreSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        queryset = Store.objects.select_related('owner')
        search = self.request.query_params.get('search')
        if search:
            queryset = queryset.filter(
                models.Q(name__icontains=search) |
                models.Q(location__icontains=search) |
                models.Q(owner__username__icontains=search)
            )
        return queryset


class StoreDetailView(generics.RetrieveAPIView):
    serializer_class = StoreSerializer
    permission_classes = [permissions.IsAuthenticated]
    queryset = Store.objects.select_related('owner')

class ProductViewSet(viewsets.ModelViewSet):
    queryset = Product.objects.select_related('store', 'store__owner')
    serializer_class = ProductSerializer
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get_permissions(self):
        if self.request.method in permissions.SAFE_METHODS:
            return [permissions.IsAuthenticated()]
        return [permissions.IsAuthenticated(), IsOwner(), IsStoreOwner()]

    def get_queryset(self):
        queryset = Product.objects.select_related('store', 'store__owner')
        store_id = self.request.query_params.get('store_id')
        if store_id:
            queryset = queryset.filter(store_id=store_id)

        user = self.request.user
        role_param = (self.request.query_params.get('role') or '').lower()
        action = getattr(self, 'action', None)
        resolver_route = getattr(getattr(self.request, 'resolver_match', None), 'route', '') or ''
        is_my_products_route = 'my-products' in resolver_route
        is_seller_scope = user.is_authenticated and (role_param == 'seller' or is_my_products_route)

        if action in {'update', 'partial_update', 'destroy'}:
            return queryset

        if action == 'list':
            if is_seller_scope:
                return queryset.filter(seller=user)
            if hasattr(Product, 'is_active'):
                return queryset.filter(is_active=True)
            return queryset

        if is_my_products_route and user.is_authenticated:
            return queryset.filter(seller=user)

        if role_param == 'seller' and user.is_authenticated:
            return queryset.filter(seller=user)

        if hasattr(Product, 'is_active'):
            return queryset.filter(is_active=True)
        return queryset

    def perform_create(self, serializer):
        store, _ = Store.objects.get_or_create(
            owner=self.request.user,
            defaults={'name': f"{self.request.user.username}'s Store"}
        )
        save_kwargs = {'store': store, 'seller': self.request.user}
        if hasattr(Product, 'is_active'):
            save_kwargs['is_active'] = True
        serializer.save(**save_kwargs)

    def _ensure_owner(self, instance):
        if instance.seller != self.request.user:
            raise PermissionDenied("You do not have permission to modify this product.")

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

        self._ensure_owner(instance)
        serializer = self.get_serializer(instance, data=data, partial=partial)
        serializer.is_valid(raise_exception=True)
        if delete_previous:
            instance.image.delete(save=False)
        self.perform_update(serializer)

        if getattr(instance, '_prefetched_objects_cache', None):
            instance._prefetched_objects_cache = {}

        return Response(serializer.data)

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        self._ensure_owner(instance)
        try:
            return super().destroy(request, *args, **kwargs)
        except models.ProtectedError:
            return Response(
                {"detail": "Product has existing transactions and cannot be deleted."},
                status=status.HTTP_400_BAD_REQUEST,
            )


class MyProductViewSet(ProductViewSet):
    """
    Dedicated endpoints for sellers to manage their own products without the
    public queryset restrictions applied in ProductViewSet.
    """

    permission_classes = [permissions.IsAuthenticated, IsOwner, IsStoreOwner]

    def get_permissions(self):
        return [permissions.IsAuthenticated(), IsOwner(), IsStoreOwner()]

    def get_queryset(self):
        queryset = super().get_queryset()
        return queryset.filter(seller=self.request.user)


class TransactionViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = TransactionSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_permissions(self):
        role = (self.request.query_params.get('role') or '').lower()
        if role == 'seller':
            return [permissions.IsAuthenticated(), IsOwner()]
        return [permission() for permission in self.permission_classes]

    def get_queryset(self):
        user = self.request.user
        queryset = Transaction.objects.select_related(
            'buyer', 'seller', 'product', 'product__store'
        ).order_by('-created_at')

        role_param = (self.request.query_params.get('role') or '').lower()
        if role_param == 'seller':
            queryset = queryset.filter(seller=user)
            store_id = self.request.query_params.get('store_id')
            if store_id:
                queryset = queryset.filter(product__store_id=store_id)
        elif role_param == 'buyer':
            queryset = queryset.filter(buyer=user)
        else:
            if getattr(user, 'role', None) == User.Role.BUYER:
                queryset = queryset.filter(buyer=user)
            elif getattr(user, 'role', None) == User.Role.OWNER:
                queryset = queryset.filter(seller=user)
            else:
                queryset = queryset.filter(models.Q(seller=user) | models.Q(buyer=user))

        return _filter_transactions_by_status(queryset, self.request)


class CreateTransactionView(generics.CreateAPIView):
    serializer_class = TransactionSerializer
    permission_classes = [permissions.IsAuthenticated, IsBuyer]

    def create(self, request, *args, **kwargs):
        product = get_object_or_404(Product, id=request.data.get('product_id'))
        quantity = int(request.data.get('quantity', 1))
        
        if product.stock < quantity:
            return Response({"error": "Not enough stock."}, status=status.HTTP_400_BAD_REQUEST)

        buyer_full_name = request.data.get('buyer_full_name', '')
        shipping_address = request.data.get('shipping_address', '')
        buyer_phone_number = request.data.get('buyer_phone_number', '')

        with db_transaction.atomic():
            seller = product.seller or product.store.owner
            if product.seller_id is None:
                product.seller = seller
                product.save(update_fields=['seller'])
            product.stock -= quantity
            product.save()
            transaction = Transaction.objects.create(
                buyer=request.user,
                seller=seller,
                product=product,
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
        if request.data.get('payment_status') != 'success':
            return Response(status=status.HTTP_400_BAD_REQUEST)

        if transaction.status == Transaction.TransactionStatus.NEED_VERIFICATION:
            return Response(status=status.HTTP_200_OK)

        if transaction.status != Transaction.TransactionStatus.PENDING:
            return Response(status=status.HTTP_400_BAD_REQUEST)

        transaction.status = Transaction.TransactionStatus.NEED_VERIFICATION
        transaction.save(update_fields=['status', 'updated_at'])
        push_notification_task(
            user_ids=[transaction.seller_id],
            title="Menunggu Verifikasi Pembayaran",
            body=f"Pembayaran untuk '{transaction.product.name}' menunggu verifikasi Anda."
        )
        return Response(status=status.HTTP_200_OK)

@extend_schema(exclude=True)
class SellerDepositItemView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsOwner]
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
            push_notification_task(
                user_ids=[transaction.buyer_id],
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
                push_notification_task(
                    user_ids=[transaction.seller_id],
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

    def get_permissions(self):
        role = (self.request.query_params.get('role') or '').lower()
        if role == 'seller':
            return [permissions.IsAuthenticated(), IsOwner()]
        return [permission() for permission in self.permission_classes]

    def get_queryset(self):
        user = self.request.user
        qs = Transaction.objects.select_related('buyer', 'seller', 'product').order_by('-created_at')
        if user.is_staff:
            return _filter_transactions_by_status(qs, self.request)
        role = self.request.query_params.get('role')
        if role == 'seller':
            if user.role != User.Role.OWNER:
                raise PermissionDenied("Only owners can access seller transactions.")
            qs = qs.filter(seller=user)
            store_id = self.request.query_params.get('store_id')
            if store_id:
                qs = qs.filter(product__store_id=store_id)
        if role == 'buyer':
            qs = qs.filter(buyer=user)
        if role not in {'seller', 'buyer'}:
            qs = qs.filter(models.Q(seller=user) | models.Q(buyer=user))
        return _filter_transactions_by_status(qs, self.request)


class TransactionDetailView(generics.RetrieveAPIView):
    serializer_class = TransactionSerializer
    permission_classes = [permissions.IsAuthenticated]
    queryset = Transaction.objects.select_related('buyer', 'seller', 'product')

    def get_object(self):
        transaction = super().get_object()
        _ensure_participant(self.request.user, transaction)
        return transaction


class TransactionApproveView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsOwner]

    @extend_schema(request=None, responses=TransactionSerializer)
    def post(self, request, pk):
        transaction = get_object_or_404(Transaction, pk=pk)
        _ensure_seller(request.user, transaction)
        if transaction.status != Transaction.TransactionStatus.NEED_VERIFICATION:
            return Response(
                {'error': 'Transaction is not waiting for verification.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        transaction.status = Transaction.TransactionStatus.ESCROW
        transaction.paid_at = timezone.now()
        transaction.save(update_fields=['status', 'paid_at', 'updated_at'])
        push_notification_task(
            user_ids=[transaction.buyer_id],
            title="Order Approved",
            body=f"Pesanan '{transaction.product.name}' telah disetujui seller."
        )
        serializer = TransactionSerializer(transaction, context={'request': request})
        return Response(serializer.data)


class TransactionRejectView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsOwner]

    @extend_schema(request=None, responses=TransactionSerializer)
    def post(self, request, pk):
        transaction = get_object_or_404(Transaction, pk=pk)
        _ensure_seller(request.user, transaction)
        if transaction.status != Transaction.TransactionStatus.NEED_VERIFICATION:
            return Response(
                {'error': 'Transaction is not waiting for verification.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        transaction.status = Transaction.TransactionStatus.COMPLETED
        transaction.save(update_fields=['status', 'updated_at'])
        push_notification_task(
            user_ids=[transaction.buyer_id],
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
        transaction.paid_at = None
        transaction.status = Transaction.TransactionStatus.NEED_VERIFICATION
        transaction.save(
            update_fields=[
                'payment_proof',
                'payment_proof_uploaded_at',
                'paid_at',
                'status',
                'updated_at',
            ]
        )

        push_notification_task(
            user_ids=[transaction.seller_id],
            title="Payment Proof Uploaded",
            body=f"Bukti pembayaran untuk '{transaction.product.name}' telah diunggah."
        )

        serializer = TransactionSerializer(transaction, context={'request': request})
        return Response(serializer.data)


class TransactionGenerateOtpView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsOwner]

    @extend_schema(request=None, responses=TransactionSerializer)
    def post(self, request, pk):
        transaction = get_object_or_404(Transaction, pk=pk)
        _ensure_seller(request.user, transaction)
        otp = ''.join(random.choices(string.digits, k=6))
        transaction.otp = otp
        transaction.status = Transaction.TransactionStatus.AWAITING_PICKUP
        transaction.payment_expires_at = timezone.now() + timedelta(hours=6)
        transaction.save(update_fields=['otp', 'status', 'payment_expires_at', 'updated_at'])

        push_notification_task(
            user_ids=[transaction.buyer_id],
            title="Pickup OTP",
            body=f"Kode OTP pengambilan untuk '{transaction.product.name}': {otp}"
        )

        serializer = TransactionSerializer(transaction, context={'request': request})
        return Response(serializer.data)


class TransactionShippingUpdateView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsOwner]

    @extend_schema(request=TransactionShippingSerializer, responses=TransactionSerializer)
    def post(self, request, pk):
        transaction = get_object_or_404(Transaction, pk=pk)
        _ensure_seller(request.user, transaction)
        serializer = TransactionShippingSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        for field, value in serializer.validated_data.items():
            setattr(transaction, field, value)
        transaction.save(update_fields=['buyer_full_name', 'shipping_address', 'buyer_phone_number', 'updated_at'])

        push_notification_task(
            user_ids=[transaction.buyer_id],
            title="Shipping Info Updated",
            body=f"Informasi pengiriman untuk transaksi '{transaction.product.name}' telah diperbarui."
        )

        return Response(TransactionSerializer(transaction, context={'request': request}).data)


class TransactionBuyerShippingUpdateView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(request=TransactionShippingSerializer, responses=TransactionSerializer)
    def post(self, request, pk):
        transaction = get_object_or_404(Transaction, pk=pk)
        if request.user != transaction.buyer and not request.user.is_staff:
            raise PermissionDenied("Only the buyer can update this information.")
        serializer = TransactionShippingSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        for field, value in serializer.validated_data.items():
            setattr(transaction, field, value)
        transaction.save(update_fields=['buyer_full_name', 'shipping_address', 'buyer_phone_number', 'updated_at'])

        push_notification_task(
            user_ids=[transaction.seller_id],
            title="Buyer Shipping Details Updated",
            body=f"Pembeli memperbarui informasi pengiriman untuk '{transaction.product.name}'."
        )

        return Response(TransactionSerializer(transaction, context={'request': request}).data)
