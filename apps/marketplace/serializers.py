from drf_spectacular.utils import OpenApiTypes, extend_schema_field
from rest_framework import serializers
from .models import Store, Product, Transaction
from apps.users.serializers import UserDetailSerializer

class StoreSerializer(serializers.ModelSerializer):
    owner = UserDetailSerializer(read_only=True)

    class Meta:
        model = Store
        fields = ['id', 'owner', 'name', 'description']

class ProductSerializer(serializers.ModelSerializer):
    image_url = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = ['id', 'store', 'name', 'price', 'stock', 'description', 'image', 'image_url', 'created_at', 'updated_at']
        read_only_fields = ['store', 'created_at', 'updated_at']

    def get_image_url(self, obj):
        if obj.image and hasattr(obj.image, 'url'):
            request = self.context.get('request')
            return request.build_absolute_uri(obj.image.url) if request else obj.image.url
        return None

    def to_representation(self, instance):
        data = super().to_representation(instance)
        request = self.context.get('request')
        if instance.image:
            url = instance.image.url
            data['image'] = request.build_absolute_uri(url) if request else url
        else:
            data['image'] = None
        return data

class TransactionSerializer(serializers.ModelSerializer):
    buyer = UserDetailSerializer(read_only=True)
    seller = UserDetailSerializer(read_only=True)
    product = ProductSerializer(read_only=True)
    buyer_name = serializers.SerializerMethodField()
    seller_name = serializers.SerializerMethodField()
    product_name = serializers.CharField(source='product.name', read_only=True)
    payment_proof_url = serializers.SerializerMethodField()

    class Meta:
        model = Transaction
        fields = [
            'id', 'buyer', 'seller', 'buyer_name', 'seller_name', 'product', 'product_name',
            'quantity', 'total_price', 'status', 'payment_gateway_id', 'payment_proof',
            'payment_proof_url', 'payment_proof_uploaded_at', 'paid_at', 'payment_expires_at',
            'qris_payload', 'qris_image', 'buyer_full_name',
            'shipping_address', 'buyer_phone_number', 'otp_code', 'created_at', 'updated_at',
        ]
        read_only_fields = [
            'buyer', 'seller', 'status', 'payment_gateway_id', 'payment_proof_uploaded_at',
            'payment_proof', 'created_at', 'updated_at', 'otp_code', 'paid_at', 'payment_expires_at',
            'qris_payload', 'qris_image'
        ]

    @extend_schema_field(OpenApiTypes.STR)
    def get_buyer_name(self, obj):
        user = getattr(obj, 'buyer', None)
        if not user:
            return ''
        return user.get_full_name() or user.username

    @extend_schema_field(OpenApiTypes.STR)
    def get_seller_name(self, obj):
        user = getattr(obj, 'seller', None)
        if not user:
            return ''
        return user.get_full_name() or user.username

    @extend_schema_field(OpenApiTypes.STR)
    def get_payment_proof_url(self, obj):
        if obj.payment_proof and hasattr(obj.payment_proof, 'url'):
            request = self.context.get('request')
            return request.build_absolute_uri(obj.payment_proof.url) if request else obj.payment_proof.url
        return None


class TransactionShippingSerializer(serializers.Serializer):
    buyer_full_name = serializers.CharField(max_length=255)
    shipping_address = serializers.CharField()
    buyer_phone_number = serializers.CharField(max_length=32)


class PaymentProofUploadSerializer(serializers.Serializer):
    payment_proof = serializers.FileField()
