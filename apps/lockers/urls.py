from django.urls import path

from .views import (
    DeliveryRetrieveView,
    DeliveryStorageView,
    DeliveryTransferView,
    LockerCommandReconciliationView,
    LockerCommandStatusView,
    LockerLogListView,
    VerifyDeliveryView,
)

urlpatterns = [
    path('inbound/verify-delivery/', VerifyDeliveryView.as_view(), name='verify-delivery'),
    path('inbound/<int:delivery_id>/transfer/', DeliveryTransferView.as_view(), name='delivery-transfer'),
    path('inbound/<int:delivery_id>/store/', DeliveryStorageView.as_view(), name='delivery-store'),
    path('deliveries/<int:delivery_id>/retrieve/', DeliveryRetrieveView.as_view(), name='delivery-retrieve'),
    path('commands/<uuid:command_id>/', LockerCommandStatusView.as_view(), name='locker-command-status'),
    path(
        'commands/<uuid:command_id>/reconcile/',
        LockerCommandReconciliationView.as_view(),
        name='locker-command-reconcile',
    ),
    path('logs/', LockerLogListView.as_view(), name='locker-logs'),
]
