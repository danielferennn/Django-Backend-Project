from django.urls import path, include
from rest_framework.routers import DefaultRouter
from . import views

router = DefaultRouter()
router.register(r'products', views.ProductViewSet, basename='product')
router.register(r'my-products', views.MyProductViewSet, basename='my-product')
router.register(r'transactions', views.TransactionViewSet, basename='transaction')

urlpatterns = [
    path('transactions/create/', views.CreateTransactionView.as_view(), name='create-transaction'),
    path('transactions/', views.TransactionListView.as_view(), name='transaction-list'),
    path('transactions/<int:pk>/', views.TransactionDetailView.as_view(), name='transaction-detail'),
    path('transactions/<int:pk>/approve/', views.TransactionApproveView.as_view(), name='transaction-approve'),
    path('transactions/<int:pk>/reject/', views.TransactionRejectView.as_view(), name='transaction-reject'),
    path('transactions/<int:pk>/payment-proof/', views.TransactionPaymentProofUploadView.as_view(), name='transaction-payment-proof'),
    path('transactions/<int:pk>/generate-otp/', views.TransactionGenerateOtpView.as_view(), name='transaction-generate-otp'),
    path('transactions/<int:pk>/shipping/', views.TransactionShippingUpdateView.as_view(), name='transaction-shipping'),
    path('transactions/<int:pk>/buyer-shipping/', views.TransactionBuyerShippingUpdateView.as_view(), name='transaction-buyer-shipping'),
    path('transactions/deposit-item/', views.SellerDepositItemView.as_view(), name='seller-deposit-item'),
    path('transactions/retrieve-item/', views.BuyerRetrieveItemView.as_view(), name='buyer-retrieve-item'),
    path('store/me/', views.MyStoreView.as_view(), name='my-store'),
    path('stores/', views.StoreListView.as_view(), name='store-list'),
    path('stores/<int:pk>/', views.StoreDetailView.as_view(), name='store-detail'),
    path('', include(router.urls)),
    path('webhooks/payment/', views.PaymentWebhookView.as_view(), name='payment-webhook'),
    path('webhooks/confirm-deposit/', views.ConfirmMarketplaceDepositWebhookView.as_view(), name='confirm-marketplace-deposit'),
    path('webhooks/confirm-retrieval/', views.ConfirmMarketplaceRetrievalWebhookView.as_view(), name='confirm-marketplace-retrieval'),
]
