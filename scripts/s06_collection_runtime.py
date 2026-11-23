"""Exercise the failure-to-query chain with a loopback F origin and real Worker."""

import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from secrets import token_urlsafe
from threading import Event, Thread, current_thread
from unittest.mock import patch
from urllib.parse import urlencode
from uuid import uuid4
from wsgiref.simple_server import make_server

from s01_runtime import Client, wait_for
from s02_support import BOUNDARY, START, allow, create_product, observe_db
from s03_runtime import LIMITS, city_packet, control_payload
from s05_http import QuietRequestHandler
from s06_guard import require
from s06_quality_runtime import verify_quality_detail

RUNS = "/api/v1/admin/collection-runs/"
WINDOWS = "/api/v1/admin/collection-windows/"
HEALTH = "/api/v1/admin/operations/health/"


@contextmanager
def delayed_callback(run_id):
    """Hold one real HTTP transaction's committed callback before it reserves a send."""
    from collection import services
    from django.core.wsgi import get_wsgi_application

    waiting, release = Event(), Event()
    publish = services.publish
    with make_server(
        "127.0.0.1", 0, get_wsgi_application(), handler_class=QuietRequestHandler
    ) as server:
        thread = Thread(target=server.serve_forever)

        def gated(pk):
            if current_thread() is thread and pk == run_id:
                waiting.set()
                require(release.wait(7), "redelivery_callback_timeout")
            return publish(pk)

        with patch.object(services, "publish", gated):
            thread.start()
            try:
                yield Client(f"http://127.0.0.1:{server.server_port}"), waiting, release
            finally:
                release.set()
                server.shutdown()
                thread.join(3)
                require(not thread.is_alive(), "redelivery_web_not_stopped")


