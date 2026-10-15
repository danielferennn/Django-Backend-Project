import warnings

from django.contrib.auth import get_user_model
from django.db import models
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from django.utils.http import content_disposition_header
from PIL import Image, UnidentifiedImageError
from rest_framework import generics, permissions, status, viewsets
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.parsers import FormParser, MultiPartParser, JSONParser
from rest_framework.response import Response
from rest_framework.views import APIView

from drf_spectacular.utils import OpenApiTypes, extend_schema

from .models import Store, Product, Transaction
from .serializers import (
    CreateTransactionSerializer,
    PAYMENT_PROOF_FORMAT_MIME_TYPES,
    PaymentProofUploadSerializer,
    PickupCodeSerializer,
    ProductSerializer,
    StoreSerializer,
    TransactionSerializer,
    TransactionShippingSerializer,
)
from .permissions import IsStoreOwner
from .services import (
    InvalidTransactionState,
    PaymentInstructionsUnavailable,
    ProductUnavailable,
    StockUnavailable,
    approve_manual_payment,
    create_manual_purchase,
    reject_manual_payment,
    submit_payment_proof,
)
from apps.lockers.models import LockerRequest
from apps.lockers.serializers import LockerRequestSerializer
from apps.lockers.services import (
    InvalidLockerWorkflow,
    InvalidPickupCode,
    LockerUnavailable,
    PickupCodeExpired,
    PickupCodeLocked,
    reissue_marketplace_pickup_code,
    request_marketplace_deposit,
    request_marketplace_retrieval,
)
from apps.notifications.tasks import push_notification_task
from apps.users.permissions import IsSeller, IsBuyer

User = get_user_model()

def _filter_transactions_by_status(queryset, request):
    status_param = request.query_params.get('status')
    if not status_param:
        return queryset

    statuses = [value.strip().upper() for value in status_param.split(',') if value.strip()]
    valid_statuses = set(Transaction.TransactionStatus.values)
    invalid_statuses = set(statuses) - valid_statuses
    if invalid_statuses:
        raise ValidationError({'status': f'Unknown transaction status: {", ".join(sorted(invalid_statuses))}.'})
    return queryset.filter(status__in=statuses)

class MyStoreView(generics.RetrieveUpdateAPIView):
    serializer_class = StoreSerializer
    permission_classes = [permissions.IsAuthenticated, IsSeller, IsStoreOwner]
    
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
        return [permissions.IsAuthenticated(), IsSeller(), IsStoreOwner()]

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

        # Authorization must be enforced before any stored file is touched so a
        # request that is not allowed to modify this product can never delete or
        # replace its image.  ``get_object`` already applies ``IsStoreOwner``,
        # but ``_ensure_owner`` is an additional gate (it checks
        # ``instance.seller``) and must run first as well.
        self._ensure_owner(instance)

        data = request.data.copy()
        previous_image = None
        if 'image' in data and not data.get('image'):
            # Explicitly clearing the image: remember the file to remove but do
            # not delete it until the update has been validated and persisted.
            data['image'] = None
            if instance.image:
                previous_image = (instance.image.storage, instance.image.name)
        elif request.FILES.get('image') and instance.image:
            # Replacing the image: the previous file is only superseded once the
            # new one has been validated and saved.
            previous_image = (instance.image.storage, instance.image.name)

        serializer = self.get_serializer(instance, data=data, partial=partial)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)

        # Deletion of the superseded file is deferred until the update has been
        # validated and persisted, so a rejected update leaves the original
        # image intact. The file is removed through its storage backend rather
        # than ``FieldFile.delete()`` so the newly saved image on the in-memory
        # instance is not cleared.
        if previous_image:
            storage, name = previous_image
            if name:
                storage.delete(name)

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

    permission_classes = [permissions.IsAuthenticated, IsSeller, IsStoreOwner]

    def get_permissions(self):
        return [permissions.IsAuthenticated(), IsSeller(), IsStoreOwner()]

    def get_queryset(self):
        queryset = super().get_queryset()
        return queryset.filter(seller=self.request.user)


