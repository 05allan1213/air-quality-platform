from django.urls import path

from . import views
from .profile import change_password

urlpatterns = [
    path("csrf/", views.csrf),
    path("register/", views.register),
    path("login/", views.sign_in),
    path("logout/", views.sign_out),
    path("password/", change_password),
]
