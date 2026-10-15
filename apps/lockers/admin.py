from django import forms
from django.contrib import admin
from django.core.exceptions import ValidationError
from django.db import transaction


from .models import Delivery, Locker, LockerLog, LockerRequest, Package


@admin.register(Locker)
class LockerAdmin(admin.ModelAdmin):
    list_display = ('number', 'type', 'status', 'gpio_pin')
    list_filter = ('type', 'status')
    search_fields = ('number',)
    fields = ('number', 'type', 'status', 'last_opened_by', 'gpio_pin')
    readonly_fields = ('status', 'last_opened_by')

    def get_readonly_fields(self, request, obj=None):
        fields = list(super().get_readonly_fields(request, obj))
        if obj is not None:
            fields.extend(('number', 'type'))
        return tuple(fields)

    def save_model(self, request, obj, form, change):
        if change:
            original = Locker.objects.get(pk=obj.pk)
            obj.number = original.number
            obj.type = original.type
            obj.status = original.status
            obj.last_opened_by_id = original.last_opened_by_id
        super().save_model(request, obj, form, change)

    def has_delete_permission(self, request, obj=None):
        return False


class DeliveryAdminForm(forms.ModelForm):
    owner = forms.ModelChoiceField(queryset=Delivery._meta.get_field('owner').remote_field.model.objects.all())

    class Meta:
        model = Delivery
        fields = ('receipt_number', 'courier', 'locker', 'owner')


@admin.register(Delivery)
class DeliveryAdmin(admin.ModelAdmin):
    form = DeliveryAdminForm
    list_display = ('receipt_number', 'courier', 'owner', 'locker', 'storage_locker', 'status')
    list_filter = ('status',)
    search_fields = ('receipt_number', 'owner__email', 'courier__email')
    fields = (
        'receipt_number', 'courier', 'locker', 'owner', 'storage_locker', 'status',
        'retrieved_at', 'created_at', 'updated_at',
    )
    readonly_fields = ('storage_locker', 'status', 'retrieved_at', 'created_at', 'updated_at')

    def get_readonly_fields(self, request, obj=None):
        fields = list(super().get_readonly_fields(request, obj))
        if obj is not None:
            fields.extend(('receipt_number', 'courier', 'locker'))
            if obj.owner_id:
                fields.append('owner')
        return tuple(fields)

    def save_model(self, request, obj, form, change):
        if not change and not obj.owner_id:
            raise ValidationError({'owner': 'A delivery recipient is required.'})
        with transaction.atomic():
            if change:
                original = Delivery.objects.select_for_update().get(pk=obj.pk)
                obj.receipt_number = original.receipt_number
                obj.courier_id = original.courier_id
                obj.locker_id = original.locker_id
                obj.status = original.status
                obj.storage_locker_id = original.storage_locker_id
                obj.retrieved_at = original.retrieved_at
                recipient_assigned = original.owner_id is None and obj.owner_id is not None
                if original.owner_id is not None:
                    obj.owner_id = original.owner_id
                elif obj.owner_id is None:
                    raise ValidationError({'owner': 'A delivery recipient is required.'})
            else:
                recipient_assigned = False
            super().save_model(request, obj, form, change)
            if recipient_assigned:
                LockerLog.objects.create(
                    locker_id=obj.locker_id,
                    user=request.user,
                    delivery=obj,
                    action=LockerLog.Action.RECONCILE,
                    is_successful=True,
                    details='Staff assigned a recipient to a legacy delivery.',
                )

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(LockerLog)
class LockerLogAdmin(admin.ModelAdmin):
    list_display = ('id', 'locker', 'user', 'action', 'timestamp', 'is_successful')
    list_filter = ('action', 'is_successful', 'timestamp')
    search_fields = ('locker__number', 'user__email')
    readonly_fields = tuple(field.name for field in LockerLog._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(LockerRequest)
class LockerRequestAdmin(admin.ModelAdmin):
    list_display = ('command_id', 'locker', 'purpose', 'status', 'created_at', 'expires_at')
    list_filter = ('purpose', 'status')
    search_fields = ('locker__number', 'error_code')
    readonly_fields = tuple(
        field.name for field in LockerRequest._meta.fields if field.name != 'legacy_metadata'
    ) + ('legacy_metadata',)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


admin.site.register(Package)