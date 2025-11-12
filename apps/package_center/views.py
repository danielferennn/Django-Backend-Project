from django.contrib.auth import get_user_model
from rest_framework import permissions, viewsets

from .models import PackageEntry
from .serializers import PackageEntrySerializer

User = get_user_model()


class IsOwnerReceiver(permissions.BasePermission):
    def has_object_permission(self, request, view, obj):
        return obj.owner == request.user

    def has_permission(self, request, view):
        return request.user.is_authenticated


class PackageEntryViewSet(viewsets.ModelViewSet):
    serializer_class = PackageEntrySerializer
    permission_classes = [permissions.IsAuthenticated, IsOwnerReceiver]

    def get_queryset(self):
        queryset = PackageEntry.objects.filter(owner=self.request.user)
        status_filter = self.request.query_params.get('status')
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        return queryset

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user)
