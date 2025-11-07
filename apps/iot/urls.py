from django.urls import path

from .views import DeviceEventIngestView, FaceMatchIngestView

urlpatterns = [
    path('events/', DeviceEventIngestView.as_view(), name='iot-events-ingest'),
    path('face-match/', FaceMatchIngestView.as_view(), name='iot-face-match-ingest'),
]
