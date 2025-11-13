from rest_framework import serializers
from .models import Datawajahnew,Logsmartaccess2
class Imagedatawajahserializernew(serializers.ModelSerializer):
    class Meta:
        model=Datawajahnew
        fields='__all__'
class Logsmartaccesserializernew(serializers.ModelSerializer):
    # id_user=Datauserserializer(read_only=True)
    class Meta:
        model=Logsmartaccess2
        fields='__all__'
