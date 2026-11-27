"""Session-authenticated, non-cacheable quality governance endpoints."""

from uuid import UUID

from accounts.api import CsrfSessionAuthentication
from accounts.management_api import rejection
from django.urls import path
from django.views.decorators.cache import never_cache
from rest_framework.decorators import api_view, authentication_classes
from rest_framework.response import Response

from .catalog_governance import station_change
from .policy import Denied
from .quality_governance import act, list_issues
from .queries import pk
from .s04_http import params


@never_cache
@api_view(["GET"])
def issues(request):
    try:
        values = params(
            request.query_params, {"product", "scope", "run", "window", "status", "before"}
        )
        if "product" not in values or values.get("scope", "formal") not in {"formal", "synthetic"}:
            raise Denied("invalid_request")
        if values.get("status") not in {None, "open", "in_review", "resolved"}:
            raise Denied("invalid_request")
        try:
            run = UUID(values["run"]) if "run" in values else None
        except ValueError:
            raise Denied("invalid_request") from None
        return Response(
            list_issues(
                request,
                {
                    "product": pk(values["product"]),
                    "synthetic": values.get("scope") == "synthetic",
                    "before": pk(values["before"]) if "before" in values else 2**63 - 1,
                    "run": run,
                    "window": pk(values["window"]) if "window" in values else None,
                    "status": values.get("status"),
                },
            )
        )
    except Denied as exc:
        return rejection(exc)


def event(request, event_id, *, write):
    try:
        return Response(act(request, event_id, write=write))
    except Denied as exc:
        return rejection(exc)


@never_cache
@api_view(["GET"])
def detail(request, event_id):
    return event(request, event_id, write=False)


class QualityActionSessionAuthentication(CsrfSessionAuthentication):
    def enforce_csrf(self, request):
        # Django checks the session's CSRF header without invoking the DRF JSON
        # parser via request.POST. act() parses the body after global authorization.
        super().enforce_csrf(request._request)


@never_cache
@api_view(["POST"])
@authentication_classes([QualityActionSessionAuthentication])
def actions(request, event_id):
    return event(request, event_id, write=True)


@never_cache
@api_view(["GET", "POST"])
def stations(request, station_id):
    try:
        if not 0 < station_id < 2**63:
            raise Denied("invalid_request")
        write = request.method == "POST"
        values = params(
            request.query_params,
            set() if write else {"action", "product", "identifier", "at", "target"},
        )
        if write and not isinstance(request.data, dict):
            raise Denied("invalid_request")
        return Response(
            station_change(
                request,
                station_id,
                values=values,
                data=request.data if write else None,
            )
        )
    except Denied as exc:
        return rejection(exc)


urlpatterns = [
    path("admin/quality-issues/", issues),
    path("admin/quality-issues/<int:event_id>/", detail),
    path("admin/quality-issues/<int:event_id>/actions/", actions),
    path("admin/stations/<int:station_id>/changes/", stations),
]
