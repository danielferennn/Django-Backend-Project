from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import generics, permissions, serializers, status
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView

from .models import User
from .serializers import UserDetailSerializer, UserRegistrationSerializer
from .throttling import UserLoginThrottle, UserRegistrationThrottle

class UserLookupErrorSerializer(serializers.Serializer):
    """Reusable body for ``{detail, faceid}`` DRF error responses (schema-only)."""

    detail = serializers.CharField(required=False)
    faceid = serializers.ListField(child=serializers.CharField(), required=False)


class UserRegistrationView(generics.CreateAPIView):
    queryset = User.objects.all()
    authentication_classes = []
    permission_classes = (permissions.AllowAny,)
    throttle_classes = (UserRegistrationThrottle,)
    serializer_class = UserRegistrationSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        return Response(
            {
                'message': 'User registered successfully',
                'user': {
                    'id': user.id,
                    'username': user.username,
                    'email': user.email,
                    'role': user.role,
                },
            },
            status=status.HTTP_201_CREATED,
        )


class Usergetrole(APIView):
    permission_classes = (permissions.IsAuthenticated,)

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name='faceid',
                type=str,
                location=OpenApiParameter.QUERY,
                required=True,
                description='Face id of the user to look up.',
            ),
        ],
        responses={
            200: UserDetailSerializer,
            400: UserLookupErrorSerializer,
            403: UserLookupErrorSerializer,
            404: UserLookupErrorSerializer,
        },
        description=(
            'Look up a user profile by face id. Non-staff callers may only look '
            'up their own record; others receive 404.'
        ),
    )
    def get(self, request):
        face_id = request.query_params.get('faceid')
        if not face_id:
            raise ValidationError({'faceid': 'This query parameter is required.'})
        try:
            user = User.objects.get(face_id=face_id)
        except User.DoesNotExist as exc:
            raise NotFound('User not found.') from exc
        if not request.user.is_staff and user.pk != request.user.pk:
            raise NotFound('User not found.')
        return Response(UserDetailSerializer(user).data, status=status.HTTP_200_OK)


class UserLoginView(TokenObtainPairView):
    authentication_classes = []
    permission_classes = (permissions.AllowAny,)
    throttle_classes = (UserLoginThrottle,)


class UserProfileView(generics.RetrieveAPIView):
    queryset = User.objects.all()
    serializer_class = UserDetailSerializer
    permission_classes = (permissions.IsAuthenticated,)

    def get_object(self):
        return self.request.user