class CreateTransactionView(generics.CreateAPIView):
    serializer_class = CreateTransactionSerializer
    permission_classes = [permissions.IsAuthenticated, IsBuyer]

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            purchase, instructions = create_manual_purchase(
                buyer=request.user,
                **serializer.validated_data,
            )
        except PaymentInstructionsUnavailable:
            return Response(
                {'error': 'Manual payment instructions are not configured.'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        except ProductUnavailable:
            return Response({'error': 'Product is unavailable.'}, status=status.HTTP_404_NOT_FOUND)
        except StockUnavailable:
            return Response({'error': 'Not enough stock.'}, status=status.HTTP_400_BAD_REQUEST)

        response_data = TransactionSerializer(
            purchase,
            context={'request': request},
        ).data
        response_data['manual_payment_instructions'] = instructions
        return Response(response_data, status=status.HTTP_201_CREATED)


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
            return [permissions.IsAuthenticated(), IsSeller()]
        return [permission() for permission in self.permission_classes]

    def get_queryset(self):
        user = self.request.user
        qs = Transaction.objects.select_related('buyer', 'seller', 'product').order_by('-created_at')
        if user.is_staff:
            return _filter_transactions_by_status(qs, self.request)
        role = self.request.query_params.get('role')
        if role == 'seller':
            if user.role not in {User.Role.OWNER, User.Role.SELLER}:
                raise PermissionDenied("Only sellers can access seller transactions.")
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
    permission_classes = [permissions.IsAuthenticated, IsSeller]

    @extend_schema(request=None, responses=TransactionSerializer)
    def post(self, request, pk):
        transaction = get_object_or_404(Transaction, pk=pk)
        _ensure_seller(request.user, transaction)
        try:
            transaction = approve_manual_payment(transaction_id=transaction.pk)
        except InvalidTransactionState:
            return Response(
                {'error': 'Transaction is not waiting for payment verification.'},
                status=status.HTTP_409_CONFLICT,
            )
        return Response(TransactionSerializer(transaction, context={'request': request}).data)

class TransactionRejectView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsSeller]

    @extend_schema(request=None, responses=TransactionSerializer)
    def post(self, request, pk):
        transaction = get_object_or_404(Transaction, pk=pk)
        _ensure_seller(request.user, transaction)
        try:
            transaction = reject_manual_payment(transaction_id=transaction.pk)
        except InvalidTransactionState:
            return Response(
                {'error': 'Transaction is not waiting for payment verification.'},
                status=status.HTTP_409_CONFLICT,
            )
        return Response(TransactionSerializer(transaction, context={'request': request}).data)


# A fixed, non-PII download name so a stored storage name is never leaked.
_PAYMENT_PROOF_FILENAME = 'payment-proof'

def _allowed_proof_mime(stored_file):
    """Return the allowed image MIME for ``stored_file`` from its header.

    Only Pillow's header metadata is parsed (no pixel decode).  The stream is
    rewound afterwards so the caller streams the whole file, and it is left
    open on success (the caller closes it) but closed if the header is rejected
    or the format is not allowed.
    """
    stored_file.seek(0)
    try:
        with warnings.catch_warnings():
            # Pillow warns *and* raises past 2x its bomb limit; silence the
            # warning so the explicit allow-list below decides the outcome.
            warnings.simplefilter('ignore', Image.DecompressionBombWarning)
            with Image.open(stored_file) as probe:
                content_type = PAYMENT_PROOF_FORMAT_MIME_TYPES.get(probe.format)
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError):
        stored_file.close()
        raise Http404
    if content_type is None:
        stored_file.close()
        raise Http404
    stored_file.seek(0)
    return content_type

