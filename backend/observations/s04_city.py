"""One-stamp C panels and independent 24-hour T panels."""

from collections import defaultdict
from zoneinfo import ZoneInfo

from django.db import transaction
from django.db.models import Q
from django.urls import path

from .models import City, Product
from .normalization import INDICATORS, STANDARD_2012, STANDARD_2026, UNKNOWN_STANDARD, WINDOWS
from .policy import Denied
from .queries import pk
from .s04_http import instant, params, read_view
from .s04_station import (
    CATALOG,
    HOUR,
    epoch_queryset,
    history_link,
    observation_link,
    synthetic_scope,
)
from .selection import TICK, ReadContext

HOURLY_WINDOWS = tuple(name for name in WINDOWS if name.startswith("hourly_"))
STANDARDS = {STANDARD_2012, STANDARD_2026, UNKNOWN_STANDARD}


def _standard(values):
    standard = values.get("standard_series")
    if standard and standard not in STANDARDS:
        raise Denied("invalid_request")
    return standard


def lock_city_products(user_id, product_id, synthetic, shape, destinations=()):
    anchor = Product.objects.filter(pk=product_id, synthetic=synthetic, shape=shape)
    ids = Product.objects.filter(
        Q(pk__in=anchor) | Q(source_id__in=anchor.values("source_id"), shape__in=destinations),
        synthetic=synthetic,
    ).values_list("pk", flat=True)
    ctx = ReadContext(user_id, list(ids), synthetic)
    if product_id not in ctx.products:
        raise Denied()
    return ctx, ctx.products[product_id]


def _city(product, city_id):
    city = City.objects.filter(pk=pk(str(city_id)), source_id=product.source_id).first()
    if city is None:
        raise Denied()
    return city


def _on_source_hour(product, stamp):
    local = stamp.astimezone(ZoneInfo(product.source_timezone))
    return not (local.minute or local.second or local.microsecond)


def city_navigation(ctx, products, cities, at):
    """Resolve destinations in batches from their own permissions at the propagated instant."""
    links = defaultdict(lambda: defaultdict(list))
    cities = list(cities)
    if not cities:
        return links
    stamp = at or ctx.now
    for destination in products:
        ids = [city.pk for city in cities if city.source_id == destination.source_id]
        if not ids:
            continue
        indicators = INDICATORS[destination.shape]
        if destination.shape in {"C", "T"}:
            if at is not None and not _on_source_hour(destination, at):
                continue
            trend = destination.shape == "T"
            start = stamp - 23 * HOUR if trend and at else stamp
            end = stamp + HOUR if trend and at else stamp + TICK
            visible = set()
            for window in HOURLY_WINDOWS:
                scope = ctx.scope(destination, indicators, ids, start=start, end=end, window=window)
                visible.update(s.entity_id for s in scope.segments)
            for city in sorted(visible):
                route = f"cities/{city}" + ("/trend" if trend else "")
                selection = {"anchor" if trend else "at": at.isoformat()} if at else {}
                links[city]["trend" if trend else "city"].append(
                    {
                        "product": destination.pk,
                        "href": observation_link(route, destination, **selection),
                    }
                )
        elif destination.shape in {"S", "H"}:
            epochs = dict(
                epoch_queryset(destination)
                .filter(city_id__in=ids, active_period__contains=stamp)
                .values_list("pk", "city_id")
            )
            scope = ctx.scope(
                destination, indicators, epochs, start=stamp, end=stamp + TICK, fields=CATALOG
            )
            for city in sorted({epochs[s.entity_id] for s in scope.segments}):
                links[city]["stations"].append(
                    {
                        "product": destination.pk,
                        "href": observation_link(
                            f"cities/{city}/stations", destination, at=stamp.isoformat()
                        ),
                    }
                )
    return links


def hourly_rows(ctx, product, cities, *, start=None, end=None, standard=None):
    """Yield each eligible hourly window and its authorized SQL relation."""
    for window in HOURLY_WINDOWS:
        scope = ctx.scope(
            product, INDICATORS[product.shape], cities, start=start, end=end, window=window
        )
        if not scope.permitted:
            continue
        rows = scope.revisions().filter(key__window_definition=window, key__report_kind="hourly")
        if standard:
            rows = rows.filter(
                Q(key__indicator="AQI", key__standard_series=standard)
                | (~Q(key__indicator="AQI") & Q(key__standard_series="not_applicable")),
            )
        yield window, scope, rows


def _anchor(scoped):
    instants = (
        rows.order_by("-key__observed_at_utc")
        .values_list("key__observed_at_utc", flat=True)
        .first()
        for _, _, rows in scoped
    )
    return max((at for at in instants if at is not None), default=None)


