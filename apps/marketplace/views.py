from rest_framework import viewsets, generics, permissions, status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from django.shortcuts import get_object_or_404
from django.db import transaction as db_transaction
from django.utils import timezone
from django.conf import settings
import random

from .models import Store, Product, Transaction
from .serializers import StoreSerializer, ProductSerializer, TransactionSerializer
from .permissions import IsStoreOwner
from .services import PaymentGatewayService
from apps.lockers.models import Locker
from apps.lockers.services import BlynkAPIService
from apps.lockers.tasks import send_notification_task
from apps.users.models import User
import logging

logger = logging.getLogger(__name__)


def _safe_notify(user_id, message):
    try:
        send_notification_task.delay(user_id=user_id, message=message)
    except Exception as exc:  # pragma: no cover - fallback for dev without broker
        logger.warning("Notification task fallback due to %s. Running synchronously.", exc)
        send_notification_task.run(user_id=user_id, message=message)

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
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get_permissions(self):
        if self.request.method in permissions.SAFE_METHODS:
            return [permissions.IsAuthenticated()]
        return [permissions.IsAuthenticated(), IsStoreOwner()]

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
        
        buyer_full_name = request.data.get('buyer_full_name', '').strip()
        shipping_address = request.data.get('shipping_address', '').strip()
        buyer_phone_number = request.data.get('buyer_phone_number', '').strip()

        with db_transaction.atomic():
            product.stock -= quantity
            product.save()
            transaction = Transaction.objects.create(
                buyer=request.user, seller=product.store.owner, product=product,
                quantity=quantity, total_price=product.price * quantity,
                buyer_full_name=buyer_full_name,
                shipping_address=shipping_address,
                buyer_phone_number=buyer_phone_number,
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


class UploadPaymentProofView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request, *args, **kwargs):
        transaction = get_object_or_404(Transaction, pk=kwargs.get('pk'), buyer=request.user)

        if transaction.status in {
            Transaction.TransactionStatus.ESCROW,
            Transaction.TransactionStatus.PAID,
            Transaction.TransactionStatus.COMPLETED,
            Transaction.TransactionStatus.RELEASED,
        } and transaction.payment_proof:
            return Response({"detail": "Payment proof already processed."}, status=status.HTTP_409_CONFLICT)

        file_obj = request.FILES.get('payment_proof')
        if not file_obj:
            return Response({"detail": "payment_proof is required."}, status=status.HTTP_400_BAD_REQUEST)

        if file_obj.size > 5 * 1024 * 1024:
            return Response({"detail": "File exceeds 5MB limit."}, status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)

        content_type = (file_obj.content_type or '').lower()
        allowed_types = {'image/jpeg', 'image/jpg', 'image/png'}
        filename = (file_obj.name or '').lower()
        allowed_extensions = ('.jpg', '.jpeg', '.png')
        if content_type not in allowed_types and not filename.endswith(allowed_extensions):
            return Response(
                {"detail": "Invalid file type. Allowed types: JPG, JPEG, PNG."},
                status=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            )

        if transaction.payment_proof:
            transaction.payment_proof.delete(save=False)

        transaction.payment_proof = file_obj
        transaction.payment_proof_uploaded_at = timezone.now()
        transaction.save(update_fields=['payment_proof', 'payment_proof_uploaded_at'])

        serializer = TransactionSerializer(transaction, context={'request': request})
        return Response(serializer.data, status=status.HTTP_200_OK)


class UpdateTransactionShippingView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, *args, **kwargs):
        transaction = get_object_or_404(Transaction, pk=kwargs.get('pk'), buyer=request.user)

        full_name = (request.data.get('buyer_full_name') or '').strip()
        address = (request.data.get('shipping_address') or '').strip()
        phone = (request.data.get('buyer_phone_number') or '').strip()

        if not full_name or not address or not phone:
            return Response(
                {"detail": "buyer_full_name, shipping_address, and buyer_phone_number are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        transaction.buyer_full_name = full_name
        transaction.shipping_address = address
        transaction.buyer_phone_number = phone
        transaction.save(update_fields=['buyer_full_name', 'shipping_address', 'buyer_phone_number'])

        serializer = TransactionSerializer(transaction, context={'request': request})
        return Response(serializer.data, status=status.HTTP_200_OK)


class ApproveTransactionView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, *args, **kwargs):
        transaction = get_object_or_404(Transaction, pk=kwargs.get('pk'), seller=request.user)
        if transaction.status not in {
            Transaction.TransactionStatus.PENDING,
            Transaction.TransactionStatus.REJECTED,
        }:
            return Response({"detail": "Transaction is not awaiting manual approval."}, status=status.HTTP_400_BAD_REQUEST)
        if not transaction.payment_proof:
            return Response({"detail": "Payment proof required before approval."}, status=status.HTTP_400_BAD_REQUEST)

        transaction.status = Transaction.TransactionStatus.ESCROW
        transaction.paid_at = timezone.now()
        transaction.save(update_fields=['status', 'paid_at'])

        _safe_notify(transaction.buyer.id, f"Pembayaran untuk '{transaction.product.name}' telah disetujui.")

        serializer = TransactionSerializer(transaction, context={'request': request})
        return Response(serializer.data, status=status.HTTP_200_OK)


class RejectTransactionView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, *args, **kwargs):
        transaction = get_object_or_404(Transaction, pk=kwargs.get('pk'), seller=request.user)
        transaction.status = Transaction.TransactionStatus.REJECTED
        transaction.save(update_fields=['status'])

        _safe_notify(transaction.buyer.id, f"Pembayaran untuk '{transaction.product.name}' ditolak. Silakan hubungi penjual.")

        serializer = TransactionSerializer(transaction, context={'request': request})
        return Response(serializer.data, status=status.HTTP_200_OK)