class TransactionPaymentProofUploadView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def get_permissions(self):
        # POST upload stays authenticated-then-service-checked (the buyer is
        # validated in ``submit_payment_proof``); reading a proof is restricted
        # to an active seller/staff role *and* the owning seller below, so a
        # downgraded or inactive former seller cannot still read it.
        if self.request.method == 'GET':
            return [permissions.IsAuthenticated(), IsSeller()]
        return super().get_permissions()

    @extend_schema(request=PaymentProofUploadSerializer, responses=TransactionSerializer)
    def post(self, request, pk):
        get_object_or_404(Transaction, pk=pk)
        serializer = PaymentProofUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            purchase = submit_payment_proof(
                transaction_id=pk,
                buyer=request.user,
                payment_proof=serializer.validated_data['payment_proof'],
            )
        except PermissionError as exc:
            raise PermissionDenied(str(exc)) from exc
        except InvalidTransactionState:
            return Response(
                {'error': 'Payment proof can only be submitted for a pending transaction.'},
                status=status.HTTP_409_CONFLICT,
            )
        return Response(TransactionSerializer(purchase, context={'request': request}).data)

    @extend_schema(responses={(200, 'image/*'): OpenApiTypes.BINARY})
    def get(self, request, pk):
        """Stream the private proof to its seller (or staff) only.

        ``TransactionSerializer`` deliberately omits the proof, so verification
        needs a dedicated, authenticated read that never exposes a public
        storage URL.  Buyers and unrelated sellers are denied; a transaction
        without a proof, or whose stored file has gone missing, is a 404.
        """
        transaction = get_object_or_404(Transaction, pk=pk)
        _ensure_seller(request.user, transaction)
        if not transaction.payment_proof:
            raise Http404
        try:
            stored_file = transaction.payment_proof.storage.open(transaction.payment_proof.name)
        except FileNotFoundError:
            raise Http404
        content_type = _allowed_proof_mime(stored_file)
        response = FileResponse(stored_file, content_type=content_type)
        response['Content-Disposition'] = content_disposition_header(False, _PAYMENT_PROOF_FILENAME)
        response['Cache-Control'] = 'private, no-store'
        response['X-Content-Type-Options'] = 'nosniff'
        return response

class SellerDepositItemView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsSeller]

    @extend_schema(request=None, responses=LockerRequestSerializer)
    def post(self, request, pk):
        purchase = get_object_or_404(Transaction, pk=pk)
        _ensure_seller(request.user, purchase)
        try:
            command = request_marketplace_deposit(transaction_id=purchase.pk, actor=request.user)
        except PermissionError as exc:
            raise PermissionDenied(str(exc)) from exc
        except LockerUnavailable:
            return Response({'error': 'No available marketplace locker.'}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        except InvalidLockerWorkflow:
            return Response({'error': 'Transaction is not ready for locker deposit.'}, status=status.HTTP_409_CONFLICT)
        return Response(LockerRequestSerializer(command).data, status=status.HTTP_202_ACCEPTED)


class BuyerRetrieveItemView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(request=PickupCodeSerializer, responses=LockerRequestSerializer)
    def post(self, request, pk):
        purchase = get_object_or_404(Transaction, pk=pk)
        serializer = PickupCodeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            command = request_marketplace_retrieval(
                transaction_id=purchase.pk,
                buyer=request.user,
                otp=serializer.validated_data['otp'],
            )
        except PermissionError as exc:
            raise PermissionDenied(str(exc)) from exc
        except PickupCodeLocked:
            return Response({'error': 'Pickup code is locked.'}, status=status.HTTP_429_TOO_MANY_REQUESTS)
        except PickupCodeExpired:
            return Response({'error': 'Pickup code expired.'}, status=status.HTTP_410_GONE)
        except InvalidPickupCode:
            return Response({'error': 'Invalid or already used pickup code.'}, status=status.HTTP_400_BAD_REQUEST)
        except LockerUnavailable:
            return Response({'error': 'The pickup locker is unavailable.'}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        except InvalidLockerWorkflow:
            return Response({'error': 'Transaction is not ready for pickup.'}, status=status.HTTP_409_CONFLICT)
        return Response(LockerRequestSerializer(command).data, status=status.HTTP_202_ACCEPTED)


class PickupCodeReissueView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsSeller]

    @extend_schema(request=None, responses=TransactionSerializer)
    def post(self, request, pk):
        purchase = get_object_or_404(Transaction, pk=pk)
        _ensure_seller(request.user, purchase)
        try:
            purchase = reissue_marketplace_pickup_code(
                transaction_id=purchase.pk,
                actor=request.user,
            )
        except PermissionError as exc:
            raise PermissionDenied(str(exc)) from exc
        except InvalidLockerWorkflow:
            return Response(
                {'error': 'Pickup code can only be reissued after expiry or lockout.'},
                status=status.HTTP_409_CONFLICT,
            )
        return Response(TransactionSerializer(purchase, context={'request': request}).data)


class TransactionShippingUpdateView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsSeller]

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
