from django.urls import path, include
from rest_framework.routers import DefaultRouter
from . import views

router = DefaultRouter()
router.register(r'products', views.ProductViewSet, basename='product')
router.register(r'transactions', views.TransactionViewSet, basename='transaction')

urlpatterns = [
    path('transactions/create/', views.CreateTransactionView.as_view(), name='create-transaction'),
    path('transactions/<int:pk>/payment-proof/', views.UploadPaymentProofView.as_view(), name='upload-payment-proof'),
    path('transactions/<int:pk>/shipping/', views.UpdateTransactionShippingView.as_view(), name='update-transaction-shipping'),
    path('transactions/<int:pk>/approve/', views.ApproveTransactionView.as_view(), name='approve-transaction'),
    path('transactions/<int:pk>/reject/', views.RejectTransactionView.as_view(), name='reject-transaction'),
    path('transactions/<int:pk>/generate-otp/', views.GenerateOtpView.as_view(), name='generate-otp'),
    path('transactions/deposit-item/', views.SellerDepositItemView.as_view(), name='seller-deposit-item'),
    path('transactions/retrieve-item/', views.BuyerRetrieveItemView.as_view(), name='buyer-retrieve-item'),
    path('store/me/', views.MyStoreView.as_view(), name='my-store'),
    path('', include(router.urls)),
    path('webhooks/payment/', views.PaymentWebhookView.as_view(), name='payment-webhook'),
    path('webhooks/qris/confirm/', views.QrisConfirmWebhookView.as_view(), name='qris-confirm-webhook'),
    path('webhooks/confirm-deposit/', views.ConfirmMarketplaceDepositWebhookView.as_view(), name='confirm-marketplace-deposit'),
    path('webhooks/confirm-retrieval/', views.ConfirmMarketplaceRetrievalWebhookView.as_view(), name='confirm-marketplace-retrieval'),
]
