"""Project bounded acquisition evidence for individually authorized observation cells."""

import json
from datetime import timedelta

from django.db import connection

from .policy import Scope


def collection_status(ctx, product, revisions, entity):
    from collection.admission import cell_key

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
                [
                    key.pk,
                    cell_key(entity_id, key.observed_at_utc, key.indicator),
                    key.object_code,
                    key.indicator,
                    key.observed_at_utc.isoformat(),
                ]
            )
    if not cells:
        return {}
    # A window's overall result can depend on another city's data. Match its frozen
    # roster / actual item and compute the outcome for just this cell.
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT (c.v->>0)::bigint, event.* FROM jsonb_array_elements(%s::jsonb) c(v)
            CROSS JOIN LATERAL (
                SELECT r.error_code, r.status, r.request_state, item.status, r.finished_at,
                       r.next_retry_at, j.cadence_seconds, r.started_at,
                       EXISTS (SELECT 1 FROM observations_receipt rec WHERE rec.collection_item_id=item.id
                               AND rec.outcome IN ('first','content_changed','unordered_conflict','same_version_conflict')) AS changed,
                       EXISTS (SELECT 1 FROM observations_receipt rec WHERE rec.collection_item_id=item.id
                               AND rec.outcome IN ('same','superseded','same_content','duplicate','watermark_advanced','late')) AS observed_receipt
                FROM collection_workwindow w
                JOIN collection_run r ON r.window_id=w.id
                JOIN collection_job j ON j.id=r.job_id AND j.product_id=w.product_id
                LEFT JOIN collection_runitem item ON item.run_id=r.id AND item.window_id=w.id
                    AND item.item_key=c.v->>1 AND item.object_code=c.v->>2
                    AND item.indicator=c.v->>3 AND item.observed_at=(c.v->>4)::timestamptz
                WHERE w.product_id=%s AND NOT w.disposed AND NOT j.disposed
                    AND w.target_start <= (c.v->>4)::timestamptz AND w.target_end > (c.v->>4)::timestamptz
                    AND (item.id IS NOT NULL OR ((w.expected -> (c.v->>1)) ->> 0 = c.v->>2
                         AND (w.expected -> (c.v->>1)) ->> 2 = c.v->>3))
                    AND (w.selection->>'mode' IS NULL OR w.selection->>'mode'='all' OR
                         (w.selection->>'mode'='include' AND (w.selection->'objects'->(c.v->>2)) ? (c.v->>3)))
                    AND ((r.finished_at <= %s AND r.status IN ('finished','paused','failed'))
                         OR (r.status='pending' AND r.error_code='budget_exhausted' AND r.request_state='unsent'
                             AND r.started_at <= %s AND r.next_retry_at > %s))
                ORDER BY COALESCE(r.finished_at,r.started_at) DESC, r.created_at DESC, r.id DESC LIMIT 1
            ) event
        """,
            [json.dumps(cells), product.pk, ctx.now, ctx.now, ctx.now],
        )
        rows = cursor.fetchall()
    results = {}
    for (
        pk,
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
    ) in rows:
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
            source_status=state if ctx.now < until else "unknown",
            source_event=state,
            source_event_at=finished,
            source_status_until=until,
            execution_started_at=started,
        )
    return results
