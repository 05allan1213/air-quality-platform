from accounts.api import error
from accounts.limits import limited
from accounts.research_session import request_transaction
from django.views.decorators.cache import never_cache
from observations.policy import Denied
from rest_framework.decorators import api_view
from rest_framework.response import Response

from . import control
from .queries import read_run, read_window
from .services import change_job, redeliver, submit


def reject(exc):
    if exc.code in ("invalid_request", "invalid_target"):
        return error(exc.code, 400)
    if exc.code in ("request_conflict", "window_contract_conflict", "precondition_conflict"):
        return error(exc.code, 409)
    return error(exc.code, 403)


@never_cache
@api_view(["POST"])
def runs(request):
    if limited("collection", request.user.pk):
        return error("rate_limited", 429)
    try:
        with request_transaction():
            run = submit(request.user.pk, request.data, request=request)
        run.refresh_from_db()
    except Denied as exc:
        return reject(exc)
    return Response(
        {"run_id": str(run.pk), "window_id": run.window_id, "dispatch": run.dispatch},
        status=503 if run.status == "pending" and run.dispatch == "unconfirmed" else 202,
    )


@never_cache
@api_view(["GET", "POST"])
def jobs(request, pk):
    if limited("collection", request.user.pk):
        return error("rate_limited", 429)
    try:
        with request_transaction():
            if request.method == "GET":
                return Response(control.job_diagnostic(request, pk))
            data = control.parse(request.data, control.JOB_ACTIONS)
            result = change_job(request.user.pk, pk, data["action"], request=request, data=data)
    except Denied as exc:
        return reject(exc)
    return Response(result)


@never_cache
@api_view(["GET"])
def windows(request, pk):
    try:
        return Response(read_window(request.user.pk, pk, request.query_params))
    except Denied as exc:
        return reject(exc)


@never_cache
@api_view(["GET", "POST"])
def recover(request, run_id):
    if limited("collection", request.user.pk):
        return error("rate_limited", 429)
    try:
        with request_transaction():
            if request.method == "GET":
                return Response(read_run(request, run_id))
            data = control.parse(request.data, {"redeliver"})
            run = redeliver(request.user.pk, run_id, request=request, data=data)
    except Denied as exc:
        return reject(exc)
    return Response({"run_id": str(run.pk), "window_id": run.window_id}, status=202)
