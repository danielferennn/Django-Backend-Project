from django.urls import path

from . import views

urlpatterns = [
    path('createimagetrainingusernew/', views.Createimagetrainingusernew.as_view(), name='createimagetrainingusernew'),
    path('getuserimageexists/', views.Getimageexistsuser.as_view(), name='getuserimageexists'),
    path('training-images/<int:pk>/', views.FaceTrainingImageView.as_view(), name='face-training-image'),
    path('createlogusersmartnew/', views.Createlogusersmartnew.as_view(), name='createlogusersmartnew'),
    path('getuserlogsmartnew/', views.Getuserlogsmartnews.as_view(), name='getuserlogsmartnew'),
    path('access-logs/<str:log_id>/image/', views.FaceAccessLogImageView.as_view(), name='face-access-log-image'),
]
