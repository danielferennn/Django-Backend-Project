from django.urls import path
from .views import (
    ConfirmDepositWebhookView,
    LockerLogListView,
    OpenStorageLockerView,
    ValidateOtpView,
    VerifyDeliveryView,
)

urlpatterns = [
    path('inbound/verify-delivery/', VerifyDeliveryView.as_view(), name='verify-delivery'),
    path('inbound/confirm-deposit/', ConfirmDepositWebhookView.as_view(), name='confirm-deposit-webhook'),
    path('storage/open/', OpenStorageLockerView.as_view(), name='open-storage-locker'),
    path('logs/', LockerLogListView.as_view(), name='locker-logs'),
    path('otp/validate/', ValidateOtpView.as_view(), name='locker-otp-validate'),
]
