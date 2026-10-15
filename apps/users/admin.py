import logging

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.core.exceptions import PermissionDenied

from .models import User

logger = logging.getLogger(__name__)


@admin.register(User)
class SmartLockerUserAdmin(UserAdmin):
    fieldsets = (
        (None, {'fields': ('username', 'password')}),
        ('Personal info', {'fields': ('first_name', 'last_name', 'email', 'role', 'face_id')}),
        ('Permissions', {'fields': ('is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions')}),
        ('Important dates', {'fields': ('last_login', 'date_joined')}),
    )
    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': (
                'username', 'email', 'password1', 'password2', 'first_name', 'last_name',
                'role', 'is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions',
            ),
        }),
    )
    readonly_fields = ('face_id', 'last_login', 'date_joined')
    list_display = ('username', 'email', 'role', 'is_active', 'is_staff')
    list_filter = ('role', 'is_active', 'is_staff')
    search_fields = ('username', 'email', 'face_id')
    ordering = ('email',)
    filter_horizontal = ('groups', 'user_permissions')

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        if not request.user.is_superuser:
            queryset = queryset.filter(is_staff=False, is_superuser=False)
        return queryset

    def get_fieldsets(self, request, obj=None):
        fieldsets = super().get_fieldsets(request, obj)
        if request.user.is_superuser:
            return fieldsets
        protected = {'is_staff', 'is_superuser', 'groups', 'user_permissions'}
        return [
            (name, {
                **options,
                'fields': tuple(field for field in options['fields'] if field not in protected),
            })
            for name, options in fieldsets
        ]

    def has_change_permission(self, request, obj=None):
        if obj is not None and not request.user.is_superuser and (obj.is_staff or obj.is_superuser):
            return False
        return super().has_change_permission(request, obj)

    def has_view_permission(self, request, obj=None):
        if obj is not None and not request.user.is_superuser and (obj.is_staff or obj.is_superuser):
            return False
        return super().has_view_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if obj is not None and not request.user.is_superuser and (obj.is_staff or obj.is_superuser):
            return False
        return super().has_delete_permission(request, obj)

    def save_model(self, request, obj, form, change):
        if not request.user.is_superuser:
            if change:
                original = User.objects.get(pk=obj.pk)
                if original.is_staff or original.is_superuser:
                    raise PermissionDenied
            obj.is_staff = False
            obj.is_superuser = False
        super().save_model(request, obj, form, change)

    def delete_model(self, request, obj):
        # Deleting the user cascades the Datawajahnew rows but never the private
        # bytes, so capture the stored names first and purge them after commit.
        from apps.facerecognition.services import capture_training_file_names

        names = capture_training_file_names(obj)
        super().delete_model(request, obj)
        self._schedule_biometric_cleanup(names)

    def delete_queryset(self, request, queryset):
        from apps.facerecognition.services import capture_training_file_names

        names = []
        for user in queryset:
            names.extend(capture_training_file_names(user))
        super().delete_queryset(request, queryset)
        self._schedule_biometric_cleanup(names)

    def _schedule_biometric_cleanup(self, names):
        """Purge private training bytes and rebuild/remove the model.

        Deferred with ``transaction.on_commit`` because admin deletions run
        inside a transaction: a rollback must not leave the model rebuilt (or
        the bytes deleted) for rows that still exist.

        Only biometric-bearing users need this: when no training files were
        referenced (e.g. deleting a buyer) there is nothing to purge and the
        model is unaffected, so no rebuild is scheduled at all.

        Deletion has already committed by the time this runs, so cleanup must
        never raise: a purge failure is logged and the model rebuild is still
        attempted, and a rebuild failure is logged too.  Recognition stays
        fail-closed (the ineligible label is excluded) regardless.
        """
        if not names:
            return

        from django.db import transaction

        from apps.facerecognition.services import purge_training_files, rebuild_face_model

        def _cleanup():
            try:
                purge_training_files(names)
            except Exception:
                logger.exception(
                    'Could not purge %d biometric training file(s) after a user delete.',
                    len(names),
                )
            try:
                rebuild_face_model()
            except Exception:
                # On commit the delete has already committed, so no rebuild
                # failure (OpenCV, OSError/PermissionError on the lock, mkdir or
                # os.replace, or ModelPermissionError) may propagate.  The label
                # map stays fail-closed for the deleted/ineligible owner.
                logger.exception('Biometric model could not be rebuilt after a user delete.')

        transaction.on_commit(_cleanup)
