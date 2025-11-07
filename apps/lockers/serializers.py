from rest_framework import serializers
from .models import Locker, LockerLog, Delivery
from apps.users.serializers import UserDetailSerializer

class LockerSerializer(serializers.ModelSerializer):
    class Meta:
        model = Locker
        fields = '__all__'

class LockerLogSerializer(serializers.ModelSerializer):
    locker = LockerSerializer(read_only=True)
    user = UserDetailSerializer(read_only=True)
    
    class Meta:
        model = LockerLog
        fields = '__all__'

class DeliverySerializer(serializers.ModelSerializer):
    class Meta:
        model = Delivery
        fields = '__all__'


class OtpValidationSerializer(serializers.Serializer):
    otp = serializers.CharField(max_length=6)
