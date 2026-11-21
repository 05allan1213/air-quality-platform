from accounts.api import error
from accounts.limits import limited
from django.views.decorators.cache import never_cache
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .models import FixtureRun
from .normalization import Rejected
from .queries import query_observation
from .services import submit


def rejection(exc):
    if exc.code == "permission_denied":
        return error(exc.code, 403)
    if exc.code.endswith("_denied"):
        return error("use_denied", 403)
    return error(exc.code, 400)


@api_view(["POST"])
def fixture_runs(request):
    if limited("fixture", request.user.pk):
        return error("rate_limited", 429)
    try:
        run_id, dispatch = submit(request.user.pk, request.data)
    except Rejected as exc:
        return rejection(exc)
    if dispatch == FixtureRun.Dispatch.UNCONFIRMED:
        return Response({"code": "dispatch_unconfirmed", "run_id": run_id}, status=503)
    return Response({"run_id": run_id}, status=202)


@never_cache
@api_view(["GET"])
def observations(request):
    try:
        return Response(query_observation(request.query_params))
    except Rejected as exc:
        return rejection(exc)
