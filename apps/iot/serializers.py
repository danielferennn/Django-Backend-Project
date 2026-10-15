import json

from rest_framework import serializers

from .models import IoTEvent

MAX_EVENT_PAYLOAD_BYTES = 4096
DEVICE_EVENT_TYPES = (
    IoTEvent.EventType.LOCKER_OPENED,
    IoTEvent.EventType.ITEM_DETECTED,
    IoTEvent.EventType.ITEM_REMOVED,
    IoTEvent.EventType.COMMAND_FAILED,
)


class IoTEventSerializer(serializers.ModelSerializer):
    command_id = serializers.UUIDField(source='command.command_id', read_only=True)

    class Meta:
        model = IoTEvent
        fields = ['id', 'event_id', 'command_id', 'event_type', 'payload', 'created_at']
        read_only_fields = fields


class IoTIngestSerializer(serializers.Serializer):
    event_id = serializers.UUIDField()
    command_id = serializers.UUIDField()
    event_type = serializers.ChoiceField(choices=DEVICE_EVENT_TYPES)
    payload = serializers.JSONField(required=False, default=dict)

    def validate_payload(self, payload):
        if not isinstance(payload, dict):
            raise serializers.ValidationError('Event payload must be a JSON object.')
        if len(json.dumps(payload, separators=(',', ':'), ensure_ascii=True).encode('utf-8')) > MAX_EVENT_PAYLOAD_BYTES:
            raise serializers.ValidationError('Event payload exceeds 4096 bytes.')
        return payload
