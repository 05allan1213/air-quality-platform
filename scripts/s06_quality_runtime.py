"""V05 collection detail authorization with a real Worker and loopback F origin."""

import json
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from uuid import uuid4

from s01_runtime import Client, wait_for
from s02_support import BOUNDARY, START, allow, create_product, observe_db
from s03_runtime import LIMITS, city_packet, control_payload
from s06_guard import require

RUNS = "/api/v1/admin/collection-runs/"
ISSUES = "/api/v1/admin/quality-issues/"


def verify_quality_detail(web, operator, password, source):
    from collection.admission import LEDGER
    from collection.models import Job, Run
    from observations.disposition import dispose_due
    from observations.models import (
        Batch,
        City,
        CityIdentifier,
        QualityIssue,
        SiteEpoch,
        Station,
        StationIdentifier,
        StationObservation,
        StationRevision,
    )
    from observations.policy import clock, revoke_grant
    from psycopg.types.range import TimestamptzRange

    operator_web = Client(web.base).login(operator.username, password)
    period = TimestamptzRange(START, None)
    code, station_code, name = "125", "A125", "F station 125"
    product = create_product("S", "S", source)
    product.contract_version = "s03.f.v1"
    product.save(update_fields=["contract_version"])
    city = City.objects.create(source=source, name=f"Fixture {code}", geography=f"f-{code}")
    CityIdentifier.objects.create(
        city=city, source=source, code=code, active_period=period, evidence="F"
    )
    station = Station.objects.create(source=source, name=name)
    StationIdentifier.objects.create(
        source=source, station=station, code=station_code, active_period=period, evidence="F"
    )
    SiteEpoch.objects.create(
        station=station,
        city=city,
        active_period=period,
        name=name,
        location_evidence="F",
        certainty="confirmed",
    )
    job = Job.objects.create(
        product=product,
        city=city,
        executor=operator,
        profile="f-exact-v1",
        expected_codes=[station_code],
        roster_basis="F",
        enabled=True,
    )
    for use, fields in (
        ("acquire_online", ["*"]),
        ("process_local", ["normalized"]),
        ("retain_normalized", sorted(LEDGER | {"catalog"})),
        ("review", sorted(LEDGER)),
    ):
        options = {"collection_limits": LIMITS} if use == "acquire_online" else {}
        allow(
            operator,
            product,
            use,
            fields=fields,
            data_from=date.min,
            data_until=date.max,
            **options,
        )
    rights = {
        op: allow(operator, product, op, fields=["quality_detail"])
        for op in ("retain_normalized", "review")
    }

    packets = []
    accesses = []

    class Origin(BaseHTTPRequestHandler):
        def do_GET(self):
            if not packets or not self.path.startswith("/CityData/GetAQIDataPublishLive?cityName="):
                self.send_error(404)
                return
            target, payload = packets.pop(0)
            marker = self.headers.get("X-S03-Target")
            accesses.append(marker == target.isoformat())
            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    def execute(job, at, payload):
        packets.append((at, payload))
        status, submitted = operator_web.call(
            RUNS, dict(job=job.pk, reason="manual", request_id=str(uuid4()), target=at.isoformat())
        )
        if status != 202 or not submitted.get("run_id"):
            error = submitted.get("code")
            allowed = {
                "invalid_request",
                "unregistered_profile",
                "use_denied",
                "collection_paused",
                "rate_limited",
                "window_disposed",
                "window_closed",
                "budget_exhausted",
            }
            require(False, f"quality_v05_submit_{status}_{error if error in allowed else 'other'}")
        run = Run.objects.get(pk=submitted["run_id"])

        def terminal():
            run.refresh_from_db()
            return run if run.status not in {"pending", "running"} else None

        return wait_for("V05 Worker settlement", terminal, seconds=12)

    def read_run(run):
        status, record = operator_web.call(f"{RUNS}{run.pk}/")
        require(status == 200, "quality_v05_run_http")
        return record

    result = {}
    with ThreadingHTTPServer(("127.0.0.1", 18080), Origin) as origin:
        thread = Thread(target=origin.serve_forever)
        thread.start()
        try:
            for variant, hour in (
                ("allowed", BOUNDARY),
                ("ledger_only", BOUNDARY + timedelta(hours=2)),
            ):
                row = city_packet(hour, code, aqi=0) | {
                    "PositionName": name,
                    "StationCode": station_code,
                    "O3": "10 mg/m3",
                    "PM10": None,
                    "NO2": 10000,
                }
                row.pop("PM2_5")
                conflict = row | {"AQI": 20}
                bad_time = row | {"TimePoint": "F invalid timestamp"}
                first = execute(job, hour, [row, row, conflict, bad_time])
                next_hour = hour + timedelta(hours=1)
                empty = execute(job, next_hour, [])
                malformed = execute(job, next_hour, {"unexpected": []})
                require(
                    first.status == empty.status == "finished"
                    and first.result == "partial"
                    and empty.result == "no_new"
                    and not empty.items.exists()
                    and empty.batch_id is None
                    and malformed.status == "paused"
                    and malformed.error_code == "invalid_shape"
                    and malformed.batch_id is None
                    and not malformed.items.exists()
                    and empty.window_id == malformed.window_id,
                    "quality_v05_empty_structure",
                )
                trace, empty_trace, failed_trace = map(read_run, (first, empty, malformed))
                events = list(QualityIssue.objects.filter(run=first).order_by("pk"))
                expected = {
                    "missing_key",
                    "invalid_value",
                    "missing_null",
                    "concentration_peak",
                    "unordered_conflict",
                    "invalid_time",
                }
                revisions = StationRevision.objects.filter(
                    key__product=product, key__observed_at_utc=hour
                )
                key = StationObservation.objects.get(
                    product=product, indicator="AQI", observed_at_utc=hour
                )
                require(
                    key.disputed
                    and revisions.filter(key=key, is_current=True, value=0).exists()
                    and revisions.filter(key=key, candidate=True, value=20).exists()
                    and revisions.filter(
                        key__indicator="PM10", quality="missing", value=None
                    ).exists()
                    and revisions.filter(
                        key__indicator="NO2", quality="suspect", value=10000
                    ).exists()
                    and any(x["outcome"] == "duplicate" for x in trace["receipts"]),
                    "quality_v05_normalized_disposition",
                )
                require(not empty_trace["quality_issues"], "quality_v05_legal_empty_event")
                if variant == "allowed":
                    require(
                        {event.code for event in events} == expected
                        and {row["code"] for row in trace["quality_issues"]} == expected
                        and len(trace["quality_issues"]) == len(events)
                        and [row["code"] for row in failed_trace["quality_issues"]]
                        == ["invalid_shape"],
                        "quality_v05_event_codes",
                    )
                    by_code = {event.code: event for event in events}
                    link = by_code["unordered_conflict"]
                    require(
                        link.batch_id == first.batch_id == link.receipt.batch_id
                        and link.run_item_id == link.receipt.collection_item_id
                        and link.receipt.station_revision.key_id == key.pk
                        and link.severity == "blocking"
                        and link.rule_version == 1
                        and link.status == "open"
                        and link.handler_id is None
                        and by_code["missing_key"].receipt_id is None
                        and by_code["invalid_time"].run_item_id is None,
                        "quality_v05_exact_lineage",
                    )
                    revoke_grant(operator.pk, rights["review"].pk, "F detail review withdrawn")
                    review_hidden = read_run(first)
                    old_evidence, evidence = operator_web.call(
                        f"/api/v1/admin/observation-evidence/?product={product.pk}"
                        f"&scope=synthetic&station_key={key.pk}"
                    )
                    detail_status, _ = operator_web.call(f"{ISSUES}{link.pk}/")
                    require(
                        not review_hidden["quality_issues"]
                        and all(not item["reason"] for item in review_hidden["items"])
                        and all(
                            cell["reason"] != "invalid_value" for cell in review_hidden["cells"]
                        )
                        and old_evidence == 200
                        and not evidence["issues"]
                        and detail_status == 403
                        and QualityIssue.objects.filter(pk=link.pk).exists(),
                        "quality_v05_review_only_withdrawal",
                    )
                    revoke_grant(operator.pk, rights["retain_normalized"].pk, "F detail withdrawn")
                    dispose_due(product.pk)
                    after_cleanup = read_run(first)["coverage"]
                    require(
                        not QualityIssue.objects.filter(product=product).exists()
                        and not first.items.exclude(reason="").exists()
                        and Run.objects.get(pk=first.pk).batch_id == first.batch_id
                        and Batch.objects.filter(pk=first.batch_id).exists()
                        and all(
                            after_cleanup[k] == trace["coverage"][k]
                            for k in ("E", "O", "V", "accepted", "by_indicator")
                        ),
                        "quality_v05_physical_detail_disposition",
                    )
                    slot_released = malformed.reserved_at + timedelta(seconds=1)
                    wait_for(
                        "V05 reservation spacing",
                        lambda released=slot_released: clock() >= released,
                        seconds=4,
                    )
                    route = f"/api/v1/admin/collection-jobs/{job.pk}/"
                    status, basis = operator_web.call(route)
                    require(status == 200, "quality_v05_product_diagnostic")
                    control = control_payload(
                        "resume_product", basis["targets"]["product"]["control_version"]
                    )
                    status, _ = operator_web.call(route, control)
                    require(status == 200, "quality_v05_product_resume")
                else:
                    require(
                        not QualityIssue.objects.filter(product=product).exists()
                        and not trace["quality_issues"]
                        and not failed_trace["quality_issues"]
                        and not first.items.exclude(reason="").exists(),
                        "quality_v05_unlicensed_detail_persisted",
                    )
                with observe_db() as db:
                    item_count, receipt_count, event_count = db.execute(
                        "SELECT (SELECT count(*) FROM collection_runitem WHERE run_id=%s), "
                        "(SELECT count(*) FROM observations_receipt WHERE batch_id=%s), "
                        "(SELECT count(*) FROM observations_qualityissue WHERE product_id=%s)",
                        [first.pk, first.batch_id, product.pk],
                    ).fetchone()
                require(
                    item_count == 7 and receipt_count > item_count and event_count == 0,
                    "quality_v05_pg_reconcile",
                )
                result[variant] = {
                    "items": item_count,
                    "receipts": receipt_count,
                    "retained_events_after_checks": event_count,
                    "event_codes": sorted({e.code for e in events}),
                    "structure_event_count": len(failed_trace["quality_issues"]),
                    "legal_empty_status": empty.result,
                    "structure_status": malformed.status,
                    "structure_batches": int(malformed.batch_id is not None),
                    "conflict_remains_disputed": key.disputed,
                }
        finally:
            origin.shutdown()
            thread.join(timeout=3)
    require(accesses == [True] * 6 and not packets, "quality_v05_origin_calls")
    return result | {"loopback_fetches": len(accesses)}
