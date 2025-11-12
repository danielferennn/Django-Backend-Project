from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView

from .models import User
from .serializers import (
    BuyerRegistrationSerializer,
    OwnerRegistrationSerializer,
    UserDetailSerializer,
    UserRegistrationSerializer,
)


class BaseRegistrationView(APIView):
    permission_classes = (permissions.AllowAny,)
    serializer_class = None

    def post(self, request, *args, **kwargs):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class BuyerRegistrationView(BaseRegistrationView):
    serializer_class = BuyerRegistrationSerializer


class OwnerRegistrationView(BaseRegistrationView):
    serializer_class = OwnerRegistrationSerializer


class UserRegistrationView(BuyerRegistrationView):
    """
    Backwards-compatible endpoint that defaults to registering buyers.
    """
    serializer_class = UserRegistrationSerializer


class UserLoginView(TokenObtainPairView):
    permission_classes = (permissions.AllowAny,)


class UserProfileView(generics.RetrieveAPIView):
    queryset = User.objects.all()
    serializer_class = UserDetailSerializer
    permission_classes = (permissions.IsAuthenticated,)

    def get_object(self):
        return self.request.user
