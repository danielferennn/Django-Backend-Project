from django.contrib.auth import get_user_model
from rest_framework import serializers

from .models import Notification


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = ['id', 'title', 'body', 'created_at']


class NotificationPushSerializer(serializers.Serializer):
    user_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        allow_empty=False,
        max_length=100,
    )
    title = serializers.CharField(max_length=255)
    body = serializers.CharField()

    def validate_user_ids(self, user_ids):
        unique_ids = list(dict.fromkeys(user_ids))
        User = get_user_model()
        existing_ids = set(User.objects.filter(pk__in=unique_ids).values_list('pk', flat=True))
        if existing_ids != set(unique_ids):
            raise serializers.ValidationError('Every recipient must exist.')
        return unique_ids
