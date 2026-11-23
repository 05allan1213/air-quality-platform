from accounts.api import error
from accounts.limits import limited
from django.views.decorators.cache import never_cache
from rest_framework.decorators import api_view
from rest_framework.response import Response

from . import history_status
from .history import submit
from .policy import Denied
from .queries import read_current, read_evidence


def reject(exc):
    return error(
        exc.code,
        {
            "permission_denied": 403,
            "use_denied": 403,
            "not_authenticated": 403,
            "database_unavailable": 503,
        }.get(exc.code, 400),
    )


@never_cache
@api_view(["GET", "POST"])
def history_imports(request):
    if limited("history", request.user.pk):
        return error("rate_limited", 429)
    try:
        if request.method == "GET":
            return Response(history_status.read(request))
        attempt, dispatch = submit(request.user.pk, request.data, request=request)
    except Denied as exc:
        return reject(exc)
    if dispatch == "unconfirmed":
        return Response({"code": "dispatch_unconfirmed", "attempt_id": attempt}, status=503)
    return Response({"attempt_id": attempt}, status=202)


@never_cache
@api_view(["GET"])
def history_import(request, attempt_id):
    if limited("history", request.user.pk):
        return error("rate_limited", 429)
    try:
        return Response(history_status.read(request, attempt_id))
    except Denied as exc:
        return reject(exc)


@never_cache
@api_view(["GET"])
def observations(request):
    try:
        return Response(read_current(request.user.pk, request.query_params))
    except Denied as exc:
        return reject(exc)


@never_cache
@api_view(["GET"])
def evidence(request):
    try:
        return Response(read_evidence(request.user.pk, request.query_params))
    except Denied as exc:
        return reject(exc)
