from accounts.api import error
from accounts.limits import limited
from django.views.decorators.cache import never_cache
from observations.policy import Denied
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .queries import read_window
from .services import change_job, redeliver, submit


def reject(exc):
    if exc.code in ("invalid_request", "invalid_target"):
        return error(exc.code, 400)
    if exc.code in ("request_conflict", "window_contract_conflict"):
        return error(exc.code, 409)
    return error(exc.code, 403)


@never_cache
@api_view(["POST"])
def runs(request):
    if limited("collection", request.user.pk):
        return error("rate_limited", 429)
    try:
        run = submit(request.user.pk, request.data)
        run.refresh_from_db()
    except Denied as exc:
        return reject(exc)
    return Response(
        {"run_id": str(run.pk), "window_id": run.window_id, "dispatch": run.dispatch},
        status=503 if run.status == "pending" and run.dispatch == "unconfirmed" else 202,
    )


@never_cache
@api_view(["POST"])
def jobs(request, pk):
    if limited("collection", request.user.pk):
        return error("rate_limited", 429)
    try:
        if not isinstance(request.data, dict) or set(request.data) != {"action"}:
            raise Denied("invalid_request")
        job = change_job(request.user.pk, pk, request.data["action"])
    except Denied as exc:
        return reject(exc)
    return Response({"job": job.pk, "enabled": job.enabled, "reason": job.pause_reason})


@never_cache
@api_view(["GET"])
def windows(request, pk):
    try:
        return Response(read_window(request.user.pk, pk, request.query_params))
    except Denied as exc:
        return reject(exc)


@never_cache
@api_view(["POST"])
def recover(request, run_id):
    if limited("collection", request.user.pk):
        return error("rate_limited", 429)
    try:
        if request.data != {"action": "redeliver"}:
            raise Denied("invalid_request")
        run = redeliver(request.user.pk, run_id)
    except Denied as exc:
        return reject(exc)
    return Response({"run_id": str(run.pk), "window_id": run.window_id}, status=202)
