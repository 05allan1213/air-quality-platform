from django.urls import path

from .favorites import favorite, favorites

urlpatterns = [
    path("me/favorites/", favorites),
    path("me/favorites/<int:item_id>/", favorite),
]
