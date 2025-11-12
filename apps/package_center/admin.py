from django.contrib import admin

from .models import PackageEntry


@admin.register(PackageEntry)
class PackageEntryAdmin(admin.ModelAdmin):
    list_display = (
        'package_name',
        'tracking_number',
        'owner',
        'status',
        'courier',
        'order_date',
    )
    list_filter = ('status', 'courier')
    search_fields = ('package_name', 'tracking_number', 'receiver_name')
