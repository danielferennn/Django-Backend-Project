from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from .models import User


class UserRegistrationSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, required=True, style={'input_type': 'password'})
    first_name = serializers.CharField(required=False, allow_blank=True)
    last_name = serializers.CharField(required=False, allow_blank=True)

    class Meta:
        model = User
        fields = ('username', 'email', 'password', 'role', 'first_name', 'last_name')
        extra_kwargs = {'role': {'required': False}}

    def to_internal_value(self, data):
        allowed_fields = {'username', 'email', 'password', 'role', 'first_name', 'last_name'}
        unknown_fields = set(data) - allowed_fields
        if unknown_fields:
            raise serializers.ValidationError({
                field: 'Unexpected field.' for field in sorted(unknown_fields)
            })
        return super().to_internal_value(data)

    def validate(self, attrs):
        role = attrs.get('role', User.Role.BUYER)
        if role != User.Role.BUYER:
            raise serializers.ValidationError({'role': 'Public registration is limited to BUYER accounts.'})

        candidate = User(
            username=attrs.get('username', ''),
            email=attrs.get('email', ''),
            first_name=attrs.get('first_name', ''),
            last_name=attrs.get('last_name', ''),
            role=User.Role.BUYER,
        )
        try:
            validate_password(attrs['password'], user=candidate)
        except DjangoValidationError as exc:
            raise serializers.ValidationError({'password': list(exc.messages)}) from exc
        return attrs

    def create(self, validated_data):
        validated_data.pop('role', None)
        password = validated_data.pop('password')
        return User.objects.create_user(
            password=password,
            role=User.Role.BUYER,
            **validated_data,
        )


class UserDetailSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ('id', 'username', 'email', 'first_name', 'last_name', 'face_id', 'role')


class UserPublicSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ('id', 'username', 'first_name', 'last_name')