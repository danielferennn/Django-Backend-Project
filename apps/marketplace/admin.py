from django.contrib import admin

from .models import Product, Store, Transaction


@admin.register(Store)
class StoreAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'owner', 'location')
    search_fields = ('name', 'owner__username')
    readonly_fields = tuple(field.name for field in Store._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'seller', 'price', 'stock', 'is_active')
    list_filter = ('is_active',)
    search_fields = ('name', 'seller__username')
    readonly_fields = tuple(field.name for field in Product._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = ('id', 'buyer', 'seller', 'product', 'quantity', 'total_price', 'status', 'created_at')
    list_filter = ('status', 'created_at')
    search_fields = ('buyer__username', 'seller__username', 'product__name')
    fields = (
        'id', 'buyer', 'seller', 'product', 'quantity', 'total_price', 'status',
        'buyer_full_name', 'shipping_address', 'buyer_phone_number',
        'payment_proof_uploaded_at', 'paid_at', 'locker', 'deposited_at',
        'otp_expires_at', 'otp_consumed_at', 'otp_failed_attempts',
        'inventory_released_at', 'created_at', 'updated_at',
    )
    readonly_fields = fields

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
