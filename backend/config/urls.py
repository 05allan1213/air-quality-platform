from collection import views as collection_views
from django.db import DatabaseError, connection
from django.http import JsonResponse
from django.urls import include, path
from django.views.decorators.http import require_GET
from observations import views as observation_views
from observations.detail import read_detail
from observations.s04_http import read_view
from samples import views as sample_views


@require_GET
def ready(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except DatabaseError:
        return JsonResponse({"web": "ok", "pg": "unavailable"}, status=503)
    return JsonResponse({"web": "ok", "pg": "ok"})


urlpatterns = [
    path("health/ready/", ready),
    path("api/v1/admin/collection-runs/", collection_views.runs),
    path("api/v1/admin/collection-runs/<uuid:run_id>/", collection_views.recover),
    path("api/v1/admin/collection-jobs/<int:pk>/", collection_views.jobs),
    path("api/v1/admin/collection-windows/<int:pk>/", collection_views.windows),
    path("api/v1/admin/history-imports/", observation_views.history_imports),
    path("api/v1/", include("observations.s04_national")),
    path("api/v1/", include("observations.s04_city")),
    path("api/v1/", include("observations.s04_station")),
    path("api/v1/observations/query/", read_view(read_detail)),
    path("api/v1/observations/current/", observation_views.observations),
    path("api/v1/admin/observation-evidence/", observation_views.evidence),
    path("api/v1/auth/", include("accounts.urls")),
    path("api/v1/admin/fixture-runs/", sample_views.fixture_runs),
    path("api/v1/fixtures/observations/", sample_views.observations),
]
