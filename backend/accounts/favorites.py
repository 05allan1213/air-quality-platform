"""Private favorite intents and live summaries from the authorized selection service."""

from collections import defaultdict
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.views.decorators.cache import never_cache
from observations.models import City, Product
from observations.normalization import INDICATORS, LEVELS, WINDOWS
from observations.policy import Denied, actor
from observations.queries import pk
from observations.s04_city import hourly_rows
from observations.s04_http import decode_cursor, encode_cursor, instant, page_size, params
from observations.s04_station import (
    epoch_queryset,
    history_link,
    observation_link,
    station_rows,
    synthetic_scope,
)
from observations.selection import TICK, ReadContext
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .api import error
from .models import Favorite

TARGET = {Favorite.Kind.CITY: "city_id", Favorite.Kind.STATION: "station_id"}
SHAPES = {Favorite.Kind.CITY: {"C", "L", "T"}, Favorite.Kind.STATION: {"S", "H"}}
ROUTE = "favorites"


def owner_items(user_id, synthetic):
    return Favorite.objects.filter(owner_id=user_id, synthetic_scope=synthetic)


def owned(query, item_id, *, lock=False):
    item = (query.select_for_update() if lock else query).filter(pk=item_id).first()
    if item is None:
        raise Denied("not_found")
    return item


def target_id(item):
    return getattr(item, TARGET[item.kind])


def require_product(ctx, product_id, kind):
    product = ctx.products[product_id]
    if product.shape not in SHAPES[kind]:
        raise Denied()
    return product


def peek(item):
    return {"id": item.pk, "kind": item.kind, "state": "unavailable"}


def rolling_city_ids(ctx, product, city_ids):
    """Use approved rolling ozone readings as identity evidence for C favorites."""
    observed = set()
    for window in WINDOWS:
        if window.startswith("rolling_") and city_ids - observed:
            scope = ctx.scope(
                product, ("O3",), city_ids - observed, end=ctx.now + TICK, window=window
            )
            observed.update(
                scope.revisions()
                .filter(key__window_definition=window)
                .order_by()
                .values_list("key__city_id", flat=True)
                .distinct()
            )
    return observed


def city_summaries(ctx, product, cities):
    indicators = INDICATORS[product.shape]
    latest_candidates = []
    scope_rows = list(hourly_rows(ctx, product, cities, end=ctx.now + TICK))
    for _, _, revisions in scope_rows:
        latest_candidates.extend(ctx.latest(revisions, product))
    observed = {revision.key.city_id for revision in latest_candidates}
    if product.shape == "L":
        latest_candidates = [r for r in latest_candidates if r.key.indicator == "AQI"]
    anchors = {}
    for revision in latest_candidates:
        key = revision.key
        anchors[key.city_id] = max(
            key.observed_at_utc, anchors.get(key.city_id, key.observed_at_utc)
        )
    selected = [r for r in latest_candidates if r.key.observed_at_utc == anchors[r.key.city_id]]
    if product.shape == "L":
        # Each L row represents the chosen L observation window, like a national snapshot.
        windows = {}
        for revision in selected:
            key = revision.key
            windows.setdefault(key.city_id, key.window_definition)
        selected = [r for r in selected if r.key.window_definition == windows[r.key.city_id]]
        for window, _, revisions in scope_rows:
            same_snapshot = Q()
            for city, at in anchors.items():
                if windows[city] == window:
                    same_snapshot |= Q(key__city_id=city, key__observed_at_utc=at)
            if same_snapshot:
                selected.extend(
                    revisions.filter(same_snapshot, key__indicator__in=LEVELS, unit="grade")
                )
    selected.sort(
        key=lambda r: (
            r.key.city_id,
            r.key.indicator,
            r.key.window_definition,
            r.key.standard_series,
            r.pk,
        )
    )
    rows = defaultdict(list)
    for row in ctx.project(product, selected):
        rows[row["city"]].append(row)
    catalog = set()
    catalog_scope = ctx.scope(product, indicators, cities, end=ctx.now + TICK, fields={"catalog"})
    for segment in catalog_scope.segments:
        if segment.start <= anchors.get(segment.entity_id, ctx.now) < segment.end:
            catalog.add(segment.entity_id)
    if product.shape == "C":
        observed.update(rolling_city_ids(ctx, product, cities - observed - catalog))
    visible_cities = observed | catalog
    if not visible_cities:
        return {}
    names = dict(
        City.objects.filter(pk__in=catalog, source_id=product.source_id).values_list("pk", "name")
    )
    output = {}
    for city in visible_cities:
        at = anchors.get(city)
        if product.shape == "C":
            link = observation_link(
                f"cities/{city}", product, **({"at": at.isoformat()} if at else {})
            )
        elif product.shape == "T":
            link = observation_link(
                f"cities/{city}/trend", product, **({"anchor": at.isoformat()} if at else {})
            )
        else:
            # A national snapshot alone cannot target this city's stable identity.
            first = rows[city][0] if rows[city] else None
            link = (
                history_link(
                    product, city, first["window_definition"], ("AQI",), at, at + timedelta(hours=1)
                )
                if first
                else None
            )
        output[city] = dict(
            product=product.pk,
            target_id=city,
            source=product.source.namespace,
            name=names.get(city),
            results=rows[city],
            snapshot_at=at,
            state="ok" if rows[city] else "no_data",
            link=link,
        )
    return output


