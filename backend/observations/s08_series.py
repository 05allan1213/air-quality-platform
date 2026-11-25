"""Purpose-scoped selection of series for the S08 research workbench."""

from datetime import timedelta
from types import SimpleNamespace

from django.urls import path
from django.views.decorators.cache import never_cache
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .normalization import CONCENTRATIONS
from .policy import Denied
from .queries import pk
from .s04_http import instant, params

METHODS = {"statistics", "compare", "pearson"}
KEYS = {
    "purpose",
    "method",
    "start",
    "end",
    "scope",
    "product",
    "city",
    "site_epoch",
    "indicator",
    "page_size",
    "cursor",
}


def selection(query):
    values = params(query, KEYS)
    if (
        values.get("purpose") != "analysis"
        or values.get("method") not in METHODS
        or not {"start", "end"} <= values.keys()
        or values.get("scope", "formal") not in {"formal", "synthetic"}
        or {"city", "site_epoch"} <= values.keys()
        or ("indicator" in values and values["indicator"] not in (*CONCENTRATIONS, "AQI"))
    ):
        raise Denied("invalid_request")
    start, end = instant(values["start"]), instant(values["end"])
    size = pk(values.get("page_size", "25"))
    if size > 50:
        raise Denied("query_too_large")
    if not start < end <= start + timedelta(days=30):
        raise Denied("invalid_request")
    object_kind = next((key for key in ("city", "site_epoch") if key in values), None)
    return SimpleNamespace(
        start=start,
        end=end,
        method=values["method"],
        synthetic=values.get("scope") == "synthetic",
        product=pk(values["product"]) if "product" in values else None,
        object_kind=object_kind,
        object_id=pk(values[object_kind]) if object_kind else None,
        indicator=values.get("indicator"),
        size=size,
        cursor=values.get("cursor"),
    )


@never_cache
@api_view(["GET"])
def series(request):
    from .s08_series_selection import read_series

    try:
        selected = selection(request.query_params)
    except Denied as exc:
        return Response({"code": exc.code}, status=400)
    return read_series(request, selected)


urlpatterns = [path("catalog/series/", series)]
