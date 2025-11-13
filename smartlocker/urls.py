"""
URL configuration for smartlocker project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/4.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

urlpatterns = [
    path('admin/', admin.site.urls),
    # buat user ini perubahan dari punya mas fahki, terus ada tambahan urls face juga
    path('users/',include('users.urls')),
    path('face/',include('facerecognition.urls')), 
    # path('api/v1/users/', include('apps.users.urls')),
    path('api/v1/lockers/', include('apps.lockers.urls')),
    path('api/v1/packages/', include('apps.lockers.package_urls')),
    path('api/v1/marketplace/', include('apps.marketplace.urls')),
    path('api/v1/package-center/', include('apps.package_center.urls')),
    path('api/v1/iot/', include('apps.iot.urls')),
    path('api/v1/notifications/', include('apps.notifications.urls')),
    path('api/schema/', SpectacularAPIView.as_view(), name='api-schema'),
    path('api/docs/', SpectacularSwaggerView.as_view(url_name='api-schema'), name='api-docs'),
]

urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
