"""The bounded history/latest API. Selection always precedes ordering and paging."""

from math import ceil

from django.db import transaction
from django.db.models import Q

from .normalization import INDICATORS, LEVELS, REPORT_KIND, STANDARD_2012, STANDARD_2026, WINDOWS
from .policy import Denied
from .queries import pk
from .s04_http import decode_cursor, encode_cursor, instant, page_size, params
from .selection import TICK, ReadContext

KEYS = {
    "product",
    "scope",
    "source",
    "cities",
    "site_epochs",
    "mode",
    "indicators",
    "report_kind",
    "window_definition",
    "unit",
    "standard_series",
    "quality",
    "start",
    "end",
    "as_of",
    "cursor",
    "page_size",
}
QUALITIES = {"all", "valid", "suspect", "missing", "censored", "invalid", "disputed"}


def sequence(value, maximum, converter=str):
    parts = value.split(",") if value else []
    if not parts or "" in parts:
        raise Denied("invalid_request")
    result = sorted({converter(part) for part in parts})
    if len(result) > maximum:
        raise Denied("query_too_large")
    return result


def unit_for(ind):
    if ind in LEVELS:
        return "grade"
    return {"AQI": "1", "CO": "mg/m³"}.get(ind, "µg/m³")


def validate_series(product, inds, window, report_kind, unit, standard):
    if (
        window not in WINDOWS
        or window == "local_daily"
        or product.shape == "D"
        or product.shape == "H"
        and window != "hourly_label_unspecified"
        or set(inds) - set(INDICATORS[product.shape])
        or REPORT_KIND.get(window, "hourly") != report_kind
        or report_kind != "hourly"
        and inds != ["O3"]
    ):
        raise Denied("invalid_request")
    if unit and any(unit_for(ind) != unit for ind in inds):
        raise Denied("invalid_request")
    for ind in inds:
        standards = (
            {STANDARD_2012, STANDARD_2026, "unknown"}
            if ind == "AQI" or ind in LEVELS
            else {"not_applicable"}
        )
        if standard and standard not in standards:
            raise Denied("invalid_request")


@transaction.atomic
def read_detail(user_id, query):
    values = params(query, KEYS)
    product_id = pk(values.get("product"))
    size = page_size(values)
    scope = values.get("scope")
    if scope not in (None, "synthetic"):
        raise Denied("invalid_request")
    ctx = ReadContext(user_id, [product_id], scope == "synthetic")
    product = ctx.products[product_id]
    if values.get("source", product.source.namespace) != product.source.namespace:
        raise Denied()
    entity = "cities" if product.level == "city" else "site_epochs"
    if ({"cities", "site_epochs"} & values.keys()) != {entity}:
        raise Denied("invalid_request")
    ids = sequence(values.get(entity), 5, pk)
    inds = sequence(values.get("indicators"), 7)
    mode = values.get("mode")
    window, report = values.get("window_definition"), values.get("report_kind")
    standard, unit = values.get("standard_series"), values.get("unit")
    quality = values.get("quality", "valid")
    validate_series(product, inds, window, report, unit, standard)
    selection = {
        "product": product.pk,
        "source": product.source.namespace,
        entity: ids,
        "indicators": inds,
        "window_definition": window,
        "report_kind": report,
        "mode": mode,
        "quality": quality,
    }
    selection.update({k: values[k] for k in ("unit", "standard_series", "scope") if k in values})
    time_keys = {"start", "end", "as_of"} & values.keys()
    if mode == "window" and time_keys == {"start", "end"} and quality in QUALITIES:
        start, end = instant(values["start"]), instant(values["end"])
        seconds = (end - start).total_seconds()
        if seconds <= 0:
            raise Denied("invalid_request")
        slots = ceil(seconds / 3600)
        if slots > 30 * 24 or slots * len(ids) * len(inds) > 3600:
            raise Denied("query_too_large")
        selection.update(start=start.isoformat(), end=end.isoformat())
    elif mode == "latest_valid" and time_keys <= {"as_of"} and quality == "valid":
        start = None
        anchor = instant(values["as_of"]) if "as_of" in values else ctx.now
        if anchor > ctx.now:
            raise Denied("invalid_request")
        if "as_of" in values:
            selection["as_of"] = anchor.isoformat()
        end = anchor + TICK
    else:
        raise Denied("invalid_request")
    binding = selection | {"page_size": size}
    prior = decode_cursor(values.get("cursor"), "detail", user_id, binding)
    if mode == "latest_valid":
        if prior:
            anchor = instant(prior["anchor"])
            end = anchor + TICK
        selection["as_of"] = anchor.isoformat()
    visible = ctx.scope(product, inds, ids, start=start, end=end, window=window)
    if not visible.permitted:
        raise Denied()
    selection[entity] = sorted({segment.entity_id for segment in visible.segments})
    selection["indicators"] = sorted({segment.indicator for segment in visible.segments})
    rows = visible.revisions(quality).filter(key__window_definition=window, key__report_kind=report)
    if unit:
        rows = rows.filter(unit=unit)
    if standard:
        rows = rows.filter(key__standard_series=standard)
    if mode == "latest_valid":
        rows = ctx.latest(rows, product)
    if prior:
        last = (
            rows.filter(key_id=prior["key"]).values_list("key__observed_at_utc", flat=True).first()
        )
        if last is None:
            raise Denied("page_changed")
        rows = rows.filter(
            Q(key__observed_at_utc__lt=last) | Q(key__observed_at_utc=last, key_id__lt=prior["key"])
        )
    page = list(rows.order_by("-key__observed_at_utc", "-key_id")[: size + 1])
    results = ctx.project(product, page[:size])
    cursor = None
    if len(page) > size:
        payload = {"key": page[size - 1].key_id}
        if mode == "latest_valid":
            payload["anchor"] = anchor.isoformat()
        cursor = encode_cursor("detail", user_id, binding, payload)
    ctx.finish()
    return dict(
        selection=selection,
        evaluated_at=ctx.now,
        synthetic=product.synthetic,
        state="ok" if results else "no_data",
        results=results,
        page={"page_size": size, "next_cursor": cursor},
    )
