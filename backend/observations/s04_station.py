"""Authorized, product-specific station directory and historical station reads."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from django.db import transaction
from django.db.models import DateTimeField, F, Func, Q
from django.urls import path

from .models import City, SiteEpoch
from .normalization import INDICATORS, REPORT_KIND, WINDOWS
from .policy import Denied, Scope
from .queries import pk
from .s04_http import decode_cursor, encode_cursor, instant, page_size, params, read_view
from .selection import TICK, ReadContext

HOURLY = "hourly_label_unspecified"
HOUR = timedelta(hours=1)
CATALOG = frozenset({"catalog"})


def synthetic_scope(values):
    scope = values.get("scope")
    if scope not in (None, "synthetic"):
        raise Denied("invalid_request")
    return scope == "synthetic"


def observation_link(route, product, **conditions):
    values = {"product": product.pk}
    if product.synthetic:
        values["scope"] = "synthetic"
    values.update(conditions)
    return f"/api/v1/{route}/?{urlencode(values)}"


def history_link(product, entity, window, indicators, start, end, **series):
    field = "site_epochs" if product.level == "station" else "cities"
    return observation_link(
        "observations/query",
        product,
        **{field: entity},
        mode="window",
        report_kind=REPORT_KIND.get(window, "hourly"),
        window_definition=window,
        indicators=",".join(indicators),
        start=start.isoformat(),
        end=end.isoformat(),
        **series,
    )


def epoch_queryset(product):
    return SiteEpoch.objects.select_related("station", "city").filter(
        station__source_id=product.source_id,
        city__source_id=product.source_id,
    )


def station_rows(ctx, product, epoch_ids, indicators, as_of):
    """One batched latest selection per represented window, including rolling O₃."""
    windows = (
        (HOURLY,)
        if product.shape == "H"
        else [name for name, (_, label) in WINDOWS.items() if label != "date"]
    )
    scopes = {}
    rows = []
    for window in windows:
        selected = indicators
        if window.startswith("rolling_"):
            if "O3" not in indicators:
                continue
            selected = ("O3",)
        visible = ctx.scope(product, selected, epoch_ids, end=as_of + TICK, window=window)
        scopes[window] = visible
        if visible.permitted:
            revisions = visible.revisions().filter(key__window_definition=window)
            if product.shape == "H":
                revisions = revisions.filter(key__standard_series="not_applicable")
            rows.extend(ctx.latest(revisions, product))
    rows.sort(key=lambda r: (r.key.indicator, r.key.window_definition, r.key.standard_series))
    return scopes, ctx.project(product, rows)


def _epoch_catalog(ctx, product, epoch_ids, as_of, *, current=False):
    return ctx.scope(
        product,
        INDICATORS[product.shape],
        epoch_ids,
        start=as_of if current else None,
        end=as_of + TICK,
        fields=CATALOG,
    )


def _status(epoch, at):
    if epoch.active_period.lower > at:
        return "upcoming"
    if epoch.active_period.upper is not None and epoch.active_period.upper <= at:
        return "historical"
    return "current"


def _site_projection(product, epoch, at, codes=(), has_catalog=False):
    return {
        "station": epoch.station_id,
        "site_epoch": epoch.pk,
        "city": epoch.city_id if has_catalog else None,
        "station_name": epoch.station.name if has_catalog else None,
        "city_name": epoch.city.name if has_catalog else None,
        "site_name": epoch.name if has_catalog else None,
        "codes": sorted(set(codes)),
        "period_start": epoch.active_period.lower if has_catalog else None,
        "period_end": epoch.active_period.upper if has_catalog else None,
        "status": _status(epoch, at) if has_catalog else "unknown",
        # Map acceptance and location evidence are handled independently of directory disclosure.
        "coordinates": None,
        "link": observation_link(
            f"stations/{epoch.station_id}",
            product,
            site_epoch=epoch.pk,
            as_of=at.isoformat(),
        ),
    }


def _catalog_codes(catalog, *, at=None):
    codes = defaultdict(set)
    for s in catalog.segments:
        if at is None or s.start <= at < s.end:
            codes[s.entity_id].add(s.code)
    return codes


def _catalog_page(ctx, product, candidates, catalog, at, controls, route, conditions):
    codes = _catalog_codes(catalog, at=at if route == "station-directory" else None)
    visible = candidates.filter(pk__in=codes).annotate(
        period_start=Func(F("active_period"), function="LOWER", output_field=DateTimeField()),
    )
    after = controls["after"]
    if after:
        last = visible.filter(pk=after["epoch"]).first()
        if last is None:
            raise Denied("page_changed")
        visible = visible.filter(
            Q(station_id__gt=last.station_id)
            | Q(station_id=last.station_id, period_start__gt=last.period_start)
            | Q(station_id=last.station_id, period_start=last.period_start, pk__gt=last.pk),
        )
    size = controls["size"]
    rows = list(visible.order_by("station_id", "period_start", "pk")[: size + 1])
    cursor = None
    if len(rows) > size:
        cursor = encode_cursor(
            route,
            ctx.user_id,
            conditions,
            {"epoch": rows[size - 1].pk, "at": at.isoformat()},
        )
    return {
        "results": [_site_projection(product, row, at, codes[row.pk], True) for row in rows[:size]],
        "page": {"page_size": size, "next_cursor": cursor},
    }


def _directory_permitted(ctx, product, city, at):
    """A broadly authorized product may provide a genuinely empty directory."""
    day = at.astimezone(ZoneInfo(product.source_timezone)).date()
    return any(
        ctx.basis(
            product, Scope(city.geography, "*", indicator, day, day + timedelta(days=1)), CATALOG
        )
        for indicator in INDICATORS[product.shape]
    )


def directory_visible(ctx, product, city, at):
    """Data-independent authorization for a city-to-directory link."""
    if product.source_id != city.source_id or product.shape not in ("S", "H"):
        return False
    ids = list(
        epoch_queryset(product)
        .filter(city_id=city.pk, active_period__contains=at)
        .values_list("pk", flat=True)
    )
    return _epoch_catalog(ctx, product, ids, at, current=True).permitted or _directory_permitted(
        ctx,
        product,
        city,
        at,
    )


@transaction.atomic
def read_station_directory(user_id, query, city):
    values = params(query, {"product", "scope", "at", "page_size", "cursor"})
    product_id = pk(values.get("product"))
    synthetic = synthetic_scope(values)
    ctx = ReadContext(user_id, [product_id], synthetic)
    product = ctx.products[product_id]
    if product.shape not in ("S", "H"):
        raise Denied()
    city = City.objects.filter(pk=pk(str(city)), source_id=product.source_id).first()
    if city is None:
        raise Denied()
    requested_at = instant(values["at"]) if "at" in values else None
    size = page_size(values)
    conditions = [product_id, synthetic, city.pk, requested_at, size]
    after = decode_cursor(values.get("cursor"), "station-directory", user_id, conditions)
    at = instant(after["at"]) if after else requested_at or ctx.now
    candidates = epoch_queryset(product).filter(city_id=city.pk, active_period__contains=at)
    ids = list(candidates.values_list("pk", flat=True))
    catalog = _epoch_catalog(ctx, product, ids, at, current=True)
    if not catalog.permitted and not _directory_permitted(ctx, product, city, at):
        raise Denied()
    payload = _catalog_page(
        ctx,
        product,
        candidates,
        catalog,
        at,
        {"size": size, "after": after},
        "station-directory",
        conditions,
    )
    ctx.finish()
    return dict(
        product=product.pk,
        city=city.pk,
        source=product.source.namespace,
        synthetic=product.synthetic,
        at=at,
        state="ok" if payload["results"] else "no_data",
        **payload,
    )


@dataclass(frozen=True)
class FixtureStatus:
    """Server-side injection for labelled F scenarios. There is no HTTP input for it."""

    source_status: str
    freshness_limit: timedelta | None = None


def _apply_fixture_status(product, rows, fixture_status):
    if fixture_status is None or not product.synthetic:
        return "unknown"
    for row in rows:
        row["source_status"] = fixture_status.source_status
        if fixture_status.freshness_limit is not None:
            row["freshness"] = (
                "stale"
                if row["age_seconds"] > fixture_status.freshness_limit.total_seconds()
                else "fresh"
            )
    return fixture_status.source_status


def _history_links(product, epoch, scopes, rows, as_of):
    by_window = defaultdict(list)
    for row in rows:
        by_window[row["window_definition"]].append(row)
    links = []
    for window, visible in scopes.items():
        last = max((r["observed_at"] for r in by_window[window]), default=None)
        if last is None and not visible.permitted:
            continue
        end = (last + HOUR) if last else min(as_of + TICK, max(s.end for s in visible.segments))
        start = end - 24 * HOUR
        indicators = sorted(
            {s.indicator for s in visible.segments if s.start < end and start < s.end}
        )
        if indicators:
            links.append(history_link(product, epoch.pk, window, indicators, start, end))
    return links


@transaction.atomic
def read_station(user_id, query, station, *, fixture_status=None):
    values = params(
        query, {"product", "scope", "site_epoch", "as_of", "indicators", "page_size", "cursor"}
    )
    product_id = pk(values.get("product"))
    synthetic = synthetic_scope(values)
    station = pk(str(station))
    ctx = ReadContext(user_id, [product_id], synthetic)
    product = ctx.products[product_id]
    if product.shape not in ("S", "H"):
        raise Denied()
    requested_at = instant(values["as_of"]) if "as_of" in values else None
    if requested_at and requested_at > ctx.now:
        raise Denied("invalid_request")
    as_of = requested_at or ctx.now
    indicators = INDICATORS[product.shape]
    if "indicators" in values:
        selected = set(values["indicators"].split(","))
        if not selected <= set(indicators):
            raise Denied("invalid_request")
        indicators = tuple(i for i in indicators if i in selected)
    candidates = epoch_queryset(product).filter(station_id=station)

    if "site_epoch" not in values:
        if "indicators" in values:
            raise Denied("invalid_request")
        size = page_size(values)
        conditions = [product_id, synthetic, station, requested_at, size]
        after = decode_cursor(values.get("cursor"), "station-epochs", user_id, conditions)
        if after:
            as_of = instant(after["at"])
        ids = list(candidates.values_list("pk", flat=True))
        catalog = _epoch_catalog(ctx, product, ids, as_of)
        if not catalog.permitted:
            raise Denied()
        payload = _catalog_page(
            ctx,
            product,
            candidates,
            catalog,
            as_of,
            {"size": size, "after": after},
            "station-epochs",
            conditions,
        )
        ctx.finish()
        return dict(
            product=product.pk,
            station=station,
            source=product.source.namespace,
            synthetic=product.synthetic,
            as_of=as_of,
            state="select_epoch",
            **payload,
        )

    if {"page_size", "cursor"} & values.keys():
        raise Denied("invalid_request")
    epoch = candidates.filter(pk=pk(values["site_epoch"])).first()
    if epoch is None:
        raise Denied()
    scopes, rows = station_rows(ctx, product, [epoch.pk], indicators, as_of)
    if not any(v.permitted for v in scopes.values()):
        raise Denied()
    # Current editions use only the code active at the requested instant. For a closed
    # epoch, use its last instant and still require that exact scope to allow catalog.
    catalog_at = (
        min(as_of, epoch.active_period.upper - TICK) if epoch.active_period.upper else as_of
    )
    catalog = _epoch_catalog(ctx, product, [epoch.pk], catalog_at, current=True)
    codes = _catalog_codes(catalog, at=catalog_at)[epoch.pk]
    identity = _site_projection(product, epoch, as_of, codes, bool(codes))
    source_status = _apply_fixture_status(product, rows, fixture_status)
    links = _history_links(product, epoch, scopes, rows, as_of)
    ctx.finish()
    return dict(
        product=product.pk,
        station=station,
        site_epoch=epoch.pk,
        identity=identity,
        source=product.source.namespace,
        synthetic=product.synthetic,
        as_of=as_of,
        evaluated_at=ctx.now,
        state="ok" if rows else "no_data",
        source_status=source_status,
        results=rows,
        history=links,
    )


urlpatterns = [
    path("cities/<int:city>/stations/", read_view(read_station_directory)),
    path("stations/<int:station>/", read_view(read_station)),
]
