"""Authorized research-series directory for the S08 selection flow."""

from datetime import UTC, datetime, time, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from accounts.research_session import SessionCheck, reject, request_transaction
from django.db import connection
from rest_framework.response import Response

from .analysis_query import compatible, encoded
from .detail import unit_for
from .models import Product, Source, UseGrant
from .normalization import INDICATORS, REPORT_KIND, STANDARD_2012, STANDARD_2026, WINDOWS
from .policy import Denied, Scope, applicable
from .research_access import uses
from .research_spec import METHOD_VERSION
from .revisions import tables
from .s04_http import decode_cursor, encode_cursor
from .selection import TICK, ReadContext, Segment

OBLIGATIONS = uses("analysis", SimpleNamespace(method="statistics"))


def _window_names(product):
    if product.shape == "H":
        return ("hourly_label_unspecified",)
    return tuple(name for name in WINDOWS if name != "local_daily")


def _spec(indicator, window, standard, start, end, method):
    return SimpleNamespace(
        method=method,
        method_version=METHOD_VERSION,
        window=window,
        report=REPORT_KIND.get(window, "hourly"),
        indicators=(indicator,),
        units={indicator: unit_for(indicator)},
        standards={indicator: standard},
        start=start,
        end=end,
    )


def _authorized_days(ctx, product, identity, indicator, window, selected, decisions):
    entity_id, code, geography, identity_start, identity_end = identity
    zone = ZoneInfo(product.source_timezone)
    first = selected.start.astimezone(zone).date()
    last = (selected.end - TICK).astimezone(zone).date() + timedelta(days=1)
    cuts = {first, last}
    day = first
    while day < last:
        cuts.add(day)
        day += timedelta(days=1)
    for grant in ctx.grants[product.pk]:
        for boundary in (grant.data_from, grant.data_until):
            if first < boundary < last:
                cuts.add(boundary)

    width_hours, position = WINDOWS[window]
    width = timedelta(hours=width_hours)
    low = identity_start + (width if position == "right" else timedelta(0))
    high = identity_end
    if high and position == "left":
        high += TICK - width
    elif high and position == "right":
        high += TICK

    dates = sorted(cuts)
    for day_start, day_end in zip(dates, dates[1:], strict=False):
        local_start = datetime.combine(day_start, time.min, zone).astimezone(UTC)
        local_end = datetime.combine(day_end, time.min, zone).astimezone(UTC)
        start = max(selected.start, local_start, low)
        end = min(selected.end, local_end, high or selected.end)
        if start >= end:
            continue
        scope = Scope(geography, code, indicator, day_start, day_end)
        if scope not in decisions:
            try:
                decisions[scope] = tuple(
                    applicable(
                        product,
                        operation,
                        scope,
                        fields,
                        ctx.now,
                        ctx.grants[product.pk],
                        audience=audience,
                    )
                    for operation, audience, fields in OBLIGATIONS
                )
            except Denied as exc:
                if exc.code != "use_denied":
                    raise
                decisions[scope] = None
        if decisions[scope] is not None:
            yield (
                decisions[scope],
                Segment(entity_id, code, geography, indicator, start, end, day_start, day_end),
            )


def _coalesce_segments(segments):
    merged = []
    previous_signature = None
    previous = None
    for bases, segment in segments:
        signature = bases
        if (
            previous
            and signature == previous_signature
            and previous.day_end == segment.day_start
            and previous.end == segment.start
            and (previous.entity_id, previous.code, previous.indicator)
            == (segment.entity_id, segment.code, segment.indicator)
        ):
            previous = Segment(
                previous.entity_id,
                previous.code,
                previous.geography,
                previous.indicator,
                previous.start,
                segment.end,
                previous.day_start,
                segment.day_end,
            )
            merged[-1] = previous
        else:
            previous = segment
            merged.append(segment)
        previous_signature = signature
    return merged


def _standards(product, indicator, window, selected):
    choices = (
        (STANDARD_2012, STANDARD_2026, "unknown") if indicator == "AQI" else ("not_applicable",)
    )
    for standard in choices:
        spec = _spec(indicator, window, standard, selected.start, selected.end, selected.method)
        try:
            compatible(product, spec)
        except Denied as exc:
            if exc.code != "incompatible_series":
                raise
        else:
            yield standard


