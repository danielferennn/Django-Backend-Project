from rest_framework import serializers

from apps.users.serializers import UserPublicSerializer

from .models import Delivery, Locker, LockerLog, LockerRequest, Package


class LockerSerializer(serializers.ModelSerializer):
    class Meta:
        model = Locker
        fields = ['id', 'number', 'type', 'status']


class LockerLogSerializer(serializers.ModelSerializer):
    locker = LockerSerializer(read_only=True)
    user = UserPublicSerializer(read_only=True)

    class Meta:
        model = LockerLog
        fields = [
            'id', 'locker', 'user', 'command', 'transaction', 'delivery',
            'action', 'timestamp', 'is_successful', 'details',
        ]
        read_only_fields = fields


class LockerRequestSerializer(serializers.ModelSerializer):
    locker_number = serializers.CharField(source='locker.number', read_only=True, allow_null=True)

    class Meta:
        model = LockerRequest
        fields = [
            'command_id', 'locker_number', 'purpose', 'status', 'expires_at',
            'published_at', 'completed_at', 'error_code', 'created_at',
        ]
        read_only_fields = fields


class LockerCommandReconciliationSerializer(serializers.Serializer):
    decision = serializers.ChoiceField(
        choices=[
            ('retry', 'Retry'),
            ('confirm_failed', 'Confirm failed'),
            ('confirm_completed', 'Confirm completed'),
        ]
    )
    confirmation = serializers.CharField(required=False, allow_blank=True, max_length=64, default='')


class DeliverySerializer(serializers.ModelSerializer):
    class Meta:
        model = Delivery
        fields = [
            'id', 'receipt_number', 'courier', 'locker', 'owner', 'storage_locker',
            'status', 'retrieved_at', 'created_at', 'updated_at',
        ]
        read_only_fields = fields


class OtpValidationSerializer(serializers.Serializer):
    otp = serializers.RegexField(r'^\d{6}$', trim_whitespace=False)


class PackageSerializer(serializers.ModelSerializer):
    class Meta:
        model = Package
        fields = [
            'id',
            'name',
            'tracking_number',
            'courier',
            'order_date',
            'status',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['created_at', 'updated_at']