def station_summaries(ctx, product, stations):
    epochs = list(
        epoch_queryset(product).filter(
            station_id__in=stations, active_period__startswith__lte=ctx.now
        )
    )
    if not epochs:
        return {}
    ids = [epoch.pk for epoch in epochs]
    _, row_data = station_rows(ctx, product, ids, INDICATORS[product.shape], ctx.now)
    rows = defaultdict(list)
    for row in row_data:
        rows[row["site_epoch"]].append(row)
    # Current epochs are checked at the request instant. Closed epochs use their last instant.
    catalog = ctx.scope(
        product, INDICATORS[product.shape], ids, end=ctx.now + TICK, fields={"catalog"}
    )
    by_epoch = defaultdict(list)
    for segment in catalog.segments:
        by_epoch[segment.entity_id].append(segment)
    visible_sites = defaultdict(list)
    active = {}
    for epoch in sorted(epochs, key=lambda item: item.pk):
        upper = epoch.active_period.upper
        is_current = upper is None or ctx.now < upper
        identity_at = ctx.now if is_current else upper - TICK
        directory = any(s.start <= identity_at < s.end for s in by_epoch[epoch.pk])
        if is_current and directory:
            active[epoch.station_id] = epoch.pk
        if not directory and not rows[epoch.pk]:
            continue
        visible_sites[epoch.station_id].append(
            dict(
                site_epoch=epoch.pk,
                name=epoch.station.name if directory else None,
                site_name=epoch.name if directory else None,
                period_start=epoch.active_period.lower if directory else None,
                period_end=upper if directory else None,
                status=("current" if is_current else "historical") if directory else "unknown",
                link=observation_link(
                    f"stations/{epoch.station_id}",
                    product,
                    site_epoch=epoch.pk,
                    as_of=ctx.now.isoformat(),
                ),
            )
        )
    output = {}
    for station, sites in visible_sites.items():
        selected = next((site for site in sites if site["site_epoch"] == active.get(station)), None)
        # Old editions remain explicit choices. They never fill the current edition's cards.
        status = (
            "select_epoch"
            if selected is None
            else "ok"
            if rows[selected["site_epoch"]]
            else "no_data"
        )
        # Only a disclosed current or sole historical epoch can be a default destination.
        destination = selected or (
            sites[0] if len(sites) == 1 and sites[0]["status"] == "historical" else None
        )
        output[station] = dict(
            product=product.pk,
            target_id=station,
            source=product.source.namespace,
            name=selected["name"]
            if selected
            else next((s["name"] for s in reversed(sites) if s["name"]), None),
            site_epoch=selected["site_epoch"] if selected else None,
            epochs=sites,
            results=rows[selected["site_epoch"]] if selected else [],
            state=status,
            link=destination["link"] if destination else None,
        )
    return output


def project_batch(ctx, favorite_items):
    groups = defaultdict(set)
    for item in favorite_items:
        if target_id(item) and item.preferred_product_id in ctx.products:
            product = ctx.products[item.preferred_product_id]
            if product.shape in SHAPES[item.kind]:
                groups[product.pk].add(target_id(item))

    summaries = {}
    for product_id, targets in groups.items():
        product = ctx.products[product_id]
        read = station_summaries if product.level == "station" else city_summaries
        summaries[product_id] = read(ctx, product, targets)
    return [
        {**peek(item), **summaries.get(item.preferred_product_id, {}).get(target_id(item), {})}
        for item in favorite_items
    ]


def read_context(user_id, items, synthetic):
    ids = {item.preferred_product_id for item in items} - {None}
    # Removal of an optional product while the page is loading leaves a private placeholder.
    present = Product.objects.filter(pk__in=ids, synthetic=synthetic).values_list("pk", flat=True)
    return ReadContext(user_id, list(present), synthetic)


def read_summaries(user_id, items, synthetic):
    try:
        ctx = read_context(user_id, items, synthetic)
        results = project_batch(ctx, items)
        ctx.finish()
        return results
    except Denied as exc:
        if exc.code != "use_denied":
            raise
        return [peek(item) for item in items]


@transaction.atomic
def list_items(user_id, query):
    controls = params(query, {"scope", "cursor", "page_size"})
    synthetic = synthetic_scope(controls)
    size = page_size(controls)
    conditions = [synthetic, size]
    after = decode_cursor(controls.get("cursor"), ROUTE, user_id, conditions)
    user_items = owner_items(user_id, synthetic)
    if after:
        created, last_id = instant(after["created"]), after["id"]
        if not user_items.filter(pk=last_id, created_at=created).exists():
            raise Denied("page_changed")
        user_items = user_items.filter(
            Q(created_at__lt=created) | Q(created_at=created, pk__lt=last_id)
        )
    window = list(user_items.order_by("-created_at", "-pk")[: size + 1])
    items = window[:size]
    next_cursor = None
    if len(window) > size:
        last = items[-1]
        next_cursor = encode_cursor(
            ROUTE, user_id, conditions, {"created": last.created_at.isoformat(), "id": last.pk}
        )
    results = read_summaries(user_id, items, synthetic)
    return {"results": results, "page": {"page_size": size, "next_cursor": next_cursor}}


