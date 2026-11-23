from django.views.decorators.cache import never_cache
from observations.policy import Denied
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .api import error
from .governance import account_action


def rejection(exc):
    status = {
        "request_conflict": 409,
        "precondition_conflict": 409,
        "input_changed": 409,
        "invalid_request": 400,
        "not_found": 404,
    }.get(exc.code, 403)
    return error(exc.code, status)


@never_cache
@api_view(["GET", "POST"])
def accounts(request, pk):
    if request.query_params or pk >= 2**63:
        return error("invalid_request", 400)
    try:
        data = request.data if request.method == "POST" else None
        if request.method == "POST" and not isinstance(data, dict):
            return error("invalid_request", 400)
        return Response(account_action(request, pk, data))
    except Denied as exc:
        return rejection(exc)