def _authorized_ranges(ctx, product, request_selection):
    entity_kind = "city" if product.level == "city" else "site_epoch"
    if request_selection.object_kind not in {None, entity_kind}:
        return [], {}
    identity_rows = ctx.identities(
        product, None if request_selection.object_id is None else (request_selection.object_id,)
    )
    indicators = INDICATORS[product.shape]
    wanted = request_selection.indicator
    decisions = {}
    candidates = {
        (indicator, window): tuple(_standards(product, indicator, window, request_selection))
        for indicator in indicators
        if wanted is None or wanted == indicator
        for window in _window_names(product)
    }
    ranges = []

    for identity in identity_rows:
        _, _, _, identity_start, identity_end = identity
        if identity_start and identity_start >= request_selection.end:
            continue
        if identity_end and identity_end <= request_selection.start:
            continue
        for indicator in indicators:
            for window in _window_names(product):
                for segment in _coalesce_segments(
                    _authorized_days(
                        ctx, product, identity, indicator, window, request_selection, decisions
                    )
                ):
                    if wanted is not None and wanted != indicator:
                        continue
                    for standard in candidates.get((indicator, window), ()):
                        ranges.append(
                            {
                                "product": product.pk,
                                "level": product.level,
                                "shape": product.shape,
                                "source": product.source.namespace,
                                "zone": product.source_timezone,
                                "entity": segment.entity_id,
                                "code": segment.code,
                                "indicator": indicator,
                                "unit": unit_for(indicator),
                                "window_name": window,
                                "report": REPORT_KIND.get(window, "hourly"),
                                "standard": standard,
                                "low": segment.start,
                                "high": segment.end,
                                "day_start": segment.day_start,
                                "day_end": segment.day_end,
                            }
                        )
    return ranges, {scope: basis for scope, basis in decisions.items() if basis is not None}


def _anchor_query(level):
    obs, rev, entity_field, _, _ = tables(level)
    return f"""SELECT a.* FROM allowed a WHERE a.level = '{level}' AND EXISTS (
        SELECT 1 FROM {obs._meta.db_table} k
        JOIN {rev._meta.db_table} r ON r.key_id = k.id AND r.is_current AND NOT r.candidate
        WHERE k.product_id = a.product AND k.{entity_field}_id = a.entity
          AND NOT k.retired AND k.object_code = a.code AND k.indicator = a.indicator
          AND k.window_definition = a.window_name AND k.report_kind = a.report
          AND k.standard_series = a.standard AND r.unit = a.unit
          AND k.observed_at_utc >= a.low AND k.observed_at_utc < a.high
          AND k.source_local_date >= a.day_start AND k.source_local_date < a.day_end)"""


def _sql_page(ranges, last, size):
    order = "product, entity, indicator, window_name, standard, low"
    after = f"WHERE ({order}) > (%s, %s, %s, %s, %s, %s::timestamptz)" if last is not None else ""
    # Only the authorized range relation can contribute a series or affect the cursor digest.
    with connection.cursor() as cursor:
        cursor.execute(
            f"""WITH allowed AS MATERIALIZED (
                SELECT * FROM jsonb_to_recordset(%s::jsonb) AS a(
                    product bigint, level text, shape text, source text, zone text, entity bigint,
                    code text, indicator text, unit text, window_name text, report text,
                    standard text, low timestamptz, high timestamptz, day_start date, day_end date)
            ), anchors AS (
                {_anchor_query("city")} UNION ALL {_anchor_query("station")}
            ), options AS MATERIALIZED (
                SELECT {order}, max(high) AS high, shape, source, zone, level, unit, report
                FROM anchors GROUP BY {order}, shape, source, zone, level, unit, report
            ), version AS (
                SELECT encode(sha256(convert_to(coalesce(string_agg(
                    to_jsonb(options)::text, E'\\n' ORDER BY {order}), ''), 'UTF8')), 'hex')
                    AS snapshot FROM options
            )
            SELECT snapshot, page.* FROM version LEFT JOIN LATERAL (
                SELECT * FROM options {after} ORDER BY {order} LIMIT %s
            ) page ON true ORDER BY {order}""",
            [encoded(ranges), *(last or ()), size + 1],
        )
        rows = cursor.fetchall()

    snapshot = rows[0][0]
    items = []
    for (
        _,
        product,
        entity,
        indicator,
        window,
        standard,
        start,
        end,
        shape,
        source,
        zone,
        level,
        unit,
        report,
    ) in rows:
        if product is not None:
            items.append(
                {
                    "source": source,
                    "product": product,
                    "shape": shape,
                    "city" if level == "city" else "site_epoch": entity,
                    "indicator": indicator,
                    "unit": unit,
                    "report_kind": report,
                    "window_definition": window,
                    "standard_series": standard,
                    "source_timezone": zone,
                    "permitted_from": start.isoformat(),
                    "permitted_until": end.isoformat(),
                }
            )
    return snapshot, items


