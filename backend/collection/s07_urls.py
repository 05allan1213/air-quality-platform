from django.urls import path
from observations.views import history_import

from . import views

urlpatterns = [
    path("admin/collection-jobs/", views.job_list),
    path("admin/collection-windows/", views.window_list),
    path("admin/operations/trends/", views.trends),
    path("admin/history-imports/<uuid:attempt_id>/", history_import),
]
