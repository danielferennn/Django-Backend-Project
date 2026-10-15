import warnings

from decimal import Decimal
from drf_spectacular.utils import OpenApiTypes, extend_schema_field
from PIL import Image, UnidentifiedImageError
from rest_framework import serializers
from .models import Store, Product, Transaction
from apps.users.serializers import UserPublicSerializer

# Resource-exhaustion ceilings for payment proofs.  The byte cap alone cannot
# bound how many pixels a stored file expands to (a few hundred bytes can declare
# a 40000x40000 canvas), so a per-side dimension cap limits the decoded buffer a
# later ``Image.load()`` would allocate.
MAX_PAYMENT_PROOF_BYTES = 5 * 1024 * 1024
MAX_PAYMENT_PROOF_DIMENSION = 4096

# The one allowlist shared by upload validation and proof download: an accepted
# upload must be a format the authenticated proof viewer can serve, so a valid
# submission is never stored unreadable.  Values are the MIME types derived from
# the Pillow-validated file header.
PAYMENT_PROOF_FORMAT_MIME_TYPES = {
    'JPEG': 'image/jpeg',
    'PNG': 'image/png',
    'WEBP': 'image/webp',
}

class StoreSerializer(serializers.ModelSerializer):
    owner = UserPublicSerializer(read_only=True)

    class Meta:
        model = Store
        fields = ['id', 'owner', 'name', 'description', 'location']

class ProductSerializer(serializers.ModelSerializer):
    image_url = serializers.SerializerMethodField()
    price = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=Decimal('0'))
    store_info = StoreSerializer(source='store', read_only=True)
    seller = UserPublicSerializer(read_only=True)

    class Meta:
        model = Product
        fields = [
            'id',
            'store',
            'seller',
            'store_info',
            'name',
            'price',
            'stock',
            'description',
            'image',
            'image_url',
            'is_active',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['store', 'seller', 'created_at', 'updated_at', 'store_info']

    @extend_schema_field(serializers.CharField(allow_null=True))
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

class CreateTransactionSerializer(serializers.Serializer):
    product_id = serializers.IntegerField(min_value=1)
    quantity = serializers.IntegerField(min_value=1)
    buyer_full_name = serializers.CharField(max_length=255, required=False, allow_blank=True, default='')
    shipping_address = serializers.CharField(required=False, allow_blank=True, default='')
    buyer_phone_number = serializers.CharField(max_length=32, required=False, allow_blank=True, default='')


class PickupCodeSerializer(serializers.Serializer):
    otp = serializers.RegexField(r'^\d{6}$', trim_whitespace=False)

class TransactionSerializer(serializers.ModelSerializer):
    buyer = UserPublicSerializer(read_only=True)
    seller = UserPublicSerializer(read_only=True)
    product = ProductSerializer(read_only=True)
    buyer_name = serializers.SerializerMethodField()
    seller_name = serializers.SerializerMethodField()
    product_name = serializers.CharField(source='product.name', read_only=True)

    class Meta:
        model = Transaction
        fields = [
            'id', 'buyer', 'seller', 'buyer_name', 'seller_name', 'product', 'product_name',
            'quantity', 'total_price', 'status', 'payment_proof_uploaded_at', 'paid_at',
            'buyer_full_name', 'shipping_address', 'buyer_phone_number', 'locker',
            'deposited_at', 'created_at', 'updated_at',
        ]
        read_only_fields = fields

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


class TransactionShippingSerializer(serializers.Serializer):
    buyer_full_name = serializers.CharField(max_length=255)
    shipping_address = serializers.CharField()
    buyer_phone_number = serializers.CharField(max_length=32)


class PaymentProofUploadSerializer(serializers.Serializer):
    payment_proof = serializers.ImageField()

    def validate_payment_proof(self, image):
        if image.size > MAX_PAYMENT_PROOF_BYTES:
            raise serializers.ValidationError('Image must not exceed 5 MiB.')
        self._validate_image(image)
        return image

    def _validate_image(self, image):
        """Reject images whose format is unservable or whose canvas is too big.

        Only Pillow's header metadata is read (``Image.open`` parses the header
        lazily), so an oversize canvas is rejected without decoding or
        allocating its pixel buffer.  The upload's file position is preserved so
        the subsequent save reads the whole file.
        """
        position = image.tell()
        try:
            with warnings.catch_warnings():
                # Pillow warns *and* raises past 2x its bomb limit; silence the
                # warning so the explicit cap below decides the outcome.
                warnings.simplefilter('ignore', Image.DecompressionBombWarning)
                with Image.open(image) as probe:
                    width, height = probe.size
                    image_format = probe.format
        except (
            UnidentifiedImageError,
            Image.DecompressionBombError,
            OSError,
            ValueError,
        ):
            raise serializers.ValidationError('Upload must be a valid image.')
        finally:
            image.seek(position)
        if image_format not in PAYMENT_PROOF_FORMAT_MIME_TYPES:
            raise serializers.ValidationError('Upload must be a JPEG, PNG or WEBP image.')
        if width <= 0 or height <= 0:
            raise serializers.ValidationError('Upload must be a valid image.')
        if width > MAX_PAYMENT_PROOF_DIMENSION or height > MAX_PAYMENT_PROOF_DIMENSION:
            raise serializers.ValidationError(
                f'Image dimensions must not exceed '
                f'{MAX_PAYMENT_PROOF_DIMENSION}x{MAX_PAYMENT_PROOF_DIMENSION} pixels.'
            )