def _history(product, city, scoped, at, standard):
    if at is None:
        return []
    links = []
    start, end = at - 23 * HOUR, at + HOUR
    for window, scope, _ in scoped:
        indicators = [
            i
            for i in INDICATORS[product.shape]
            if any(s.indicator == i and s.start < end and start < s.end for s in scope.segments)
        ]
        if standard and "AQI" in indicators:
            links.append(
                history_link(
                    product, city.pk, window, ["AQI"], start, end, standard_series=standard
                )
            )
            indicators.remove("AQI")
        if indicators:
            links.append(history_link(product, city.pk, window, indicators, start, end))
    return links


@transaction.atomic
def read_city(user_id, query, city):
    values = params(query, {"product", "scope", "at", "as_of", "standard_series"})
    if "at" in values and "as_of" in values:
        raise Denied("invalid_request")
    standard = _standard(values)
    at = instant(values["at"]) if "at" in values else None
    as_of = instant(values["as_of"]) if "as_of" in values else None
    ctx, product = lock_city_products(
        user_id, pk(values.get("product")), synthetic_scope(values), "C", ("T", "S", "H")
    )
    city = _city(product, city)
    if (as_of is not None and as_of > ctx.now) or (
        at is not None and not _on_source_hour(product, at)
    ):
        raise Denied("invalid_request")
    scoped = list(
        hourly_rows(
            ctx,
            product,
            [city.pk],
            start=at,
            end=(at or as_of or ctx.now) + TICK,
            standard=standard,
        )
    )
    if not scoped:
        raise Denied()
    at = at or _anchor(scoped)
    selected = defaultdict(list)
    records = []
    if at:
        for _, _, rows in scoped:
            records.extend(
                rows.filter(key__observed_at_utc=at).order_by(
                    "key__indicator", "key__standard_series"
                )
            )
        for row in ctx.project(product, records):
            selected[row["indicator"]].append(row)
    catalog_at = at or as_of or ctx.now
    catalog = ctx.scope(
        product, INDICATORS["C"], [city.pk], start=catalog_at, end=catalog_at + TICK, fields=CATALOG
    )
    siblings = [
        destination for destination in ctx.products.values() if destination.pk != product.pk
    ]
    links = dict(city_navigation(ctx, siblings, [city], at or as_of)[city.pk])
    links["history"] = _history(product, city, scoped, at, standard)
    slots = [
        {
            "indicator": indicator,
            "state": "ok" if indicator in selected else "unavailable",
            "results": selected.get(indicator, []),
        }
        for indicator in INDICATORS["C"]
    ]
    ctx.finish()
    return dict(
        product=product.pk,
        city=city.pk,
        city_name=city.name if catalog.permitted else None,
        source=product.source.namespace,
        synthetic=product.synthetic,
        snapshot_at=at,
        evaluated_at=ctx.now,
        standard_series=standard,
        state="ok" if records else "no_data",
        slots=slots,
        links=links,
    )


@transaction.atomic
def read_city_trend(user_id, query, city):
    values = params(query, {"product", "scope", "anchor", "standard_series"})
    standard = _standard(values)
    exact = instant(values["anchor"]) if "anchor" in values else None
    ctx, product = lock_city_products(
        user_id, pk(values.get("product")), synthetic_scope(values), "T"
    )
    city = _city(product, city)
    if exact is not None and not _on_source_hour(product, exact):
        raise Denied("invalid_request")
    if exact is None:
        candidate = list(
            hourly_rows(ctx, product, [city.pk], end=ctx.now + TICK, standard=standard)
        )
        if not candidate:
            raise Denied()
        anchor = _anchor(candidate)
    else:
        anchor = exact
    points = []
    start = end = None
    if anchor is not None:
        start, end = anchor - 23 * HOUR, anchor + HOUR
        scoped = list(hourly_rows(ctx, product, [city.pk], start=start, end=end, standard=standard))
        if not scoped:
            raise Denied()
        records = []
        for _, _, rows in scoped:
            records.extend(
                rows.filter(key__observed_at_utc__lte=anchor).order_by(
                    "key__observed_at_utc", "key__standard_series"
                )
            )
        grouped = defaultdict(list)
        for row in ctx.project(product, records):
            grouped[row["observed_at"]].append(row)
        for i in range(24):
            at = start + i * HOUR
            visible = grouped.get(at, [])
            points.append(
                {"observed_at": at, "state": "ok" if visible else "no_data", "results": visible}
            )
    ctx.finish()
    return dict(
        product=product.pk,
        city=city.pk,
        source=product.source.namespace,
        synthetic=product.synthetic,
        evaluated_at=ctx.now,
        standard_series=standard,
        anchor=anchor,
        anchor_basis="explicit" if exact else "latest_visible" if anchor else "unknown",
        start=start,
        end=end,
        state="ok" if any(p["results"] for p in points) else "no_data",
        points=points,
    )


urlpatterns = [
    path("cities/<int:city>/", read_view(read_city)),
    path("cities/<int:city>/trend/", read_view(read_city_trend)),
]
