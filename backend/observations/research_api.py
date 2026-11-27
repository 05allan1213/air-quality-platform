"""Session/CSRF boundary for bounded analytical POST requests."""

from django.db import OperationalError, transaction
from django.urls import path
from django.views.decorators.cache import never_cache
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .analysis_query import build_relation, detail_rows
from .policy import Denied
from .research_access import Access, ResearchContext
from .research_spec import object_with_keys, parse_spec, positive_int
from .s04_http import decode_cursor, encode_cursor, instant

PARENT = {"selection", "method", "method_version", "grain"}
SUPPORT = {"input_version", "support", "page_size", "cursor", "bucket"}
STATUS = {
    "use_denied": 403,
    "permission_denied": 403,
    "not_found": 404,
    "incompatible_series": 422,
    "method_changed": 409,
    "input_changed": 409,
    "expired": 410,
    "database_unavailable": 503,
}
JSON_LIMIT = 1024 * 1024


def bounded(request, data):
    def fits():
        # JSONRenderer honors negotiated media parameters such as indent=4.
        body = request.accepted_renderer.render(data, request.accepted_media_type)
        return len(body) <= JSON_LIMIT

    if fits():
        return Response(data)
    # Saved runs wrap the same report. Charge its complete envelope, attribution,
    # columns and chart against the same budget before fitting the sorted prefix.
    pairs = data.get("result", data).get("pearson_pairs")
    if not pairs or not pairs["points"]:
        raise Denied("query_too_large")
    points = pairs["points"]
    pairs.update(points=[], shown=0, truncated=True)
    if not fits():
        raise Denied("query_too_large")
    low, high = 0, len(points) - 1
    while low < high:
        shown = (low + high + 1) // 2
        pairs.update(points=points[:shown], shown=shown)
        if fits():
            low = shown
        else:
            high = shown - 1
    pairs.update(points=points[:low], shown=low)
    return Response(data)


def drilldown(rel, data, user):
    spec = rel.spec
    if "input_version" not in data and (spec.method != "detail" or "cursor" in data):
        raise Denied("invalid_request")
    if data.get("input_version", rel.input_version) != rel.input_version:
        raise Denied("input_changed")
    size = positive_int(data.get("page_size", 50))
    if size > 100:
        raise Denied("query_too_large")
    support = data.get("support", "observed")
    if (
        support not in ("observed", "effective", "paired")
        or spec.method == "detail"
        and support == "paired"
    ):
        raise Denied("invalid_request")
    bucket = data.get("bucket")
    if bucket is not None:
        object_with_keys(bucket, {"start", "end", "local_hour", "pos"}, {"start", "end"})
        if "pos" in bucket and (
            type(bucket["pos"]) is not int
            or not 0 <= bucket["pos"] < len(rel.profiles)
            or support == "paired"
        ):
            raise Denied("invalid_request")
        if "local_hour" in bucket and (
            type(bucket["local_hour"]) is not int or not 0 <= bucket["local_hour"] <= 23
        ):
            raise Denied("invalid_request")
        bucket = bucket | {key: instant(bucket[key]) for key in ("start", "end")}
        if not spec.start <= bucket["start"] < bucket["end"] <= spec.end:
            raise Denied("invalid_request")
    binding = [spec.canonical, rel.input_version, support, bucket, size]
    page = decode_cursor(data.get("cursor"), "research.details", user, binding)
    rows = detail_rows(
        rel, support=support, limit=size + 1, bucket=bucket, after=page["last"] if page else None
    )
    token = None
    if len(rows) > size:
        rows.pop()
        last = rows[-1]
        token = encode_cursor(
            "research.details",
            user,
            binding,
            {"last": [(last["observed_at"] or spec.start).isoformat(), last["pos"], last["key"]]},
        )
    return dict(
        state="ok" if rows else "no_data",
        results=rows,
        page={"next_cursor": token, "page_size": size},
    )


def query_response(request, spec, purpose, data, *, nowait):
    user_id = request.user.pk
    with transaction.atomic():
        rel = build_relation(user_id, spec, purpose=purpose, nowait=nowait)
        if purpose == "details":
            response = drilldown(rel, data, user_id)
        else:
            from .research_statistics import summarize

            response = summarize(rel, series=rel.series)
        result = bounded(
            request,
            spec.canonical
            | dict(
                input_version=rel.input_version,
                source_timezone=rel.product.source_timezone,
                attribution=rel.attributions,
                requested_window={"start": spec.start, "end": spec.end},
                **rel.metadata,
            )
            | response,
        )
        rel.after()
    return result


def research_view(action):
    @never_cache
    @api_view(["POST"])
    def view(request):
        try:
            if request.query_params:
                raise Denied("invalid_request")
            data = object_with_keys(
                request.data, PARENT | (SUPPORT if action == "details" else set())
            )
            # A bounded first page can issue the parent version for the 12,000-cell detail CSV.
            spec = parse_spec(
                {k: v for k, v in data.items() if k in PARENT},
                export=action == "details" and data.get("method") == "detail",
            )
            if (
                action == "compare"
                and spec.method != "compare"
                or action == "statistics"
                and spec.method not in {"pearson", "statistics"}
            ):
                raise Denied("invalid_request")
            purpose = "details" if action == "details" else "analysis"
            try:
                return query_response(request, spec, purpose, data, nowait=True)
            except OperationalError as exc:
                if getattr(exc.__cause__, "sqlstate", None) != "55P03":
                    raise
            # A competing writer owns a dependency. Check rights without waiting on a
            # hidden resource, then recompute everything after the authorized lock wait.
            with transaction.atomic():
                Access(ResearchContext(request.user.pk, spec, locked=True), spec, purpose, ())
            return query_response(request, spec, purpose, data, nowait=False)
        except Denied as exc:
            return Response({"code": exc.code}, status=STATUS.get(exc.code, 400))

    return view


urlpatterns = [
    path(f"analysis/{action}/", research_view(action))
    for action in ("details", "compare", "statistics")
]
