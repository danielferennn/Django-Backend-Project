from django.urls import path, include
from rest_framework.routers import DefaultRouter
from . import views

router = DefaultRouter()
router.register(r'products', views.ProductViewSet, basename='product')
router.register(r'my-products', views.MyProductViewSet, basename='my-product')

urlpatterns = [
    path('transactions/create/', views.CreateTransactionView.as_view(), name='create-transaction'),
    path('transactions/', views.TransactionListView.as_view(), name='transaction-list'),
    path('transactions/<int:pk>/', views.TransactionDetailView.as_view(), name='transaction-detail'),
    path('transactions/<int:pk>/approve/', views.TransactionApproveView.as_view(), name='transaction-approve'),
    path('transactions/<int:pk>/reject/', views.TransactionRejectView.as_view(), name='transaction-reject'),
    path('transactions/<int:pk>/payment-proof/', views.TransactionPaymentProofUploadView.as_view(), name='transaction-payment-proof'),
    path('transactions/<int:pk>/deposit/', views.SellerDepositItemView.as_view(), name='seller-deposit-item'),
    path('transactions/<int:pk>/retrieve/', views.BuyerRetrieveItemView.as_view(), name='buyer-retrieve-item'),
    path('transactions/<int:pk>/pickup-code/reissue/', views.PickupCodeReissueView.as_view(), name='pickup-code-reissue'),
    path('transactions/<int:pk>/shipping/', views.TransactionShippingUpdateView.as_view(), name='transaction-shipping'),
    path('transactions/<int:pk>/buyer-shipping/', views.TransactionBuyerShippingUpdateView.as_view(), name='transaction-buyer-shipping'),
    path('store/me/', views.MyStoreView.as_view(), name='my-store'),
    path('stores/', views.StoreListView.as_view(), name='store-list'),
    path('stores/<int:pk>/', views.StoreDetailView.as_view(), name='store-detail'),
    path('', include(router.urls)),
]
