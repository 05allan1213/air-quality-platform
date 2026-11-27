"""Project bounded acquisition evidence for individually authorized observation cells."""

import json
from datetime import timedelta
from zoneinfo import ZoneInfo

from django.db import connection
from psycopg import sql

from .normalization import LEVELS, UNKNOWN_STANDARD
from .policy import Scope


def collection_status(ctx, product, revisions, entity):
    from collection.admission import PROFILES, cell_key
    from collection.cnemc_jobs import PROFILE, cnemc

    cells = []
    for revision in revisions:
        key = revision.key
        if key.observed_at_utc is None:
            continue
        entity_id = getattr(key, entity + "_id")
        day = key.source_local_date
        scope = Scope(
            ctx._geographies[product.source_id, product.level, entity_id],
            key.object_code,
            key.indicator,
            day,
            day + timedelta(days=1),
        )
        if ctx.basis(product, scope, ("source_status",)):
            cells.append(
                dict(
                    key_id=key.pk,
                    item_key=cell_key(entity_id, key.observed_at_utc, key.indicator),
                    object_code=key.object_code,
                    indicator=key.indicator,
                    observed_at=key.observed_at_utc.isoformat(),
                    window_standard=key.standard_series
                    if (key.report_kind, key.window_definition)
                    == ("hourly", "hourly_label_unspecified")
                    else None,
                    default_standard=UNKNOWN_STANDARD
                    if key.indicator == "AQI" or key.indicator in LEVELS
                    else "not_applicable",
                )
            )
    if not cells:
        return {}
    side = "city" if entity == "city" else "station"
    endpoint = cnemc.PRODUCTS.get(product.code)
    cnemc_contract = (
        product.contract_version == cnemc.VERSION
        and product.source_timezone == cnemc.TIMEZONE
        and endpoint is not None
        and cnemc.ENDPOINTS[endpoint].shape == product.shape
    )
    # Receipts associate responses with a full observation key. The roster alone
    # identifies a request cell. For receipt-free events, retain one candidate per
    # frozen standard so an invalid or unrelated configuration can be skipped.
    with connection.cursor() as cursor:
        cursor.execute(
            sql.SQL("""
            SELECT c.key_id, c.observed_at, c.window_standard, c.default_standard, event.*
            FROM jsonb_to_recordset(%s::jsonb) c(
                key_id bigint, item_key text, object_code text, indicator text,
                observed_at timestamptz, window_standard text, default_standard text
            )
            CROSS JOIN LATERAL (
                SELECT DISTINCT ON (basis.standard)
                       r.error_code, r.status, r.request_state, item.status, r.finished_at,
                       r.next_retry_at, j.cadence_seconds, r.started_at,
                       evidence.changed, evidence.observed_receipt, basis.standard::text,
                       r.created_at, r.id AS run_id
                FROM collection_workwindow w
                JOIN collection_run r ON r.window_id=w.id
                JOIN collection_job j ON j.id=r.job_id AND j.product_id=w.product_id
                LEFT JOIN collection_runitem item ON item.run_id=r.id AND item.window_id=w.id
                    AND item.item_key=c.item_key AND item.object_code=c.object_code
                    AND item.indicator=c.indicator AND item.observed_at=c.observed_at
                CROSS JOIN LATERAL (
                    SELECT bool_or(rec.outcome IN (
                               'first','content_changed','unordered_conflict','same_version_conflict'
                           )) AS changed,
                           bool_or(rec.outcome IN (
                               'same','superseded','same_content','duplicate','watermark_advanced','late'
                           )) AS observed_receipt
                    FROM observations_receipt rec
                    JOIN {revision} rev ON rev.id=rec.{revision_fk}
                    JOIN observations_batch b ON b.id=rec.batch_id AND NOT b.redacted
                    WHERE rec.collection_item_id=item.id AND rec.batch_id=r.batch_id
                        AND rev.key_id=c.key_id
                ) evidence
                CROSS JOIN LATERAL (
                    SELECT (evidence.changed IS TRUE OR evidence.observed_receipt IS TRUE) AS receipted,
                           w.profile = ANY(%s) AS fixture
                ) contract
                CROSS JOIN LATERAL (
                    SELECT CASE WHEN contract.receipted OR contract.fixture THEN NULL
                                ELSE w.standard END AS standard
                ) basis
                WHERE w.product_id=%s AND NOT w.disposed AND NOT j.disposed
                    AND w.target_start <= c.observed_at AND w.target_end > c.observed_at
                    AND (item.id IS NOT NULL OR ((w.expected -> c.item_key) ->> 0 = c.object_code
                         AND (w.expected -> c.item_key) ->> 2 = c.indicator))
                    AND (w.selection->>'mode' IS NULL OR w.selection->>'mode'='all' OR
                         (w.selection->>'mode'='include'
                             AND (w.selection->'objects'->c.object_code) ? c.indicator))
                    AND ((r.finished_at <= %s AND r.status IN ('finished','paused','failed'))
                         OR (r.status='pending' AND r.error_code='budget_exhausted' AND r.request_state='unsent'
                             AND r.started_at <= %s AND r.next_retry_at > %s))
                    AND (contract.receipted OR (
                        (item.id IS NULL OR item.status IN ('absent','rejected'))
                        AND (contract.fixture AND c.window_standard = c.default_standard
                             OR (%s AND w.profile = %s AND r.endpoint <> ''
                                 AND c.window_standard IS NOT NULL))
                    ))
                ORDER BY basis.standard, COALESCE(r.finished_at,r.started_at) DESC,
                         r.created_at DESC, r.id DESC
            ) event
            ORDER BY c.key_id, COALESCE(event.finished_at,event.started_at) DESC,
                     event.created_at DESC, event.run_id DESC
        """).format(
                revision=sql.Identifier(f"observations_{side}revision"),
                revision_fk=sql.Identifier(f"{side}_revision_id"),
            ),
            [
                json.dumps(cells),
                sorted(PROFILES),
                product.pk,
                ctx.projection_at,
                ctx.projection_at,
                ctx.projection_at,
                cnemc_contract,
                PROFILE,
            ],
        )
        rows = cursor.fetchall()
    results = {}
    selected = set()
    profiles = {}
    for (
        pk,
        observed_at,
        window_standard,
        default_standard,
        code,
        status,
        request,
        item,
        finished,
        retry,
        cadence,
        started,
        changed,
        has_receipt,
        standard_json,
        _created_at,
        _run_id,
    ) in rows:
        if pk in selected:
            continue
        if standard_json is not None:
            if standard_json not in profiles:
                config = json.loads(standard_json)
                try:
                    profiles[standard_json] = (
                        cnemc.observation_profile(product.shape, product.source_timezone, config)
                        if isinstance(config, dict)
                        else None
                    )
                except cnemc.WireError:
                    profiles[standard_json] = None
            evidence = profiles[standard_json]
            if evidence is None:
                continue
            profile, period = evidence
            day = observed_at.astimezone(ZoneInfo(cnemc.TIMEZONE)).date()
            series = (
                profile.standard_series
                if default_standard == UNKNOWN_STANDARD and period and period[0] <= day < period[1]
                else default_standard
            )
            if window_standard != series:
                continue
        selected.add(pk)
        state = "unknown"
        if code in {"budget_exhausted", "upstream_rate_limited"}:
            state = "quota_wait"
        elif code in {"upstream_transient", "upstream_denied", "timeout"} and request in {
            "possible",
            "responded",
        }:
            state = "upstream_unavailable"
        elif status == "finished" and request == "responded" and item in {"reread", "absent"}:
            if item == "absent":
                state = "missing_at_source"
            elif changed:
                state = "responded"
            elif has_receipt:
                state = "no_new"
        elif status == "finished" and request == "responded" and item == "accepted":
            state = "responded"
        if state == "unknown":
            continue
        until = (
            retry
            if state == "quota_wait" and retry and (finished is None or retry > finished)
            else finished + timedelta(seconds=cadence)
        )
        results[pk] = dict(
            source_status=state if ctx.projection_at < until else "unknown",
            source_event=state,
            source_event_at=finished,
            source_status_until=until,
            execution_started_at=started,
        )
    return results
