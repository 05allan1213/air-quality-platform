"""Nationwide L snapshots with authorization before anchoring or aggregation."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import pairwise
from zoneinfo import ZoneInfo

from collection.models import WorkWindow
from django.core.exceptions import EmptyResultSet
from django.db import connection, transaction
from django.db.models import Count, F, Max, Window
from django.db.models.functions import DenseRank
from django.urls import path

from .models import City, Product
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
    read_view,
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

    def conditions(self):
        return {
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


def _selection(query):
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
        },
    )
    synthetic = synthetic_scope(values)
    order = values.get("order", "city")
    standard = values.get("standard_series")
    window = values.get("window_definition", HOURLY)
    at = instant(values["at"]) if "at" in values else None
    as_of = instant(values["as_of"]) if "as_of" in values else None
    name, geography = values.get("q"), values.get("geography")
    if (
        order not in ("city", "aqi_desc")
        or standard not in (None, UNKNOWN_STANDARD, *KNOWN)
        or order == "aqi_desc"
        and standard not in KNOWN
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


def _matching_catalog(catalog, selection):
    cities = City.objects.filter(pk__in={segment.entity_id for segment in catalog.segments})
    if selection.name:
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
    eligible_cities = _city_ids(eligible).distinct()
    coverage_complete = not visible.exclude(key__city_id__in=eligible_cities).exists()
    eligible_count = eligible_cities.count()
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


def _rank_rows(rankable, *, cities=None, last=None, size=None):
    # The outer predicates must not change the rows seen by DENSE_RANK.
    queryset = (
        rankable.order_by()
        .annotate(
            position=Window(
                DenseRank(), partition_by=F("key__standard_series"), order_by=F("value").desc()
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
        query += " WHERE aqi < %s OR (aqi = %s AND city > %s)"
        binds = (*binds, last.value, last.value, last.key.city_id)
    if size is not None:
        query += " ORDER BY aqi DESC, city ASC LIMIT %s"
        binds = (*binds, size + 1)
    with connection.cursor() as cursor:
        cursor.execute(query, binds)
        return cursor.fetchall()


def _rank_digest(row):
    return fingerprint([row.pk, row.key.qualification_version, str(row.value), row.key.city_id])


def _page(visible, rankable, selection, after):
    last_city = after["city"] if after else None
    if selection.order == "aqi_desc":
        last = rankable.filter(key__city_id=last_city).first() if after else None
        if after and (last is None or _rank_digest(last) != after.get("rank_digest")):
            raise Denied("page_changed")
        ranked = _rank_rows(rankable, last=last, size=selection.size)
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
    ranked = _rank_rows(rankable, cities=current) if current and rankable is not None else []
    return current, {rid: pos for rid, _, _, pos in ranked}, len(cities) > selection.size


def _roster(ctx, product, selection, anchor, catalog, summary, visible):
    """Use an independently recorded exact L roster only when its inputs can be disclosed."""
    if anchor is None or summary["covered_city_count"] is None or selection.window != HOURLY:
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


def _project_page(ctx, product, anchor, selection, visible, catalog_rows, cities, ranks):
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
        (sibling for sibling in ctx.products.values() if sibling.pk != product.pk),
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


@transaction.atomic
def read_national(user_id, query):
    product, selection, token = _selection(query)
    conditions = selection.conditions()
    after = decode_cursor(token, ROUTE, user_id, conditions)
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
    requested_catalog = _matching_catalog(catalog, selection) if filtered else catalog
    visible = _hour_rows(base, selection) if anchor else _hour_rows(base, selection).none()
    # Bind directory eligibility to the same visible revision. A different source code
    # attached to the city must not make a hidden AQI row participate in name filtering.
    catalog_rows = _hour_rows(requested_catalog, selection).filter(
        pk__in=visible.order_by().values("pk")
    )
    if filtered:
        visible = visible.filter(key__city_id__in=_city_ids(catalog_rows))
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
    if not summary_permitted and selection.order == "aqi_desc":
        raise Denied()

    rankable = None
    summary = {"state": "unavailable"}
    if summary_permitted:
        eligible = _hour_rows(summary_scope, selection).filter(
            pk__in=visible.order_by().values("pk")
        )
        rankable = _rankable(eligible)
        summary = _summary(visible, eligible, rankable, selection)
        _roster(ctx, product, selection, anchor, catalog, summary, visible)
    cities, ranks, more = _page(visible, rankable, selection, after)
    results, last = _project_page(
        ctx, product, anchor, selection, visible, catalog_rows, cities, ranks
    )
    next_cursor = None
    if more:
        tail = {"city": cities[-1], "snapshot_at": anchor.isoformat()}
        if selection.order == "aqi_desc":
            tail["rank_digest"] = _rank_digest(last)
        next_cursor = encode_cursor(ROUTE, user_id, conditions, tail)
    ctx.finish()
    return {
        "selection": conditions | {"source": product.source.namespace},
        "synthetic": product.synthetic,
        "snapshot_at": anchor,
        "evaluated_at": ctx.now,
        "state": "ok" if results else "no_data",
        "summary": summary,
        "results": results,
        "page": {"page_size": selection.size, "next_cursor": next_cursor},
    }


urlpatterns = [path("overview/national/", read_view(read_national))]
