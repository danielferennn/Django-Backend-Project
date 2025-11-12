from rest_framework.routers import DefaultRouter

from .views import PackageEntryViewSet

router = DefaultRouter()
router.register(r'packages', PackageEntryViewSet, basename='package-entry')

urlpatterns = router.urls
