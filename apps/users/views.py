from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView

# tambahan library baru punya mas fahmi dibawah ini
from django.contrib.auth import get_user_model
from django.shortcuts import render

from . import models as models
from . import serializers as serializer
from .models import User
from .serializers import (
    UserDetailSerializer,
    UserRegistrationSerializer,
)


class UserRegistrationView(generics.CreateAPIView):
    """
    View untuk registrasi user dengan berbagai role.
    OWNER role memerlukan first_name dan last_name.
    """

    queryset = models.User.objects.all()
    permission_classes = (permissions.AllowAny,)
    serializer_class = serializer.UserRegistrationSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)

        # Validasi data
        if serializer.is_valid():
            user = serializer.save()

            # Response berbeda berdasarkan role
            response_data = {
                'message': 'User registered successfully',
                'user': {
                    'id': user.id,
                    'username': user.username,
                    'email': user.email,
                    'role': user.role,
                }
            }

            # Tambahkan info khusus untuk OWNER
            if user.role == models.User.Role.OWNER:
                response_data['user']['first_name'] = user.first_name
                response_data['user']['last_name'] = user.last_name
                response_data['user']['face_id'] = user.face_id
                response_data['message'] = 'Owner registered successfully. Face ID generated.'

            return Response(response_data, status=status.HTTP_201_CREATED)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

class Usergetrole(APIView):
    def get(self,request):
        faceidowner=request.query_params.get('faceid', None)
        # items=models.User.objects.filter(role=models.User.Role.OWNER)
        items=models.User.objects.get(face_id=faceidowner)
        userdetail=serializer.UserDetailSerializer(items)
        # userdetail=serializer.UserDetailSerializer(items,many=True)
        return Response(userdetail.data,status=status.HTTP_200_OK)

class UserLoginView(TokenObtainPairView):
    permission_classes = (permissions.AllowAny,)

class UserProfileView(generics.RetrieveAPIView):
    queryset = User.objects.all()
    serializer_class = UserDetailSerializer
    permission_classes = (permissions.IsAuthenticated,)

    def get_object(self):
        return self.request.user
