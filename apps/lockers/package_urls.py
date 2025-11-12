from django.urls import path

from . import package_views

urlpatterns = [
    path('', package_views.PackageListCreateView.as_view(), name='package-list'),
    path('active/', package_views.PackageActiveListView.as_view(), name='package-active'),
    path('completed/', package_views.PackageCompletedListView.as_view(), name='package-completed'),
    path('<int:pk>/', package_views.PackageDetailView.as_view(), name='package-detail'),
]
