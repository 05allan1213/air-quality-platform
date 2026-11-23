"""Discovery starts with rows the user can read; a source directory is a separate grant."""

from datetime import timedelta
from zoneinfo import ZoneInfo

from django.db import transaction
from django.db.models import Q
from django.urls import path

from .models import Product, UseGrant
from .normalization import INDICATORS, WINDOWS
from .policy import Denied, Scope
from .queries import pk
from .revisions import tables
from .s04_http import (
    decode_cursor,
    encode_cursor,
    fingerprint,
    instant,
    page_size,
    params,
    read_view,
)
from .s04_station import CATALOG, HOUR, history_link, observation_link, synthetic_scope
from .selection import TICK, ReadContext

ROUTE = "catalog-objects"


def candidates(ctx, product, at, lower, limit):
    """Take a bounded, authorized prefix per window; merge by stable entity identity."""
    _, model, entity, _, _ = tables(product.level)
    ref = "key__" + entity + "_id"
    windows = (
        ("local_daily",)
        if product.shape == "D"
        else (
            ("hourly_label_unspecified",)
            if product.shape == "H"
            else tuple(w for w in WINDOWS if w != "local_daily")
        )
    )
    selected = {}
    for window in windows:
        visibility = ctx.scope(product, INDICATORS[product.shape], end=at + TICK, window=window)
        if not visibility.permitted:
            continue
        if product.shape == "D":
            cells = Q(pk__in=[])
            for segment in visibility.segments:
                cells |= Q(
                    key__city_id=segment.entity_id,
                    key__object_code=segment.code,
                    key__indicator=segment.indicator,
                    key__local_report_date__gte=segment.day_start,
                    key__local_report_date__lt=segment.day_end,
                )
            revisions = model.objects.filter(
                cells,
                key__product=product,
                key__retired=False,
                key__disputed=False,
                key__local_report_date__lte=at.astimezone(ZoneInfo(product.source_timezone)).date(),
                is_current=True,
                candidate=False,
                quality="valid",
                value__isnull=False,
            )
            stamp_field = "key__local_report_date"
        else:
            revisions = visibility.revisions().filter(key__observed_at_utc__lte=at)
            stamp_field = "key__observed_at_utc"
        found = (
            revisions.filter(key__window_definition=window, **{ref + "__gte": lower})
            .select_related("key__" + entity)
            .order_by(ref, "-" + stamp_field, "-key_id")
            .distinct(ref)[:limit]
        )
        for revision in found:
            key = revision.key
            identity = getattr(key, entity + "_id")
            order = (key.observed_at_utc or key.local_report_date, key.pk)
            if identity not in selected or order > selected[identity][0]:
                selected[identity] = order, key
    return [selected[i][1] for i in sorted(selected)[:limit]]


def entry(ctx, product, key):
    _, _, field, _, _ = tables(product.level)
    obj = getattr(key, field)
    day = key.source_local_date
    site = field == "site_epoch"
    geo = ctx._geographies[product.source_id, product.level, obj.pk]
    cell = Scope(geo, key.object_code, key.indicator, day, day + timedelta(days=1))
    has_catalog = ctx.basis(product, cell, CATALOG)
    stamp = key.observed_at_utc
    if product.shape == "D":
        href = observation_link(
            "observations/current",
            product,
            city=obj.pk,
            local_report_date=key.local_report_date.isoformat(),
            indicator=key.indicator,
            report_kind=key.report_kind,
            window_definition=key.window_definition,
            standard_series=key.standard_series,
        )
        history = href
    else:
        history = history_link(
            product,
            obj.pk,
            key.window_definition,
            [key.indicator],
            stamp,
            stamp + HOUR,
            standard_series=key.standard_series,
        )
        if site:
            href = observation_link(
                f"stations/{obj.station_id}", product, site_epoch=obj.pk, as_of=stamp.isoformat()
            )
        elif product.shape == "C":
            href = observation_link(f"cities/{obj.pk}", product, at=stamp.isoformat())
        elif product.shape == "T":
            href = observation_link(f"cities/{obj.pk}/trend", product, anchor=stamp.isoformat())
        else:
            href = observation_link("overview/national", product, at=stamp.isoformat())
    return {
        "product": product.pk,
        "shape": product.shape,
        "source": product.source.namespace,
        "synthetic": product.synthetic,
        field: obj.pk,
        "name": obj.name if has_catalog else None,
        "attribution": product.attribution,
        "rights_attributions": ctx.basis(product, cell).attributions,
        "observed_at": stamp,
        "local_report_date": key.local_report_date,
        "indicator": key.indicator,
        "link": href,
        "history_link": history,
    }


@transaction.atomic
def discover(user_id, query):
    values = params(query, {"scope", "product", "at", "page_size", "cursor"})
    synthetic = synthetic_scope(values)
    selection = pk(values["product"]) if "product" in values else None
    size = page_size(values)
    requested = instant(values["at"]) if "at" in values else None
    conditions = [selection, synthetic, requested, size]
    after = decode_cursor(values.get("cursor"), ROUTE, user_id, conditions)
    products = Product.objects.filter(
        synthetic=synthetic,
        pk__in=UseGrant.objects.filter(
            operation="display", revoked_at__isnull=True, effect="allow"
        ).values("product_id"),
    )
    if selection:
        products = products.filter(pk=selection)
    if after:
        products = products.filter(pk__gte=after["product"])
    ctx = ReadContext(user_id, products.values_list("pk", flat=True), synthetic)
    at = instant(after["at"]) if after else requested or ctx.now
    if at > ctx.now:
        raise Denied("invalid_request")
    entries = []
    seen_anchor = not after
    for product in ctx.products.values():
        lower = after["object"] if after and after["product"] == product.pk else 0
        for key in candidates(ctx, product, at, lower, size + 2):
            row = entry(ctx, product, key)
            identity = (row["product"], row.get("city", row.get("site_epoch")))
            if not seen_anchor:
                if (
                    identity != (after["product"], after["object"])
                    or fingerprint(row) != after["row"]
                ):
                    raise Denied("page_changed")
                seen_anchor = True
                continue
            entries.append((identity, row))
            if len(entries) > size:
                break
        if len(entries) > size:
            break
    if not seen_anchor:
        raise Denied("page_changed")
    ctx.finish()
    cursor = None
    if len(entries) > size:
        (last_product, last_id), last = entries[size - 1]
        cursor = encode_cursor(
            ROUTE,
            user_id,
            conditions,
            {
                "product": last_product,
                "object": last_id,
                "row": fingerprint(last),
                "at": at.isoformat(),
            },
        )
    return {
        "results": [row for _, row in entries[:size]],
        "page": {"page_size": size, "next_cursor": cursor},
    }


urlpatterns = [path("catalog/objects/", read_view(discover))]