@transaction.atomic
def get_item(user_id, item_id, synthetic):
    actor(user_id)
    item = owned(owner_items(user_id, synthetic), item_id)
    return read_summaries(user_id, [item], synthetic)[0]


def authorize(ctx, item):
    result = project_batch(ctx, [item])[0]
    if result["state"] == "unavailable":
        raise Denied()
    return result


@transaction.atomic
def create_item(user_id, synthetic, kind, target, product_id):
    items = owner_items(user_id, synthetic).filter(**{TARGET[kind]: target})
    preferred = items.values_list("preferred_product_id", flat=True).first()
    products = {preferred, product_id} - {None}
    ctx = ReadContext(user_id, products, synthetic)
    require_product(ctx, product_id, kind)
    intent = Favorite(
        owner_id=user_id,
        kind=kind,
        synthetic_scope=synthetic,
        preferred_product_id=product_id,
        **{TARGET[kind]: target},
    )
    result = authorize(ctx, intent)
    try:
        with transaction.atomic():
            intent.save(force_insert=True)
    except IntegrityError as exc:
        constraint = getattr(getattr(exc.__cause__, "diag", None), "constraint_name", None)
        if constraint not in {"favorite_owner_city", "favorite_owner_station"}:
            raise
        previous = items.select_for_update().first()
        if previous is None:
            raise Denied("favorite_exists") from None
        if previous.preferred_product_id != product_id:
            raise Denied("favorite_exists") from None
        ctx.finish()
        return {**result, "id": previous.pk}, 200
    ctx.finish()
    return {**result, "id": intent.pk}, 201


@transaction.atomic
def change_preference(user_id, item_id, synthetic, product_id):
    actor(user_id)
    items = owner_items(user_id, synthetic)
    previous = owned(items, item_id)
    products = {previous.preferred_product_id, product_id} - {None}
    ctx = ReadContext(user_id, products, synthetic)
    item = owned(items, item_id, lock=True)
    if item.preferred_product_id not in products and item.preferred_product_id is not None:
        raise Denied("page_changed")
    if target_id(item) is None:
        raise Denied()
    require_product(ctx, product_id, item.kind)
    item.preferred_product_id = product_id
    result = authorize(ctx, item)
    item.save(update_fields=["preferred_product"])
    ctx.finish()
    return result


@transaction.atomic
def delete_item(user_id, item_id, synthetic):
    actor(user_id)
    owned(owner_items(user_id, synthetic), item_id, lock=True).delete()


def body_id(data):
    if type(data) is int and 0 < data < 2**63:
        return data
    if isinstance(data, str) and len(data) <= 19:
        return pk(data)
    raise Denied("invalid_request")


def reject(exc):
    return error(
        exc.code,
        {
            "use_denied": 403,
            "permission_denied": 403,
            "not_found": 404,
            "favorite_exists": 409,
            "page_changed": 409,
        }.get(exc.code, 400),
    )


@never_cache
@api_view(["GET", "POST"])
def favorites(request):
    try:
        if request.method == "GET":
            return Response(list_items(request.user.pk, request.query_params))
        query = params(request.query_params, {"scope"})
        data = request.data
        required = {"kind", "target_id", "product"}
        if not isinstance(data, dict) or not (required <= data.keys() <= required | {"scope"}):
            raise Denied("invalid_request")
        if "scope" in data and ("scope" in query or data["scope"] != "synthetic"):
            raise Denied("invalid_request")
        if data["kind"] not in Favorite.Kind.values:
            raise Denied("invalid_request")
        result, status = create_item(
            request.user.pk,
            synthetic_scope(query | data),
            data["kind"],
            body_id(data["target_id"]),
            body_id(data["product"]),
        )
    except Denied as exc:
        return reject(exc)
    return Response(result, status=status)


@never_cache
@api_view(["GET", "PATCH", "DELETE"])
def favorite(request, item_id):
    try:
        item_id = pk(str(item_id))
        synthetic = synthetic_scope(params(request.query_params, {"scope"}))
        user_id = request.user.pk
        if request.method == "GET":
            return Response(get_item(user_id, item_id, synthetic))
        if request.method == "DELETE":
            delete_item(user_id, item_id, synthetic)
            return Response(status=204)
        data = request.data
        if not isinstance(data, dict) or set(data) != {"product"}:
            raise Denied("invalid_request")
        return Response(change_preference(user_id, item_id, synthetic, body_id(data["product"])))
    except Denied as exc:
        return reject(exc)
