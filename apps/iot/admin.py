from django.contrib import admin

from .models import IoTEvent


@admin.register(IoTEvent)
class IoTEventAdmin(admin.ModelAdmin):
    list_display = ('id', 'event_type', 'user', 'created_at')
    list_filter = ('event_type', 'created_at')
    search_fields = ('event_type', 'user__email')
