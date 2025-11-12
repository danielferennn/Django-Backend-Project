from datetime import datetime

from django.utils.dateparse import parse_datetime
from rest_framework import serializers

from .models import PackageEntry


class FlexibleDateField(serializers.DateField):
    """
    Accept plain dates as well as full ISO-8601 datetime strings and store only the date portion.
    """

    def to_internal_value(self, value):
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, str):
            parsed = parse_datetime(value)
            if parsed:
                return parsed.date()
        return super().to_internal_value(value)


class PackageEntrySerializer(serializers.ModelSerializer):
    owner = serializers.ReadOnlyField(source='owner.id')
    order_date = FlexibleDateField(required=False, allow_null=True)
    delivered_date = FlexibleDateField(required=False, allow_null=True)

    class Meta:
        model = PackageEntry
        fields = [
            'id',
            'owner',
            'package_name',
            'tracking_number',
            'courier',
            'order_date',
            'delivered_date',
            'status',
            'locker_slot',
            'receiver_name',
            'receiver_phone',
            'notes',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'owner', 'created_at', 'updated_at']
