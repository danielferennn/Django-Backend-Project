from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Notification
from .serializers import NotificationPushSerializer, NotificationSerializer
from .tasks import push_notification_task


class NotificationListView(generics.ListAPIView):
    serializer_class = NotificationSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Notification.objects.filter(user=self.request.user).order_by('-created_at')


class NotificationPushView(APIView):
    permission_classes = [permissions.IsAdminUser]

    @extend_schema(request=NotificationPushSerializer, responses={'201': OpenApiResponse(description='Notification queued')})
    def post(self, request):
        serializer = NotificationPushSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        push_notification_task.delay(**serializer.validated_data)
        return Response({'status': 'queued'}, status=status.HTTP_201_CREATED)
