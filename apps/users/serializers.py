from rest_framework import serializers

from .models import User


class UserRegistrationSerializerBase(serializers.ModelSerializer):
    password = serializers.CharField(
        write_only=True,
        required=True,
        style={'input_type': 'password'},
    )
    role = serializers.CharField(read_only=True)
    role_value = User.ROLE_BUYER

    class Meta:
        model = User
        fields = ('username', 'email', 'password', 'role', 'face_id')

    def create(self, validated_data):
        password = validated_data.pop('password')
        user = User.objects.create_user(
            password=password,
            role=self.role_value,
            **validated_data,
        )
        return user


class BuyerRegistrationSerializer(UserRegistrationSerializerBase):
    role_value = User.ROLE_BUYER


class OwnerRegistrationSerializer(UserRegistrationSerializerBase):
    role_value = User.ROLE_OWNER


class UserRegistrationSerializer(BuyerRegistrationSerializer):
    """Backward compatible serializer for the legacy /register endpoint."""


class UserDetailSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ('id', 'username', 'email', 'role', 'face_id')