def verify_collection(runtime, web, password, checks):
    from collection.models import Job, Run
    from collection.services import MAX_DELIVERIES, reconcile_due
    from django.conf import settings
    from django.contrib.auth import get_user_model
    from django.contrib.auth.models import Permission
    from django.utils.dateparse import parse_datetime
    from observations.models import City, CityIdentifier, CityObservation, QualityIssue, Source
    from observations.policy import clock
    from psycopg.types.range import TimestamptzRange

    result = checks["collection_failure_chain"] = {}
    users = get_user_model().objects
    op = users.get(username="s06-runtime")
    op.user_permissions.add(
        *Permission.objects.filter(
            content_type__app_label="observations",
            codename__in=(
                "manage_grants",
                "manage_collection",
                "run_collection",
                "review_observation",
            ),
        )
    )
    reader_password = token_urlsafe(24)
    reader = users.create_user("s06-collection-reader", password=reader_password)
    source = Source.objects.create(
        namespace="s03.fixture", publisher="S06 F", collection_limits=LIMITS
    )
    checks["V05_quality_detail"] = verify_quality_detail(web, op, password, source)
    product = create_product("C", shape="C", source=source)
    product.contract_version = "s03.f.v1"
    product.save(update_fields=["contract_version"])
    city = City.objects.create(source=source, name="Fixture 123", geography="fixture-123")
    CityIdentifier.objects.create(
        city=city,
        source=source,
        code="123",
        active_period=TimestamptzRange(START, None),
        evidence="F",
    )
    hidden_city = City.objects.create(source=source, name="Fixture 124", geography="fixture-124")
    CityIdentifier.objects.create(
        city=hidden_city,
        source=source,
        code="124",
        active_period=TimestamptzRange(START, None),
        evidence="F",
    )
    job = Job.objects.create(
        product=product,
        city=city,
        executor=op,
        profile="f-exact-v1",
        expected_codes=["123"],
        roster_basis="F",
        enabled=True,
    )
    for use in ("acquire_online", "retain_normalized", "process_local", "review", "display"):
        options = {"object_code": "123"} if use == "display" else {}
        if use == "acquire_online":
            options["collection_limits"] = LIMITS
        allow(op, product, use, data_from=date.min, data_until=date.max, fields=["*"], **options)
    packets = [city_packet(BOUNDARY, "123"), []]
    accesses = []

    class Origin(BaseHTTPRequestHandler):
        def do_POST(self):
            if self.path != "/CityData/GetAQIDataPublishLiveInfo?cityCode=123" or not packets:
                self.send_error(404)
                return
            accesses.append(self.headers.get("X-S03-Target"))
            packet = json.dumps(packets.pop(0)).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(packet)))
            self.end_headers()
            self.wfile.write(packet)

        def log_message(self, *_args):
            pass

    next_hour = BOUNDARY + timedelta(hours=1)

    def submit(target):
        http, resp = web.call(
            RUNS,
            dict(job=job.pk, reason="manual", request_id=str(uuid4()), target=target.isoformat()),
        )
        require(http == 202 and resp.get("run_id"), "collection_submit_http")
        run = Run.objects.get(pk=resp["run_id"])
        return run

    def settled(run):
        def poll():
            run.refresh_from_db()
            return run if run.status not in {"pending", "running"} else None

        return wait_for("S06 F Run settlement", poll, seconds=12)

    with ThreadingHTTPServer(("127.0.0.1", 18080), Origin) as origin:
        thread = Thread(target=origin.serve_forever)
        thread.start()
        try:
            first = settled(submit(BOUNDARY))
            require(first.status == "finished" and first.items.count() == 7, "collection_f_write")
            malformed = settled(submit(next_hour))
            require(
                malformed.status == "paused"
                and malformed.error_code == "invalid_shape"
                and malformed.batch_id is None
                and not malformed.items.exists(),
                "collection_f_malformed",
            )
            event = QualityIssue.objects.get(run=malformed)
            status, trace = web.call(f"{RUNS}{malformed.pk}/")
            require(
                status == 200 and [e["id"] for e in trace["quality_issues"]] == [event.pk],
                "collection_f_malformed_trace",
            )
            resume_route = f"/api/v1/admin/collection-jobs/{job.pk}/"
            status, basis = web.call(resume_route)
            require(status == 200, "collection_job_diagnostic")
            wait_for(
                "F reservation spacing",
                lambda: clock() >= malformed.reserved_at + timedelta(seconds=1),
                seconds=4,
            )
            cmd = control_payload("resume_product", basis["targets"]["product"]["control_version"])
            status, resume = web.call(resume_route, cmd)
            result["resume_product"] = {"http": status, "code": resume.get("code", "")}
            require(status == 200, "collection_resume_product")
        finally:
            origin.shutdown()
            thread.join(timeout=3)
    require(accesses == [BOUNDARY.isoformat(), next_hour.isoformat()], "unexpected_f_access")
    failed = settled(submit(next_hour))
    require(
        failed.status == "failed" and failed.error_code == "upstream_transient",
        "collection_origin_failure",
    )
    require(failed.window_id == malformed.window_id, "collection_failure_window")
    status, trace = web.call(f"{RUNS}{failed.pk}/")
    require(status == 200 and len(trace["runs"]) == 1, "collection_failure_trace")
    require(
        trace["runs"][0]["error_code"] == failed.error_code
        and trace["receipts"] == []
        and trace["items"] == []
        and trace["quality_issues"] == [],
        "collection_failure_projection",
    )
    window_http, window = web.call(f"{WINDOWS}{failed.window_id}/")
    health_http, health = web.call(f"{HEALTH}?window={failed.window_id}")
    with observe_db() as db:
        window_id, start_at, end_at, expected, closed_at, owner, outcome = db.execute(
            "SELECT id, target_start, target_end, expected, closed_at, owner, result "
            "FROM collection_workwindow WHERE id=%s",
            [failed.window_id],
        ).fetchone()
        if isinstance(expected, str):
            expected = json.loads(expected)
        runs = db.execute(
            "SELECT id, attempt, status, error_code, dispatch, request_state, deliveries, "
            "batch_id, manifest_count, fetched_at, started_at, finished_at, next_retry_at "
            "FROM collection_run WHERE window_id=%s ORDER BY created_at, id",
            [window_id],
        ).fetchall()
        item_count = db.execute(
            "SELECT count(*) FROM collection_runitem WHERE window_id=%s", [window_id]
        ).fetchone()[0]
        issues = db.execute(
            "SELECT run_id, code, batch_id, run_item_id FROM observations_qualityissue "
            "WHERE run_id IN (%s, %s) ORDER BY id",
            [malformed.pk, failed.pk],
        ).fetchall()
        db_pid = db.execute("SELECT pg_backend_pid()").fetchone()[0]
    require(
        window_http == health_http == 200
        and trace["window_id"] == window["window_id"] == health["window"]["window_id"] == window_id
        and parse_datetime(window["target_start"]) == start_at
        and parse_datetime(window["target_end"]) == end_at
        and start_at == next_hour
        and end_at == next_hour + timedelta(hours=1)
        and closed_at is None
        and owner is None
        and trace["state"] == window["state"] == health["window"]["state"] == "open"
        and trace["result"]
        == window["result"]
        == health["window"]["result"]
        == outcome
        == "failed",
        "collection_window_db_http_identity",
    )
    run_fields = (
        "id",
        "attempt",
        "status",
        "error_code",
        "dispatch",
        "request_state",
        "deliveries",
        "batch_id",
        "manifest_count",
        "fetched_at",
        "started_at",
        "finished_at",
        "next_retry_at",
    )

    def values(entry):
        return tuple(
            parse_datetime(entry[field])
            if entry[field] is not None and field.endswith("_at")
            else entry[field]
            for field in run_fields
        )

    pg_runs = [((str(row[0]),) + row[1:]) for row in runs]
    require(
        [row[0] for row in runs] == [malformed.pk, failed.pk]
        and [values(entry) for entry in window["runs"]] == pg_runs
        and [values(entry) for entry in trace["runs"]] == pg_runs[1:]
        and issues == [(malformed.pk, "invalid_shape", None, None)]
        and all(row[7:9] == (None, None) for row in runs)
        and runs[0][5] == "responded"
        and runs[0][9] == malformed.fetched_at
        and runs[0][10] <= runs[0][9] <= runs[0][11]
        and runs[1][5] == "possible"
        and runs[1][9] is None,
        "collection_window_attempt_lineage",
    )
    coverage = window["coverage"]
    result["same_window_diagnostics"] = {
        "pg_observer_pid": db_pid,
        "window_id": window_id,
        "target_from": start_at.isoformat(),
        "target_until": end_at.isoformat(),
        "attempts": [{k: row[k] for k in run_fields} for row in window["runs"]],
        "pg_expected_cells": len(expected) if expected is not None else None,
        "pg_attempted_cells": item_count,
        "coverage": coverage,
        "run_coverage_matches": trace["coverage"] == coverage,
        "health": health,
        "http": {"run": status, "window": window_http, "health": health_http},
    }
    require(
        trace["coverage"] == coverage == health["window"]["coverage"]
        and (coverage["E"], coverage["O"], coverage["V"], coverage["accepted"])
        == (len(expected), 0, 0, 0)
        and len(expected) == coverage["not_received"] == 7
        and len(window["cells"]) == len(expected)
        and coverage["attempted_cells"] == item_count == 0
        and coverage["latest_observed_at"] is None
        and coverage["latest_valid_at"] is None
        and not window["items"]
        and not window["receipts"]
        and health["window"]["visible_pending"] == health["window"]["visible_running"] == 0
        and health["window"]["visible_retry"] == sum(row[-1] is not None for row in runs)
        and all(component["state"] == "ok" for component in health["components"].values())
        and health["components"]["worker"]["queue_readiness"]["state"] == "ok",
        "collection_window_coverage_health",
    )
    for cell in window["cells"]:
        code, at, indicator = expected[cell["item_key"]]
        require(
            cell["object_code"] == code == "123"
            and parse_datetime(cell["observed_at"]) == parse_datetime(at) == next_hour
            and cell["indicator"] == indicator
            and not any(cell[field] for field in ("received", "accepted", "valid"))
            and cell["reason"] == "not_acquired",
            "collection_window_impact_cells",
        )
    impact_url = f"{WINDOWS}{window_id}/?object_code=123&indicator=AQI"
    impact_http, impact = web.call(impact_url)
    require(
        impact_http == 200
        and impact["window_id"] == window_id
        and impact["coverage"]["E"] == impact["coverage"]["not_received"] == 1
        and impact["coverage"]["O"] == impact["coverage"]["V"] == 0
        and impact["cells"] == [row for row in window["cells"] if row["indicator"] == "AQI"],
        "collection_precise_impact_scope",
    )
    result["same_window_diagnostics"]["impact_aqi_cells"] = impact["cells"]
    result["same_window_diagnostics"]["http"]["impact"] = impact_http
    result["worker_f_lineage"] = {
        "status": first.status,
        "observations": CityObservation.objects.filter(product=job.product).count(),
        "pre_manifest_issue": event.code,
        "pre_manifest_batches": 0,
        "failed_error_code": failed.error_code,
        "failed_attempt": trace["runs"][0]["attempt"],
        "failure_window_reused": True,
        "upstream_http_requests": len(accesses),
    }
    require(result["worker_f_lineage"]["observations"] == 7, "collection_f_row_count")
    # Freeze automatic retries before attributing the run count to read-only HTTP calls.
    Run.objects.filter(pk=failed.pk).update(next_retry_at=None)
    before = Run.objects.count()
    runtime.stop("web")
    web = runtime.web().login(op.username, password)
    user = Client(web.base).login(reader.username, reader_password)
    query = {"scope": "synthetic", "product": job.product_id}
    url = f"/api/v1/cities/{job.city_id}/?"
    http, latest = user.call(url + urlencode(query))
    require(http == 200 and latest["state"] == "ok", "collection_latest_query")
    aqi = next(slot for slot in latest["slots"] if slot["indicator"] == "AQI")["results"][0]
    with observe_db() as db:
        original = db.execute(
            "SELECT k.observed_at_utc, b.fetched_at, r.id, r.value, u.fetched_at "
            "FROM observations_cityobservation k "
            "JOIN observations_cityrevision r ON r.key_id=k.id AND r.is_current "
            "JOIN observations_receipt e ON e.city_revision_id=r.id "
            "JOIN observations_batch b ON b.id=e.batch_id "
            "JOIN collection_run u ON u.batch_id=b.id "
            "WHERE k.product_id=%s AND k.city_id=%s AND k.indicator='AQI' AND u.id=%s",
            [product.pk, city.pk, first.pk],
        ).fetchone()
        pg_time = db.execute("SELECT clock_timestamp()").fetchone()[0]
    require(
        original is not None
        and parse_datetime(aqi["observed_at"]) == original[0] == BOUNDARY
        and parse_datetime(aqi["fetched_at"]) == original[1] == original[4] == first.fetched_at
        and aqi["fetched_at_basis"] == "contributing_batch"
        and aqi["revision"] == original[2]
        and aqi["value"] == str(original[3])
        and 0 <= int((pg_time - BOUNDARY).total_seconds()) - aqi["age_seconds"] <= 2,
        "collection_latest_not_original",
    )
    explicit, empty = user.call(url + urlencode(query | {"at": next_hour.isoformat()}))
    require(explicit == 200 and empty["state"] == "no_data", "collection_historical_fallback")
    hidden, _ = user.call(f"/api/v1/cities/{hidden_city.pk}/?" + urlencode(query))
    require(hidden == 403, "collection_reader_hidden_city_leak")
    blocked, _ = user.call(f"{RUNS}{failed.pk}/")
    blocked_window, _ = user.call(f"{WINDOWS}{window_id}/")
    blocked_health, _ = user.call(f"{HEALTH}?window={window_id}")
    require(blocked == blocked_window == blocked_health == 403, "collection_reader_diagnostic_leak")
    require(before == Run.objects.count(), "collection_query_enqueued_run")
    result["cold_web_queries"] = {
        "latest_http": http,
        "observed_at": aqi["observed_at"],
        "fetched_at": aqi["fetched_at"],
        "fetched_at_basis": aqi["fetched_at_basis"],
        "age_seconds": aqi["age_seconds"],
        "explicit_window_http": explicit,
        "explicit_window_state": empty["state"],
        "unauthorized_diagnostic_http": blocked,
        "unauthorized_city_http": hidden,
        "run_delta": 0,
        "origin_online": False,
    }
    runtime.stop("beat")
    runtime.stop("worker")
    # A connection failure cannot release its conservative concurrency reservation early.
    available = max(failed.occupied_until, failed.reserved_at + timedelta(seconds=1))
    wait_for(
        "redelivery reservation spacing",
        lambda: clock() >= available,
        seconds=20,
    )
    # Drop only this Run's published message in its private Redis queue. Accelerate
    # retry eligibility, but let the production scanner count and exhaust deliveries.
    queue = settings.CELERY_TASK_ROUTES["collection.execute_run"]["queue"]
    with runtime.broker() as broker:
        baseline = broker.llen(queue)
        manual = submit(next_hour)
        for number in range(1, MAX_DELIVERIES + 1):
            manual.refresh_from_db()
            require(
                manual.dispatch == "sent"
                and manual.deliveries == number
                and manual.request_state == "unsent"
                and manual.reserved_at is None,
                "dispatch_attempt_not_recorded",
            )
            message = broker.lindex(queue, 0)
            require(
                broker.llen(queue) == baseline + 1
                and message
                and json.loads(message)["headers"]["id"] == str(manual.pk),
                "unexpected_broker_message",
            )
            require(broker.lpop(queue) == message, "failed_to_inject_message_loss")
            Run.objects.filter(pk=manual.pk).update(dispatch_due=clock())
            reconcile_due()
        manual.refresh_from_db()
        require(
            manual.dispatch == "manual"
            and manual.deliveries == MAX_DELIVERIES
            and not manual.dispatch_due
            and not manual.started_at
            and broker.llen(queue) == baseline,
            "dispatch_not_exhausted",
        )
        path = f"{RUNS}{manual.pk}/"
        status, diagnosis = web.call(path)
        require(status == 200 and diagnosis["target"]["dispatch"] == "manual", "manual_dispatch")
        command = control_payload("redeliver", diagnosis["target"]["control_version"])
        with (
            delayed_callback(manual.pk) as (other, waiting, release),
            ThreadPoolExecutor(max_workers=2) as workers,
        ):
            other.login(op.username, password)
            winner = workers.submit(other.call, path, command)
            try:
                require(waiting.wait(5), "redelivery_callback_not_observed")
                duplicate = workers.submit(web.call, path, command)
                reconcile_due()
                require(broker.llen(queue) == baseline + 1, "scanner_did_not_publish")
            finally:
                release.set()
            status, response = winner.result(timeout=10)
            replay = duplicate.result(timeout=10)
        checks["collection_redelivery_response"] = {
            "http": status,
            "code": response.get("code", ""),
        }
        require(status == 202 and response["run_id"] == str(manual.pk), "redelivery_http")
        count = broker.llen(queue)
        require(count == baseline + 1, "redelivery_broker_publish")
        require(
            replay == (status, response) == web.call(path, command), "redelivery_response_replay"
        )
        require(broker.llen(queue) == count, "redelivery_broker_duplicate")
    runtime.worker()
    settled(manual)
    require(
        manual.status == "failed" and manual.error_code == "upstream_transient",
        "redelivery_worker_not_original",
    )
    require(Run.objects.count() == before + 1 and manual.deliveries == 1, "redelivery_run_counts")
    checks["collection_redelivery"] = {
        "injected_fault": "lost_owned_messages_and_accelerated_retry_clock",
        "exhausted_actual_deliveries": MAX_DELIVERIES,
        "concurrent_sessions": 2,
        "http": status,
        "broker_messages": 1,
        "replay_messages": 0,
        "scanner_preceded_committed_callback": True,
        "original_run_deliveries": manual.deliveries,
        "worker_error": manual.error_code,
    }
    return web
