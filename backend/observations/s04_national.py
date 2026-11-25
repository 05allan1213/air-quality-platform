"""Nationwide L snapshots with authorization before anchoring or aggregation."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import pairwise
from zoneinfo import ZoneInfo

from accounts.research_session import SessionCheck
from accounts.session_generation import SESSION_GENERATION
from collection.models import WorkWindow
from django.core.exceptions import EmptyResultSet
from django.db import connection, transaction
from django.db.models import Count, F, Max, Window
from django.db.models.functions import DenseRank
from django.urls import path
from django.views.decorators.cache import never_cache
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .models import City, CityRevision, Product
from .normalization import (
    LEVELS,
    STANDARD_2012,
    STANDARD_2026,
    UNKNOWN_STANDARD,
)
from .policy import Denied, Scope
from .queries import pk
from .s04_city import HOURLY_WINDOWS, _on_source_hour, city_navigation
from .s04_http import (
    decode_cursor,
    encode_cursor,
    fingerprint,
    instant,
    page_size,
    params,
)
from .s04_station import CATALOG, HOUR, HOURLY, synthetic_scope
from .selection import TICK, ReadContext, Visibility

ROUTE = "national-overview"
KNOWN = (STANDARD_2012, STANDARD_2026)
AQI = ("AQI",)


@dataclass(frozen=True)
class Selection:
    product_id: int
    synthetic: bool
    at: datetime | None
    as_of: datetime | None
    standard: str | None
    window: str
    order: str
    name: str | None
    geography: str | None
    size: int
    home: bool = False
    grade: int | None = None
    rank_preview: str = "aqi_desc"
    focus_city: int | None = None
    map_limit: int | None = None

    def conditions(self):
        conditions = {
            "product": self.product_id,
            "scope": "synthetic" if self.synthetic else None,
            "at": self.at,
            "as_of": self.as_of,
            "standard_series": self.standard,
            "window_definition": self.window,
            "order": self.order,
            "q": self.name,
            "geography": self.geography,
            "page_size": self.size,
        }
        if self.home:
            conditions["home"] = 1
        if self.grade is not None:
            conditions["grade"] = self.grade
        return conditions


def _selection(query):
    home_keys = {"rank_preview", "focus_city", "map_limit"}
    values = params(
        query,
        {
            "product",
            "scope",
            "at",
            "as_of",
            "standard_series",
            "window_definition",
            "order",
            "q",
            "geography",
            "page_size",
            "cursor",
            "home",
            "grade",
        }
        | home_keys,
    )
    home = values.get("home") == "1"
    if ("home" in values and not home) or (not home and home_keys.intersection(values)):
        raise Denied("invalid_request")
    synthetic = synthetic_scope(values)
    order = values.get("order", "city")
    standard = values.get("standard_series")
    window = values.get("window_definition", HOURLY)
    at = instant(values["at"]) if "at" in values else None
    as_of = instant(values["as_of"]) if "as_of" in values else None
    name, geography = values.get("q"), values.get("geography")
    grade = pk(values["grade"]) if "grade" in values else None
    rank_preview = values.get("rank_preview", "aqi_desc")
    focus_city = pk(values["focus_city"]) if "focus_city" in values else None
    map_limit = pk(values["map_limit"]) if "map_limit" in values else None
    if (
        order not in ("city", "aqi_desc", "aqi_asc")
        or standard not in (None, UNKNOWN_STANDARD, *KNOWN)
        or order in ("aqi_desc", "aqi_asc")
        and standard not in KNOWN
        or grade is not None
        and (grade not in range(1, 7) or standard not in KNOWN)
        or rank_preview not in ("aqi_desc", "aqi_asc")
        or "rank_preview" in values
        and standard not in KNOWN
        or map_limit is not None
        and map_limit not in range(1, 401)
        or window not in HOURLY_WINDOWS
        or at is not None
        and as_of is not None
        or any(v is not None and not (v.strip() and v.isprintable()) for v in (name, geography))
    ):
        raise Denied("invalid_request")
    size = page_size(values)
    if "product" in values:
        product = Product.objects.filter(
            pk=pk(values["product"]), synthetic=synthetic, shape="L"
        ).first()
    elif synthetic:
        raise Denied("invalid_request")
    else:
        product = Product.objects.filter(
            source__namespace="cnemc", code="city-list-hour", synthetic=False, shape="L"
        ).first()
    if product is None:
        raise Denied()
    return (
        product,
        Selection(
            product.pk,
            synthetic,
            at,
            as_of,
            standard,
            window,
            order,
            name.strip() if name else None,
            geography.strip() if geography else None,
            size,
            home,
            grade,
            rank_preview,
            focus_city,
            map_limit,
        ),
        values.get("cursor"),
    )


def _hour_rows(scope, selection):
    rows = scope.revisions().filter(
        key__report_kind="hourly", key__window_definition=selection.window
    )
    if selection.standard is not None:
        rows = rows.filter(key__standard_series=selection.standard)
    return rows


def _city_ids(rows):
    return rows.order_by().values("key__city_id")


def _city_id_set(rows):
    return set(rows.order_by().values_list("key__city_id", flat=True))


def _empty_domain_permitted(ctx, product, *, end, start=None, summary=False):
    """Authorize an empty national scope independently of any directory entries."""
    zone = ZoneInfo(product.source_timezone)
    day_end = (end - TICK).astimezone(zone).date() + timedelta(days=1)
    if start is not None:
        cuts = [start.astimezone(zone).date(), day_end]
    else:
        days = {day_end}
        for grant in ctx.grants[product.pk]:
            days.update(day for day in (grant.data_from, grant.data_until) if day < day_end)
        cuts = sorted(days)
    # A wildcard scope requires nationwide permission and is vetoed by any
    # intersecting restriction. basis() also schedules the end-of-read recheck.
    return any(
        ctx.basis(product, Scope("*", "*", "AQI", a, b), summary=summary) for a, b in pairwise(cuts)
    )


def _matching_catalog(catalog, selection, product):
    cities = City.objects.filter(
        pk__in={segment.entity_id for segment in catalog.segments}, source_id=product.source_id
    )
    if selection.name:
        if selection.home:
            from .city_search_alias import match_city_names

            cities = match_city_names(cities, product, selection.name, catalog)
        else:
            cities = cities.filter(name__icontains=selection.name)
    if selection.geography:
        cities = cities.filter(geography=selection.geography)
    matched = set(cities.values_list("pk", flat=True))
    return Visibility(catalog.product, [s for s in catalog.segments if s.entity_id in matched])


def _rankable(eligible):
    return eligible.filter(
        key__standard_series__in=KNOWN,
        unit="1",
        provenance__comparable=True,
        provenance__grade__in=range(1, 7),
    )


def _summary(visible, eligible, rankable, selection):
    # eligible is restricted to revision IDs in visible. Equal distinct city
    # counts therefore prove full coverage without a nested anti-join over the
    # authorized intervals. The uncovered city's count stays private.
    eligible_count = _city_ids(eligible).distinct().count()
    coverage_complete = _city_ids(visible).distinct().count() == eligible_count
    groups = {}
    if selection.standard in KNOWN:
        groups[selection.standard] = {"ranked_city_count": 0, "distribution": {}}
    for item in (
        rankable.order_by()
        .values("key__standard_series", "provenance__grade")
        .annotate(city_count=Count("key__city_id", distinct=True))
    ):
        group = groups.setdefault(
            item["key__standard_series"], {"ranked_city_count": 0, "distribution": {}}
        )
        group["distribution"][str(item["provenance__grade"])] = item["city_count"]
        group["ranked_city_count"] += item["city_count"]
    return {
        "state": "ok",
        "covered_city_count": eligible_count if coverage_complete else None,
        "summary_eligible_city_count": eligible_count,
        "groups": groups,
        "expected": None,
        "completeness": "unknown",
    }


def _rank_rows(rankable, *, cities=None, last=None, size=None, direction="aqi_desc"):
    # The outer predicates must not change the rows seen by DENSE_RANK.
    value_order = F("value").asc() if direction == "aqi_asc" else F("value").desc()
    queryset = (
        rankable.order_by()
        .annotate(
            position=Window(
                DenseRank(), partition_by=F("key__standard_series"), order_by=value_order
            )
        )
        .values_list("pk", "key__city_id", "value", "position")
    )
    compiler = queryset.query.get_compiler(connection=connection)
    try:
        sql, binds = compiler.as_sql()
    except EmptyResultSet:
        return []
    query = f"WITH ranked(revision_id, city, aqi, position) AS ({sql}) SELECT * FROM ranked"
    if cities is not None:
        query += " WHERE city = ANY(%s)"
        binds = (*binds, cities)
    elif last is not None:
        comparison = ">" if direction == "aqi_asc" else "<"
        query += f" WHERE aqi {comparison} %s OR (aqi = %s AND city > %s)"
        binds = (*binds, last.value, last.value, last.key.city_id)
    if size is not None:
        order = "ASC" if direction == "aqi_asc" else "DESC"
        query += f" ORDER BY aqi {order}, city ASC LIMIT %s"
        binds = (*binds, size + 1)
    with connection.cursor() as cursor:
        cursor.execute(query, binds)
        return cursor.fetchall()


def _rank_digest(row):
    return fingerprint([row.pk, row.key.qualification_version, str(row.value), row.key.city_id])


def _page(visible, rankable, selection, after):
    last_city = after["city"] if after else None
    if selection.order in ("aqi_desc", "aqi_asc"):
        last = rankable.filter(key__city_id=last_city).first() if after else None
        if after and (last is None or _rank_digest(last) != after.get("rank_digest")):
            raise Denied("page_changed")
        ranked = _rank_rows(rankable, last=last, size=selection.size, direction=selection.order)
        cities = [city for _, city, _, _ in ranked]
        return (
            cities[: selection.size],
            {rid: pos for rid, _, _, pos in ranked[: selection.size]},
            len(cities) > selection.size,
        )
    if after and not visible.filter(key__city_id=last_city).exists():
        raise Denied("page_changed")
    city_rows = visible.filter(key__city_id__gt=last_city) if after else visible
    cities = list(
        city_rows.order_by("key__city_id")
        .values_list("key__city_id", flat=True)
        .distinct()[: selection.size + 1]
    )
    current = cities[: selection.size]
    direction = selection.order if selection.order in ("aqi_desc", "aqi_asc") else "aqi_desc"
    ranked = (
        _rank_rows(rankable, cities=current, direction=direction)
        if current and rankable is not None
        else []
    )
    return current, {rid: pos for rid, _, _, pos in ranked}, len(cities) > selection.size


def _roster(ctx, product, selection, anchor, catalog, summary, visible):
    """Use an independently recorded exact L roster only when its inputs can be disclosed."""
    if (
        anchor is None
        or summary["covered_city_count"] is None
        or selection.window != HOURLY
        or selection.grade is not None
    ):
        return
    windows = list(
        WorkWindow.objects.filter(
            product=product,
            scope_key="national",
            disposed=False,
            target_start=anchor,
            target_end=anchor + HOUR,
            expected__isnull=False,
        )
        .exclude(roster_basis="")
        .values_list("expected", flat=True)[:2]
    )
    if len(windows) != 1 or not isinstance(windows[0], dict):
        return
    allowed = ctx.scope(
        product,
        AQI,
        start=anchor,
        end=anchor + TICK,
        window=selection.window,
        fields=("collection_ledger",),
        summary=True,
    )
    permitted = {(s.entity_id, s.code) for s in allowed.segments} & {
        (s.entity_id, s.code) for s in catalog.segments
    }
    # WorkWindow.expected is a frozen mapping keyed by entity/observed_at/indicator.
    members = set()
    for key, cell in windows[0].items():
        if not (isinstance(cell, list) and len(cell) == 3):
            return
        code, stamp, indicator = cell
        if indicator != "AQI":
            continue
        city, _, _ = key.partition("/")
        if not city.isascii() or not city.isdecimal() or not isinstance(code, str):
            return
        try:
            if instant(stamp) != anchor or (int(city), code) not in permitted:
                return
        except Denied:
            return
        members.add(int(city))
    if not members:
        return
    directory = City.objects.filter(pk__in=members, source_id=product.source_id)
    if selection.name:
        if selection.home:
            from .city_search_alias import match_city_names

            directory = match_city_names(directory, product, selection.name, catalog)
        else:
            directory = directory.filter(name__icontains=selection.name)
    if selection.geography:
        directory = directory.filter(geography=selection.geography)
    roster = directory.values("pk")
    if visible.exclude(key__city_id__in=roster).exists():
        return
    summary["expected"] = roster.count()
    summary["completeness"] = (
        "complete" if summary["expected"] == summary["covered_city_count"] else "partial"
    )


def _project_page(
    ctx, product, anchor, selection, visible, catalog_rows, cities, ranks, *, trend_links=True
):
    if not cities:
        return [], None
    aqi_rows = list(
        visible.filter(key__city_id__in=cities).order_by("key__city_id", "key__standard_series")
    )
    catalog_cities = catalog_rows.filter(key__city_id__in=cities)
    directory = {
        city.pk: {"name": city.name, "geography": city.geography}
        for city in City.objects.filter(pk__in=_city_ids(catalog_cities))
    }
    link_map = city_navigation(
        ctx,
        (
            sibling
            for sibling in ctx.products.values()
            if sibling.pk != product.pk and (trend_links or sibling.shape != "T")
        ),
        City.objects.filter(pk__in=cities).only("source_id"),
        anchor,
    )
    levels = ctx.scope(
        product, LEVELS, cities, start=anchor, end=anchor + TICK, window=selection.window
    )
    grade_rows = (
        _hour_rows(levels, selection)
        .filter(unit="grade")
        .order_by("key__city_id", "key__indicator", "key__standard_series")
    )
    display = {
        city: {
            "city": city,
            "name": None,
            "geography": None,
            "series": [],
            "levels": [],
            "links": dict(link_map[city]),
        }
        for city in cities
    }
    for city, labels in directory.items():
        display[city].update(labels)
    for row in ctx.project(product, aqi_rows):
        if row["revision"] in ranks:
            row["rank"] = ranks[row["revision"]]
        display[row["city"]]["series"].append(row)
    for row in ctx.project(product, grade_rows):
        display[row["city"]]["levels"].append(row)
    last = next((row for row in aqi_rows if row.key.city_id == cities[-1]), None)
    return list(display.values()), last


def _city_labels(product, catalog_rows, cities):
    if not cities:
        return {}
    return {
        city_id: {"name": name, "geography": geography}
        for city_id, name, geography in City.objects.filter(
            pk__in=set(cities) & _city_id_set(catalog_rows), source_id=product.source_id
        ).values_list("pk", "name", "geography")
    }


def _home_digest(ctx, product, anchor, selection, visible, catalog_rows, rankable, summary):
    projected = visible if selection.order == "city" else rankable
    ids = sorted(_city_id_set(projected))
    rows, _ = _project_page(
        ctx, product, anchor, selection, visible, catalog_rows, ids, {}, trend_links=False
    )
    # Exclude independently evaluated source status and T links; they do not
    # change the stable L relation or its page boundaries.
    volatile = {
        "age_seconds",
        "evaluated_at",
        "freshness",
        "source_status",
        "source_event",
        "source_event_at",
        "source_status_until",
        "execution_started_at",
    }
    for row in rows:
        for value in row["series"] + row["levels"]:
            for key in volatile:
                value.pop(key, None)
    # The aggregate also fixes ranks when only some visible rows may be ranked.
    eligible = (
        list(rankable.order_by("pk").values_list("pk", flat=True)) if rankable is not None else []
    )
    return fingerprint({"rows": rows, "eligible": eligible, "summary": summary})


def _rank_preview(ctx, product, catalog_rows, rankable, selection):
    if rankable is None or selection.standard not in KNOWN:
        return {"state": "unavailable", "rows": []}
    series = rankable.filter(key__standard_series=selection.standard)
    ranked = _rank_rows(series, size=5, direction=selection.rank_preview)
    if not ranked:
        return {"state": "ok", "rows": []}
    ranked = ranked[:5]
    revision_ids = [revision for revision, _, _, _ in ranked]
    revisions = {
        row.pk: row
        for row in CityRevision.objects.select_related("key").filter(pk__in=revision_ids)
    }
    cities = [city for _, city, _, _ in ranked]
    labels = _city_labels(product, catalog_rows, cities)
    projected = {row["revision"]: row for row in ctx.project(product, list(revisions.values()))}
    return {
        "state": "ok",
        "rows": [
            {
                "city": city,
                "name": labels.get(city, {}).get("name"),
                "geography": labels.get(city, {}).get("geography"),
                "aqi": projected[revision_id]["value"],
                "grade": projected[revision_id]["grade"],
                "rank": position,
            }
            for revision_id, city, _value, position in ranked
        ],
    }


def _read_national(user_id, query, session_binding=None):
    product, selection, token = _selection(query)
    conditions = selection.conditions()
    route = ROUTE + "-home" if selection.home else ROUTE
    binding = [user_id, session_binding] if selection.home else user_id
    after = decode_cursor(token, route, binding, conditions)
    sibling_ids = Product.objects.filter(
        source_id=product.source_id, synthetic=product.synthetic, shape__in=("C", "T", "S", "H")
    ).values_list("pk", flat=True)
    ctx = ReadContext(user_id, [product.pk, *sibling_ids], product.synthetic)
    product = ctx.products[product.pk]
    if (selection.as_of is not None and selection.as_of > ctx.now) or (
        selection.at is not None and not _on_source_hour(product, selection.at)
    ):
        raise Denied("invalid_request")
    anchor = instant(after["snapshot_at"]) if after else selection.at
    bounds = (
        {"start": anchor, "end": anchor + TICK}
        if anchor
        else {"end": (selection.as_of or ctx.now) + TICK}
    )
    base = ctx.scope(product, AQI, window=selection.window, **bounds)
    if not base.permitted and not _empty_domain_permitted(ctx, product, **bounds):
        raise Denied()
    if anchor is None:
        anchor = _hour_rows(base, selection).aggregate(latest=Max("key__observed_at_utc"))["latest"]
        if anchor is not None:
            bounds = {"start": anchor, "end": anchor + TICK}
            base = ctx.scope(product, AQI, window=selection.window, **bounds)

    catalog = ctx.scope(product, AQI, window=selection.window, fields=CATALOG, **bounds)
    filtered = bool(selection.name or selection.geography)
    requested_catalog = _matching_catalog(catalog, selection, product) if filtered else catalog
    visible = _hour_rows(base, selection) if anchor else _hour_rows(base, selection).none()
    # Bind directory eligibility to the same visible revision. A different source code
    # attached to the city must not make a hidden AQI row participate in name filtering.
    # Inside this locked product snapshot every scope uses the same identity,
    # grant-date cuts and instant. Intersect the *whole* segments (including
    # the code and dates) before asking PostgreSQL for their revision keys.
    base_segments = set(base.segments)
    readable_catalog = Visibility(
        product, [segment for segment in requested_catalog.segments if segment in base_segments]
    )
    catalog_rows = _hour_rows(readable_catalog, selection)
    if anchor is None:
        catalog_rows = catalog_rows.none()
    if filtered:
        visible = visible.filter(key__city_id__in=_city_id_set(catalog_rows))
    # Authorize the filtered directory scope independently of current observations,
    # so a permitted city with no row can still yield an authorized empty ranking.
    ids = {s.entity_id for s in requested_catalog.segments} if filtered else None
    summary_scope = ctx.scope(product, AQI, ids, window=selection.window, summary=True, **bounds)
    if filtered:
        matched = set(requested_catalog.segments)
        summary_scope = Visibility(product, [s for s in summary_scope.segments if s in matched])
    summary_permitted = summary_scope.permitted or _empty_domain_permitted(
        ctx, product, summary=True, **bounds
    )
    if not summary_permitted and (
        selection.order in ("aqi_desc", "aqi_asc") or selection.grade is not None
    ):
        raise Denied()

    rankable = None
    summary = {"state": "unavailable"}
    if summary_permitted:
        # Summary segments require the base observation fields as well as
        # aggregation rights. With a name filter they also carry catalog rights
        # for that same code. Every eligible revision is therefore visible.
        eligible = _hour_rows(summary_scope, selection)
        if anchor is None:
            eligible = eligible.none()
        rankable = _rankable(eligible)
        if selection.grade is not None:
            rankable = rankable.filter(provenance__grade=selection.grade)
            visible = eligible = rankable
        summary = _summary(visible, eligible, rankable, selection)
        _roster(ctx, product, selection, anchor, catalog, summary, visible)
    projection_digest = None
    if selection.home:
        projection_digest = _home_digest(
            ctx, product, anchor, selection, visible, catalog_rows, rankable, summary
        )
        if after and after.get("projection_digest") != projection_digest:
            raise Denied("page_changed")
    cities, ranks, more = _page(visible, rankable, selection, after)
    results, last = _project_page(
        ctx, product, anchor, selection, visible, catalog_rows, cities, ranks
    )
    next_cursor = None
    if more:
        tail = {"city": cities[-1], "snapshot_at": anchor.isoformat()}
        if selection.order in ("aqi_desc", "aqi_asc"):
            tail["rank_digest"] = _rank_digest(last)
        if selection.home:
            tail["projection_digest"] = projection_digest
        next_cursor = encode_cursor(route, binding, conditions, tail)

    ranking = None
    focused = None
    map_projection = None
    if selection.home:
        ranking = (
            _rank_preview(ctx, product, catalog_rows, rankable, selection)
            if summary_permitted
            else {"state": "unavailable", "rows": []}
        )
        if selection.focus_city is not None:
            focusable = visible if selection.order == "city" else rankable
            if focusable.filter(key__city_id=selection.focus_city).exists():
                focus_ranks = {}
                if rankable is not None:
                    direction = "aqi_asc" if selection.order == "aqi_asc" else "aqi_desc"
                    ranked_focus = _rank_rows(
                        rankable,
                        cities=[selection.focus_city],
                        direction=direction,
                    )
                    focus_ranks = {revision: rank for revision, _, _, rank in ranked_focus}
                focused_rows, _ = _project_page(
                    ctx,
                    product,
                    anchor,
                    selection,
                    visible,
                    catalog_rows,
                    [selection.focus_city],
                    focus_ranks,
                )
                focused = focused_rows[0] if focused_rows else None
        if selection.map_limit is not None:
            # The repository has no verified, distributable national geometry yet.
            map_projection = {"state": "unavailable", "items": [], "has_more": False}
    ctx.finish()
    result = {
        "selection": conditions | {"source": product.source.namespace},
        "synthetic": product.synthetic,
        "snapshot_at": anchor,
        "evaluated_at": ctx.now,
        "state": "ok" if results else "no_data",
        "summary": summary,
        "results": results,
        "page": {"page_size": selection.size, "next_cursor": next_cursor},
    }
    if selection.home:
        result["ranking"] = ranking
        result["focused"] = focused
        if map_projection is not None:
            result["map"] = map_projection
    return result, ctx


def read_national(user_id, query, request=None):
    raw_home = (
        query.getlist("home") == ["1"] if hasattr(query, "getlist") else query.get("home") == "1"
    )
    if not raw_home:
        with transaction.atomic():
            result, _ = _read_national(user_id, query)
            return result

    session_binding = None
    if request is not None:
        with transaction.atomic():
            SessionCheck(request)
        session_binding = [request.session.session_key, request.session.get(SESSION_GENERATION, 0)]
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        result, snapshot = _read_national(user_id, query, session_binding)
    # This new READ COMMITTED transaction sees rights and Session changes that
    # happened while the response projection was being assembled.
    with transaction.atomic():
        current = ReadContext(user_id, snapshot.products, result["synthetic"])
        now = SessionCheck(request).now if request is not None else current.now
        if any(current._decide(key, now) != basis for key, basis in snapshot._checks.items()):
            raise Denied()
    return result


@never_cache
@api_view(["GET"])
def national_overview(request):
    try:
        return Response(read_national(request.user.pk, request.query_params, request))
    except Denied as exc:
        status = {
            "not_authenticated": 403,
            "use_denied": 403,
            "permission_denied": 403,
            "page_changed": 409,
        }.get(exc.code, 400)
        return Response({"code": exc.code}, status=status)


urlpatterns = [path("overview/national/", national_overview)]
