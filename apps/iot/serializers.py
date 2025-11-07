from rest_framework import serializers

from .models import IoTEvent


class IoTEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = IoTEvent
        fields = ['id', 'user', 'event_type', 'payload', 'created_at']
        read_only_fields = ['id', 'created_at']


class IoTIngestSerializer(serializers.Serializer):
    user_id = serializers.IntegerField(required=False, allow_null=True)
    event_type = serializers.ChoiceField(choices=IoTEvent.EventType.choices, required=False)
    payload = serializers.JSONField()

    def create(self, validated_data):
        user_id = validated_data.pop('user_id', None)
        user = None
        if user_id is not None:
            from django.contrib.auth import get_user_model

            User = get_user_model()
            user = User.objects.filter(id=user_id).first()

        event_type = validated_data.get('event_type') or IoTEvent.EventType.GENERIC
        return IoTEvent.objects.create(
            user=user,
            event_type=event_type,
            payload=validated_data['payload'],
        )


class FaceMatchSerializer(serializers.Serializer):
    face_id = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    confidence = serializers.FloatField()
    timestamp = serializers.DateTimeField()
    image_url = serializers.URLField(required=False, allow_blank=True)
    user_id = serializers.IntegerField(required=False)
    metadata = serializers.JSONField(required=False)


class FaceMatchResponseSerializer(serializers.Serializer):
    status = serializers.CharField()
    event_id = serializers.IntegerField()
    user_id = serializers.IntegerField(allow_null=True, required=False)