def _cursor_key(row):
    return [
        row["product"],
        row.get("city", row.get("site_epoch")),
        row["indicator"],
        row["window_definition"],
        row["standard_series"],
        row["permitted_from"],
    ]


def read_series(request, selected):
    try:
        with request_transaction():
            products = Product.objects.filter(
                synthetic=selected.synthetic,
                shape__in=("C", "S", "H"),
            )
            for operation, audience, _ in OBLIGATIONS:
                products = products.filter(
                    pk__in=UseGrant.objects.filter(
                        operation=operation,
                        audience=audience,
                        effect="allow",
                        revoked_at__isnull=True,
                    ).values("product_id")
                )
            if selected.product is not None:
                products = products.filter(pk=selected.product)
            if selected.object_kind is not None:
                products = products.filter(
                    level="city" if selected.object_kind == "city" else "station"
                )
            product_sources = dict(products.values_list("pk", "source_id"))
            locked_sources = set(
                Source.objects.select_for_update()
                .filter(pk__in=product_sources.values())
                .order_by("pk")
                .values_list("pk", flat=True)
            )
            ctx = ReadContext(request.user.pk, product_sources, selected.synthetic)
            if any(p.source_id not in locked_sources for p in ctx.products.values()):
                raise Denied("page_changed")
            auth = SessionCheck(request)
            conditions = [
                selected.method,
                selected.start,
                selected.end,
                selected.synthetic,
                selected.product,
                selected.object_kind,
                selected.object_id,
                selected.indicator,
                selected.size,
            ]
            cursor = decode_cursor(selected.cursor, "catalog-series", auth.user_id, conditions)
            last = None
            if cursor:
                last = cursor.get("last")
                if not isinstance(last, list) or len(last) != 6:
                    raise Denied("invalid_cursor")

            ranges = []
            accesses = {}
            for product in ctx.products.values():
                current_ranges, decisions = _authorized_ranges(ctx, product, selected)
                ranges.extend(current_ranges)
                if decisions:
                    accesses[product.pk] = decisions
            if not accesses:
                raise Denied()

            snapshot, rows = _sql_page(ranges, last, selected.size)
            if cursor and cursor.get("snapshot") != snapshot:
                raise Denied("page_changed")
            page = rows[: selected.size]
            next_cursor = None
            if len(rows) > selected.size:
                next_cursor = encode_cursor(
                    "catalog-series",
                    auth.user_id,
                    conditions,
                    {"last": _cursor_key(page[-1]), "snapshot": snapshot},
                )
            response = {
                "results": page,
                "page": {"page_size": selected.size, "next_cursor": next_cursor},
            }
            now = auth.finish([])
            for product_id, decisions in accesses.items():
                product = ctx.products[product_id]
                for scope, bases in decisions.items():
                    for (operation, audience, fields), basis in zip(
                        OBLIGATIONS, bases, strict=True
                    ):
                        current = applicable(
                            product,
                            operation,
                            scope,
                            fields,
                            now,
                            ctx.grants[product_id],
                            audience=audience,
                        )
                        if current != basis:
                            raise Denied()
        return Response(response)
    except Denied as exc:
        if exc.code == "page_changed":
            return Response({"code": exc.code}, status=409)
        return reject(exc)