class GenerateOtpView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, *args, **kwargs):
        transaction = get_object_or_404(Transaction, pk=kwargs.get('pk'), seller=request.user)
        if transaction.status not in {
            Transaction.TransactionStatus.ESCROW,
            Transaction.TransactionStatus.PAID,
        }:
            return Response({"detail": "OTP can only be generated after payment approval."}, status=status.HTTP_400_BAD_REQUEST)
        if not transaction.buyer_full_name or not transaction.shipping_address or not transaction.buyer_phone_number:
            return Response({"detail": "Buyer details are incomplete."}, status=status.HTTP_400_BAD_REQUEST)

        otp = f"{random.randint(0, 999999):06d}"
        transaction.otp = otp
        transaction.status = Transaction.TransactionStatus.AWAITING_PICKUP
        transaction.save(update_fields=['otp', 'status'])

        _safe_notify(transaction.buyer.id, f"OTP untuk '{transaction.product.name}' adalah {otp}.")

        serializer = TransactionSerializer(transaction, context={'request': request})
        return Response(serializer.data, status=status.HTTP_200_OK)


class PaymentWebhookView(APIView):
    permission_classes = [permissions.AllowAny]
    def post(self, request, *args, **kwargs):
        transaction = get_object_or_404(Transaction, id=request.data.get('transaction_id'))
        if request.data.get('payment_status') == 'success' and transaction.status == Transaction.TransactionStatus.PENDING:
            transaction.status = Transaction.TransactionStatus.ESCROW
            transaction.save()
            _safe_notify(transaction.seller.id, f"Pembayaran untuk '{transaction.product.name}' berhasil.")
            return Response(status=status.HTTP_200_OK)
        return Response(status=status.HTTP_400_BAD_REQUEST)

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

class ConfirmMarketplaceDepositWebhookView(APIView):
    permission_classes = [permissions.AllowAny]
    def post(self, request, *args, **kwargs):
        transaction = Transaction.objects.filter(status=Transaction.TransactionStatus.ESCROW).first()
        if transaction:
            otp = transaction.otp or f"{random.randint(0, 999999):06d}"
            transaction.status = Transaction.TransactionStatus.AWAITING_PICKUP
            transaction.otp = otp
            transaction.save(update_fields=['status', 'otp'])
            _safe_notify(transaction.buyer.id, f"Barang '{transaction.product.name}' siap diambil. Kode OTP: {otp}")
            return Response(status=status.HTTP_200_OK)
        return Response(status=status.HTTP_204_NO_CONTENT)

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
                _safe_notify(transaction.seller.id, f"Dana untuk '{transaction.product.name}' telah dilepaskan.")
                return Response(status=status.HTTP_200_OK)
        return Response(status=status.HTTP_204_NO_CONTENT)


class QrisConfirmWebhookView(APIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request, *args, **kwargs):
        expected_secret = getattr(settings, 'QRIS_WEBHOOK_SECRET', None)
        provided_secret = request.headers.get('X-Qris-Signature')
        if expected_secret and provided_secret != expected_secret:
            return Response(status=status.HTTP_403_FORBIDDEN)

        reference = request.data.get('payment_reference') or request.data.get('reference')
        if not reference:
            return Response({"detail": "Missing payment reference."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            transaction = Transaction.objects.get(payment_gateway_id=reference)
        except Transaction.DoesNotExist:
            return Response({"detail": "Transaction not found."}, status=status.HTTP_404_NOT_FOUND)

        if transaction.status != Transaction.TransactionStatus.ESCROW:
            transaction.status = Transaction.TransactionStatus.ESCROW
            transaction.paid_at = timezone.now()
            transaction.save(update_fields=['status', 'paid_at'])
            _safe_notify(transaction.seller.id, f"Pembayaran untuk '{transaction.product.name}' telah dikonfirmasi.")

        return Response({"detail": "OK"}, status=status.HTTP_200_OK)
