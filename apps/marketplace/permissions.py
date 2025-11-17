from rest_framework.permissions import BasePermission
from .models import Store

class IsStoreOwner(BasePermission):
    def has_object_permission(self, request, view, obj):
        if isinstance(obj, Store):
            return obj.owner == request.user
        return obj.store.owner == request.user


class IsOwnerOrReadOnly(BasePermission):
    def has_object_permission(self, request, view, obj):
        if request.method in ('GET', 'HEAD', 'OPTIONS'):
            return True
        return getattr(obj, 'seller', None) == request.user


class IsTransactionParticipant(BasePermission):
    def has_object_permission(self, request, view, obj):
        return request.user == obj.buyer or request.user == obj.seller
