from rest_framework import serializers

from .models import Notification


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = ['id', 'title', 'body', 'created_at']


class NotificationPushSerializer(serializers.Serializer):
    user_id = serializers.IntegerField()
    title = serializers.CharField(max_length=255)
    body = serializers.CharField()
