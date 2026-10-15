from django.contrib import admin

from .models import IoTEvent


@admin.register(IoTEvent)
class IoTEventAdmin(admin.ModelAdmin):
    list_display = ('event_id', 'command', 'event_type', 'user', 'created_at')
    list_filter = ('event_type', 'created_at')
    search_fields = ('event_type', 'command__locker__number')
    fields = ('event_id', 'command', 'event_type', 'user', 'created_at')
    readonly_fields = fields

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
