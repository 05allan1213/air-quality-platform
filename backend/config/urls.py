from django.db import DatabaseError, connection
from django.http import JsonResponse
from django.urls import include, path
from django.views.decorators.http import require_GET
from observations import views as observation_views
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
    path("api/v1/admin/history-imports/", observation_views.history_imports),
    path("api/v1/observations/current/", observation_views.observations),
    path("api/v1/admin/observation-evidence/", observation_views.evidence),
    path("api/v1/auth/", include("accounts.urls")),
    path("api/v1/admin/fixture-runs/", sample_views.fixture_runs),
    path("api/v1/fixtures/observations/", sample_views.observations),
]
