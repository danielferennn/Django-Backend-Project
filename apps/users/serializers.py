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
    allow_role_override = False

    class Meta:
        model = User
        fields = ('username', 'email', 'password', 'role', 'face_id')

    def _requested_role(self):
        if not self.allow_role_override:
            return None
        raw = (getattr(self, 'initial_data', None) or {}).get('role')
        if not raw:
            return None
        candidate = str(raw).strip().upper()
        normalized = User.LEGACY_ROLE_MAP.get(candidate)
        valid_roles = {choice[0] for choice in User.ROLE_CHOICES}
        if normalized in valid_roles:
            return normalized
        return None

    def create(self, validated_data):
        password = validated_data.pop('password')
        role = self._requested_role() or self.role_value
        user = User.objects.create_user(
            password=password,
            role=role,
            **validated_data,
        )
        return user


class BuyerRegistrationSerializer(UserRegistrationSerializerBase):
    role_value = User.ROLE_BUYER
    allow_role_override = False


class OwnerRegistrationSerializer(UserRegistrationSerializerBase):
    role_value = User.ROLE_OWNER
    allow_role_override = False


class UserRegistrationSerializer(BuyerRegistrationSerializer):
    """Backward compatible serializer for the legacy /register endpoint."""
    allow_role_override = True


class UserDetailSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ('id', 'username', 'email', 'role', 'face_id')
