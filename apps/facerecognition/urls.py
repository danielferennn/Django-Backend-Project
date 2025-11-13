from django.urls import path
from . import views
urlpatterns = [
    path('createimagetrainingusernew/',views.Createimagetrainingusernew.as_view(),name="createimagetrainingusernew"),
    path('getuserimageexists/',views.Getimageexistsuser.as_view(),name="getuserimageexists"),
    path('createlogusersmartnew/',views.Createlogusersmartnew.as_view(),name="createlogusersmartnew"),
    path('getuserlogsmartnew/',views.Getuserlogsmartnews.as_view(),name="createlogusersmartnew")
]