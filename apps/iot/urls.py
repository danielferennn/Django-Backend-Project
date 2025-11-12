from django.urls import path

from .views import DeviceEventIngestView

urlpatterns = [
    path('events/', DeviceEventIngestView.as_view(), name='iot-events-ingest'),
]
