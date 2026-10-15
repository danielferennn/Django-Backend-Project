from django.urls import reverse
from rest_framework import serializers

from .models import Datawajahnew, Logsmartaccess2


class FaceImageUploadSerializer(serializers.Serializer):
    image_user = serializers.ImageField()

class Imagedatawajahserializernew(serializers.ModelSerializer):
    image_url = serializers.SerializerMethodField()

    class Meta:
        model = Datawajahnew
        fields = ('id', 'image_url', 'created_at', 'updated_at')

    def get_image_url(self, obj):
        request = self.context.get('request')
        url = reverse('face-training-image', kwargs={'pk': obj.pk})
        return request.build_absolute_uri(url) if request else url


class Logsmartaccesserializernew(serializers.ModelSerializer):
    image_url = serializers.SerializerMethodField()

    class Meta:
        model = Logsmartaccess2
        fields = ('log_id', 'id_face_user', 'image_url', 'access_time', 'status')

    def get_image_url(self, obj):
        if not obj.image:
            return None
        request = self.context.get('request')
        url = reverse('face-access-log-image', kwargs={'log_id': obj.log_id})
        return request.build_absolute_uri(url) if request else url
