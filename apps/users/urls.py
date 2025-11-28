from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView
from .views import UserRegistrationView, Usergetrole, UserLoginView, UserProfileView

urlpatterns=[
    path('register/',UserRegistrationView.as_view(),name='user_registration'),
    path('login/',UserLoginView.as_view(),name='token_obtain_pair'),
    path('login/refresh/',TokenRefreshView.as_view(),name='token_refresh'),
    path('profile/',UserProfileView.as_view(),name='user_profile'),
    # path('userowner/',Usergetrole.as_view(),name='user_getrole'),
    path('userowner/',Usergetrole.as_view(),name='user_getdatafaceid'),
]
