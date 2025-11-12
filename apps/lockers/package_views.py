from rest_framework import generics, permissions

from .models import Package
from .serializers import PackageSerializer
from apps.users.permissions import IsOwner


class PackageListCreateView(generics.ListCreateAPIView):
    serializer_class = PackageSerializer
    permission_classes = [permissions.IsAuthenticated, IsOwner]

    def get_queryset(self):
        return Package.objects.filter(owner=self.request.user).order_by('-created_at')

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user)


class PackageDetailView(generics.RetrieveUpdateAPIView):
    serializer_class = PackageSerializer
    permission_classes = [permissions.IsAuthenticated, IsOwner]

    def get_queryset(self):
        return Package.objects.filter(owner=self.request.user)


class _BasePackageStatusListView(generics.ListAPIView):
    serializer_class = PackageSerializer
    permission_classes = [permissions.IsAuthenticated, IsOwner]
    status_value = None

    def get_queryset(self):
        queryset = Package.objects.filter(owner=self.request.user).order_by('-created_at')
        if self.status_value:
            queryset = queryset.filter(status=self.status_value)
        return queryset


class PackageActiveListView(_BasePackageStatusListView):
    status_value = Package.PackageStatus.ACTIVE


class PackageCompletedListView(_BasePackageStatusListView):
    status_value = Package.PackageStatus.COMPLETED
