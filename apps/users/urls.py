from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView
from .views import UserRegistrationView, Usergetrole, UserLoginView, UserProfileView

# yg dicomment punya kita, backend mas fahmi dibawahnya (import, url patterns)
# from .views import (
#     BuyerRegistrationView,
#     OwnerRegistrationView,
#     UserLoginView,
#     UserProfileView,
#     UserRegistrationView,
# )

# urlpatterns = [
#     path('register/', UserRegistrationView.as_view(), name='user-register'),
#     path('register/buyer/', BuyerRegistrationView.as_view(), name='user-register-buyer'),
#     path('register/owner/', OwnerRegistrationView.as_view(), name='user-register-owner'),
#     path('login/', UserLoginView.as_view(), name='token-obtain-pair'),
#     path('login/refresh/', TokenRefreshView.as_view(), name='token-refresh'),
#     path('profile/', UserProfileView.as_view(), name='user-profile'),
# ]

urlpatterns=[
    path('register/',UserRegistrationView.as_view(),name='user_registration'),
    path('login/',UserLoginView.as_view(),name='token_obtain_pair'),
    path('login/refresh/',TokenRefreshView.as_view(),name='token_refresh'),
    path('profile/',UserProfileView.as_view(),name='user_profile'),
    path('userowner/',Usergetrole.as_view(),name='user_getrole'),
]
