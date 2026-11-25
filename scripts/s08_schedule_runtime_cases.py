"""Real Beat, Worker, PostgreSQL, and loopback F; business time is injected."""

import json
import math
import os
import subprocess
import sys
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from secrets import token_urlsafe
from threading import Thread
from unittest.mock import patch
from uuid import uuid4

from s01_runtime import ROOT, wait_for
from s06_guard import IsolationError, require
from s06_runtime import Runtime
from s07_beat_checks import BEAT, backend_pid
from s07_host_guard import check_broker, check_database, check_environment, check_process_network
from verify_s07_host import start_broker

JOBS = "/api/v1/admin/collection-jobs/"
RUNS = "/api/v1/admin/collection-runs/"
HOUR = timedelta(hours=1)
SCHEDULE_REASONS = {
    "create_draft": "schedule_create",
    "propose": "schedule_change",
    "activate": "schedule_activate",
    "discard": "schedule_discard",
}


def advance(root, stamp):
    # Inject a progressing PG clock into the application and Worker. Beat's real
    # scheduler and process leases keep wall time, so a jump cannot simulate a publish.
    offset = (stamp - datetime.now(UTC)).total_seconds()
    path = root / "stage" / "clock.next"
    path.write_text(str(offset))
    path.replace(path.with_suffix(".offset"))


def setup_application(env, root):
    import django

    require(env.get("DJANGO_SETTINGS_MODULE", "config.settings") == "config.settings", "settings")
    os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings"
    django.setup()
    from django.conf import settings
    from django.db.backends.utils import CursorWrapper

    require(settings.CNEMC_MODE == "off" and not settings.CNEMC_FIXTURE_HTTP, "cnemc_enabled")
    settings.S03_FIXTURE_HTTP = True
    queue = env["AQ_INSTANCE_ID"] + ".collection"
    settings.CELERY_TASK_ROUTES = settings.CELERY_TASK_ROUTES | {
        "collection.reconcile_due": {"queue": queue},
        "collection.execute_run": {"queue": queue + ".run"},
    }
    settings.OPERATIONS_WORKER = "worker@" + env["AQ_INSTANCE_ID"]
    execute = CursorWrapper.execute
    offset_path = root / "stage" / "clock.offset"

    def inject(cursor, sql, params=None):
        if isinstance(sql, str) and "clock_timestamp()" in sql and offset_path.exists():
            offset = float(offset_path.read_text())
            require(math.isfinite(offset) and abs(offset) < 6 * 86400, "invalid_clock_offset")
            sql = sql.replace(
                "clock_timestamp()", f"(pg_catalog.clock_timestamp() + interval '{offset} seconds')"
            )
        return execute(cursor, sql, params)

    return queue, patch.object(CursorWrapper, "execute", inject)


def worker(env, port, *, scan_only):
    from celery.signals import task_postrun

    guard_env = {key: env[key] for key in env if key != "DJANGO_SETTINGS_MODULE"}
    root = check_environment(guard_env)
    check_process_network(env["S06_HOST_NET_NS"])
    check_database(guard_env, empty=False)
    check_broker(guard_env, empty=False)
    require(1024 <= port <= 65535, "invalid_f_origin_port")
    queue, clock_patch = setup_application(env, root)
    from collection.adapters import transport
    from config.celery import app
    from django.conf import settings

    transport.ORIGIN = f"http://127.0.0.1:{port}"
    queues = [queue] if scan_only else [queue, queue + ".run"]

    @task_postrun.connect(weak=False)
    def record(task_id, task, state, **_kwargs):
        route = (task.request.delivery_info or {}).get("routing_key")
        if route in queues:
            observed = [
                task.name,
                task_id,
                state,
                (task.request.headers or {}).get("aq_beat_publisher"),
            ]
            with (root / "stage" / "tasks.jsonl").open("a") as out:
                out.write(json.dumps(observed) + "\n")

    with clock_patch:
        app.worker_main(
            [
                "worker",
                "-l",
                "WARNING",
                "--pool",
                "solo",
                "-Q",
                ",".join(queues),
                "-n",
                settings.OPERATIONS_WORKER,
                "--without-gossip",
                "--without-mingle",
                "--prefetch-multiplier",
                "1",
            ]
        )


@contextmanager
def origin(allowed, fault_target, *, fault_status=503, fault_body=None):
    from s03_runtime import city_packet

    requests = []
    rejected = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            if (
                self.path != "/CityData/GetAQIDataPublishLiveInfo?cityCode=123"
                or self.headers.get("X-S03-Target") not in allowed
                or self.headers.get("Content-Length", "0") != "0"
            ):
                rejected.append(True)
                self.send_error(404)
                return
            target = self.headers["X-S03-Target"]
            inject = target == fault_target and all(t != target for t, _ in requests)
            status = fault_status if inject else 200
            requests.append((target, status))
            payload = (
                fault_body
                if inject and fault_body is not None
                else json.dumps(city_packet(datetime.fromisoformat(target), "123")).encode()
            )
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = Thread(target=server.serve_forever)
        thread.start()
        try:
            yield server.server_port, requests, rejected
        finally:
            server.shutdown()
            thread.join(3)
            require(not thread.is_alive(), "origin_thread_remaining")


def seed():
    from collection.services import register_job
    from django.contrib.auth import get_user_model
    from django.contrib.auth.models import Permission
    from observations.models import City, CityIdentifier, Source
    from observations.policy import clock
    from psycopg.types.range import TimestamptzRange
    from s02_support import START, allow, create_product
    from s03_runtime import LIMITS

    password = token_urlsafe(24)
    account = get_user_model().objects.create_user("s08-schedule-runtime", password=password)
    permissions = (
        "configure_collection_schedule",
        "manage_collection",
        "manage_grants",
        "run_collection",
    )
    authorized = list(
        Permission.objects.filter(content_type__app_label="observations", codename__in=permissions)
    )
    require(len(authorized) == len(permissions), "fixture_named_permissions")
    account.user_permissions.add(*authorized)
    source = Source.objects.create(
        namespace="s03.fixture", publisher="S08 F", collection_limits=LIMITS
    )
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
    for use in ("acquire_online", "retain_normalized", "process_local", "review"):
        terms = {"collection_limits": LIMITS} if use == "acquire_online" else {}
        allow(
            account,
            product,
            use,
            data_from=date.min,
            data_until=date.max,
            fields=["*"],
            valid_from=clock() - 8 * HOUR,
            **terms,
        )
    basis = register_job(
        account.pk, account.pk, product.pk, city.pk, "f-exact-v1", 3600, None, ["123"], "S08 F city"
    )
    return account, password, basis


def session(account, password):
    from django.test import Client

    client = Client(enforce_csrf_checks=True, HTTP_HOST="localhost")

    def csrf():
        response = client.get("/api/v1/auth/csrf/")
        require(response.status_code == 200, "csrf_status")
        return response.json()["csrf_token"]

    def send(path, body, status, csrf_token):
        response = client.post(
            path, body, content_type="application/json", HTTP_X_CSRFTOKEN=csrf_token
        )
        detail = (
            response.json() if response.headers.get("Content-Type") == "application/json" else {}
        )
        require(
            response.status_code == status,
            f"http_status_{response.status_code}_wanted_{status}_{detail.get('code', '')}",
        )
        return detail

    send("/api/v1/auth/login/", {"username": account.username, "password": password}, 200, csrf())
    token = csrf()

    def post(path, body, status):
        return send(path, body, status, token)

    return post


def command(action, version, **fields):
    return {
        "action": action,
        "reason_code": SCHEDULE_REASONS[action],
        "expected_version": version,
        "request_id": str(uuid4()),
        **fields,
    }


def configure(post, account, basis, first, boundary):
    from collection.models import Run, WorkWindow
    from collection.s08_schedule import register_template

    template = register_template(account.pk, basis.pk, [3600, 7200], 0)
    initial = dict(template=template.pk, cadence_seconds=3600, first_due_at=first.isoformat())

    def decide(plan, action):
        return post(
            f"{JOBS}{plan['job']}/schedule/",
            command(
                action,
                plan["version"],
                template_version=plan["template_version"],
                expected_predecessor_version=plan["predecessor_version"],
            ),
            200,
        )

    discarded = decide(
        post(JOBS, command("create_draft", template.version, **initial), 201), "discard"
    )
    require(discarded["state"] == "discarded", "discard_state")
    create = command("create_draft", template.version, **initial)
    draft = post(JOBS, create, 201)
    require(post(JOBS, create, 201) == draft, "draft_replay")
    require(not Run.objects.exists() and not WorkWindow.objects.exists(), "draft_created_work")
    old = decide(draft, "activate")
    newer = post(
        f"{JOBS}{old['job']}/schedule/",
        command(
            "propose",
            old["version"],
            template_version=template.version,
            cadence_seconds=7200,
            first_due_at=boundary.isoformat(),
        ),
        201,
    )
    new = decide(newer, "activate")
    require(old["state"] == new["state"] == "active", "activation_state")
    return old["job"], new["job"]


def start_beat(runtime, root):
    publisher = uuid4().hex
    with patch.dict(
        os.environ,
        AQ_BEAT_LOCK_FILE=str(root / "V02" / "beat.lock"),
        AQ_BEAT_PUBLISHER_ID=publisher,
    ):
        runtime.start("beat", sys.executable, *BEAT)
    require(
        wait_for("exclusive Beat PG lock", lambda: backend_pid(publisher), seconds=15), "beat_lock"
    )
    runtime.check_process(runtime.children["beat"])
    return publisher


def stop_beat(runtime):
    runtime.stop("beat")
    require(backend_pid() is None, "beat_pg_lock_remaining")


def task_events(root):
    path = root / "stage" / "tasks.jsonl"
    return [json.loads(row) for row in path.read_text().splitlines()] if path.exists() else []


def start_worker(runtime, queue, port, *, scan_only=False):
    from accounts.models import ServicePulse

    ServicePulse.objects.filter(component="worker").delete()
    runtime.start(
        "worker",
        sys.executable,
        str(ROOT / "scripts/s08_schedule_runtime.py"),
        "_worker",
        str(port),
        "scan" if scan_only else "all",
    )

    def ready():
        runtime.check_process(runtime.children["worker"])
        return ServicePulse.objects.filter(
            component="worker",
            instance=runtime.node,
            subscribed_queues=[queue] if scan_only else [queue, queue + ".run"],
        ).first()

    wait_for("dedicated Worker subscription", ready, seconds=15)


def verify(root, runtime, queue, evidence):
    from collection.models import ControlEvent, Job, Run, RunItem, WorkWindow
    from config.celery import app
    from observations.policy import clock
    from s02_support import observe_db

    account, password, basis = seed()
    post = session(account, password)
    # Grant registration and login have already committed on the real PG clock.
    # The F labels will also precede the real timestamps of HTTP responses.
    advance(root, datetime.now(UTC) - 6 * HOUR)
    now = clock()
    first = now.replace(minute=0, second=0, microsecond=0) + HOUR
    boundary, gap, held = first + 3 * HOUR, first + HOUR, first + 2 * HOUR
    old, new = configure(post, account, basis, first, boundary)

    def label(stamp):
        return stamp.isoformat()

    evidence["boundary"] = label(boundary)
    evidence["jobs"] = {"old": old, "new": new}
    evidence["fixture_only"] = True
    evidence["clock_injection"] = "business clock offset; Beat scheduler uses wall time"
    evidence["stage"] = "before_boundary"
    allowed = {label(stamp) for stamp in (gap, held, boundary)}
    with origin(allowed, label(held)) as (port, hits, rejected):
        evidence["calls"] = hits
        before = start_beat(runtime, root)
        start_worker(runtime, queue, port, scan_only=True)
        wait_for(
            "pre-B Beat scan",
            lambda: any(e[2:] == ["SUCCESS", before] for e in task_events(root)),
            seconds=18,
        )
        require(not WorkWindow.objects.exists() and not hits, "run_before_first_slot")
        advance(root, held + timedelta(seconds=10))
        deferred = wait_for(
            "queued old Run",
            lambda: Run.objects.filter(job_id=old, scheduled_for=held, dispatch="sent").first(),
            seconds=22,
        )
        require(deferred.status == "pending" and not hits, "old_run_not_delayed")
        require(not Run.objects.filter(job_id=new).exists(), "new_run_before_boundary")
        require(
            ControlEvent.objects.filter(
                job_id=old, action="skipped_slots", slot_start=first, slot_end=held
            ).exists(),
            "old_skipped_slots",
        )
        stop_beat(runtime)
        runtime.stop("worker")

        evidence["stage"] = "boundary_restart"
        advance(root, boundary + timedelta(seconds=10))
        after = start_beat(runtime, root)
        require(before != after, "beat_restart_identity")
        start_worker(runtime, queue, port)
        wait_for(
            "post-B Beat scan",
            lambda: any(e[2:] == ["SUCCESS", after] for e in task_events(root)),
            seconds=20,
        )
        wait_for(
            "new Run settled",
            lambda: Run.objects.filter(
                job_id=new, scheduled_for=boundary, status="finished"
            ).first(),
            seconds=30,
        )
        wait_for(
            "old Run retry settled",
            lambda: Run.objects.filter(
                predecessor=deferred,
                job_id=old,
                window_id=deferred.window_id,
                reason="retry",
                status="finished",
            ).first(),
            seconds=30,
        )
        deferred.refresh_from_db()
        require(
            deferred.status == "failed" and deferred.error_code == "upstream_transient",
            "injected_error_lineage",
        )
        require(Job.objects.get(pk=old).next_due_at is None, "old_cursor_crossed_boundary")

        evidence["stage"] = "history_recovery"
        body = {"reason": "backfill", "target": label(gap)}
        require(
            post(RUNS, body | {"job": new, "request_id": str(uuid4())}, 409)["code"]
            == "precondition_conflict",
            "new_accepted_old_gap",
        )
        require(
            post(
                RUNS,
                body | {"job": old, "target": label(boundary), "request_id": str(uuid4())},
                409,
            )["code"]
            == "precondition_conflict",
            "old_accepted_new_slot",
        )
        recovered = post(RUNS, body | {"job": old, "request_id": str(uuid4())}, 202)
        wait_for(
            "manual eligible old gap settled",
            lambda: Run.objects.filter(
                pk=recovered["run_id"], job_id=old, status="finished"
            ).first(),
            seconds=25,
        )
        duplicate = uuid4().hex
        app.send_task(
            "collection.execute_run",
            args=[str(deferred.pk)],
            task_id=duplicate,
            queue=queue + ".run",
            retry=False,
        )
        wait_for(
            "duplicate old delivery consumed",
            lambda: any(e[1:3] == [duplicate, "SUCCESS"] for e in task_events(root)),
            seconds=15,
        )
        stop_beat(runtime)
        runtime.stop("worker")

    expected = sorted(
        [(label(held), 503), (label(held), 200), (label(boundary), 200), (label(gap), 200)]
    )
    require(not rejected and sorted(hits) == expected, "f_origin_access_count")
    require(Run.objects.count() == 4 and WorkWindow.objects.count() == 3, "ledger_counts")
    require(RunItem.objects.filter(accepted=True).count() == 21, "seven_accepted_items_per_window")
    with observe_db() as observer, observer.cursor() as cursor:
        cursor.execute("""SELECT w.target_start, array_agg(DISTINCT r.job_id)
            FROM collection_workwindow w JOIN collection_run r ON r.window_id=w.id
            GROUP BY w.target_start ORDER BY w.target_start""")
        rows = cursor.fetchall()
        require(
            rows == [(gap, [old]), (held, [old]), (boundary, [new])], "independent_window_ownership"
        )
        cursor.execute("""SELECT count(*) FROM (SELECT window_id,item_key
            FROM collection_runitem WHERE accepted GROUP BY window_id,item_key
            HAVING count(*)>1) duplicates""")
        require(cursor.fetchone()[0] == 0, "duplicate_acceptance")
    evidence["window_ownership"] = [[label(stamp), owners[0]] for stamp, owners in rows]
    evidence["beat_restart"] = {"publishers_observed": 2, "exclusive_lock_released": True}
    evidence["run_lineage"] = [
        {"job": r.job_id, "reason": r.reason, "state": r.status, "window": r.window_id}
        for r in Run.objects.order_by("created_at")
    ]
    evidence["accepted_items"] = 21
    evidence["calls"] = hits
    evidence["rejected_cross_boundary_submissions"] = 2
    evidence["stage"] = "complete"


def verify_shared_pause(root, runtime, queue, evidence, scope):
    from collection.models import CollectionControl, ControlEvent, Job, Run, RunItem, WorkWindow
    from observations.policy import clock
    from s02_support import observe_db
    from s03_runtime import LIMITS

    account, password, basis = seed()
    post = session(account, password)
    advance(root, datetime.now(UTC) - 6 * HOUR)
    first = clock().replace(minute=0, second=0, microsecond=0) + HOUR
    old_gap, boundary, after_resume = first + HOUR, first + 2 * HOUR, first + 4 * HOUR
    old, new = configure(post, account, basis, first, boundary)
    affected = basis.product.source if scope == "source" else basis.product
    other = basis.product if scope == "source" else basis.product.source
    fault = (
        {"fault_status": 403} if scope == "source" else {"fault_status": 200, "fault_body": b"{"}
    )
    code = "upstream_denied" if scope == "source" else "invalid_body"
    label = datetime.isoformat
    evidence.update(
        jobs={"old": old, "new": new},
        boundary=label(boundary),
        scope=scope,
        fixture_only=True,
        clock_injection="business clock offset; Beat scheduler uses wall time",
        injected={"slot": label(first), "http": fault["fault_status"], "code": code},
        stage="induce_pause",
    )

    def submit(job, stamp, reason, status):
        return post(
            RUNS,
            {
                "job": job,
                "target": label(stamp),
                "reason": reason,
                "request_id": str(uuid4()),
            },
            status,
        )

    def control(job, action, version):
        return post(
            f"{JOBS}{job}/",
            {
                "action": action,
                "request_id": str(uuid4()),
                "expected_version": version,
                "reason_code": "operator_pause" if action == "pause" else "operator_resume",
            },
            200,
        )

    with origin(
        {label(t) for t in (first, old_gap, boundary, after_resume)}, label(first), **fault
    ) as (port, hits, rejected):
        evidence["calls"] = hits
        evidence["paused_submissions"] = []
        publisher = start_beat(runtime, root)
        start_worker(runtime, queue, port)

        def scans():
            return sum(
                event[0] == "collection.reconcile_due" and event[2:] == ["SUCCESS", publisher]
                for event in task_events(root)
            )

        def tick(stamp, ready):
            before = scans()
            advance(root, stamp + timedelta(seconds=10))
            wait_for("Beat scan at business slot", lambda: scans() > before and ready(), seconds=22)

        def assert_no_work():
            require(
                Run.objects.count() == WorkWindow.objects.count() == 1, "rejected_slot_registered"
            )
            require(
                hits == [(label(first), fault["fault_status"])] and not rejected,
                "rejected_slot_called_origin",
            )

        def reject(job, stamp, reason):
            value = submit(job, stamp, reason, 403)
            require(value == {"code": "use_denied"}, "paused_submission_diagnostic")
            assert_no_work()
            evidence["paused_submissions"].append({"job": job, "slot": label(stamp), "http": 403})

        tick(
            first,
            lambda: Run.objects.filter(job_id=old, scheduled_for=first, status="paused").exists(),
        )
        paused = Run.objects.get(job_id=old, scheduled_for=first)
        affected.refresh_from_db()
        other.refresh_from_db()
        require(
            paused.error_code == affected.collection_pause == code and not other.collection_pause,
            "auto_pause_wrong_scope",
        )
        require(
            ControlEvent.objects.filter(
                job_id=old,
                run=paused,
                action="auto_pause",
                receipt__target_kind=scope,
                receipt__reason_code=code,
            ).exists(),
            "auto_pause_event_missing",
        )
        assert_no_work()

        evidence["stage"] = "pause_crossing"
        tick(old_gap, lambda: Job.objects.filter(pk=old, enabled=True, next_due_at=None).exists())
        reject(old, old_gap, "manual")
        tick(
            boundary,
            lambda: Job.objects.filter(
                pk=new, enabled=True, handoff_checked=True, next_due_at=after_resume
            ).exists(),
        )
        reject(new, boundary, "manual")
        reject(old, old_gap, "backfill")
        require(
            ControlEvent.objects.filter(
                job_id=new, action="skipped_slots", slot_start=boundary, slot_end=after_resume
            ).exists(),
            "successor_pause_skip_missing",
        )
        evidence["skipped_slots"] = [[old, label(old_gap)], [new, label(boundary)]]

        evidence["stage"] = "resume_shared_scope"
        old_job = Job.objects.get(pk=old)
        control(old, "pause", old_job.control_version)
        old_job.refresh_from_db()
        require(
            not old_job.enabled and old_job.pause_reason == "operator_pause",
            "old_manual_pause_missing",
        )
        version = CollectionControl.objects.get(**{scope: affected}).version
        control(new, f"resume_{scope}", version)
        affected.refresh_from_db()
        old_job.refresh_from_db()
        require(
            not affected.collection_pause
            and not old_job.enabled
            and old_job.pause_reason == "operator_pause",
            "shared_resume_cleared_independent_job_pause",
        )
        require(
            ControlEvent.objects.filter(
                job_id=new,
                action=f"resume_{scope}",
                receipt__target_kind=scope,
                receipt__before_version=version,
                receipt__after_version=version + 1,
            ).exists(),
            "shared_resume_receipt_missing",
        )
        reject(old, old_gap, "backfill")
        tick(
            after_resume,
            lambda: Run.objects.filter(
                job_id=new, scheduled_for=after_resume, status="finished"
            ).exists(),
        )
        require(not Job.objects.get(pk=old).enabled, "resumed_successor_enabled_old_job")
        evidence["old_job_pause_after_shared_resume"] = old_job.pause_reason

        evidence["stage"] = "resume_old_job"
        reservation = Run.objects.get(job_id=new, scheduled_for=after_resume).reserved_at
        require(reservation is not None, "successor_reservation_missing")
        eligible_at = reservation + timedelta(seconds=LIMITS["min_interval_seconds"])
        wait_for("source minimum interval elapsed", lambda: clock() >= eligible_at, seconds=5)
        control(old, "resume", old_job.control_version)
        recovered = submit(old, old_gap, "backfill", 202)
        wait_for(
            "old gap executed after explicit resume",
            lambda: Run.objects.filter(
                pk=recovered["run_id"], job_id=old, status="finished"
            ).exists(),
            seconds=22,
        )
        stop_beat(runtime)
        runtime.stop("worker")

    require(
        hits
        == [
            (label(first), fault["fault_status"]),
            (label(after_resume), 200),
            (label(old_gap), 200),
        ]
        and not rejected,
        "shared_pause_fixture_access_count",
    )
    require(Run.objects.count() == WorkWindow.objects.count() == 3, "shared_pause_ledger_counts")
    require(RunItem.objects.filter(accepted=True).count() == 14, "shared_pause_accepted_items")
    with observe_db() as observer, observer.cursor() as cursor:
        cursor.execute("""SELECT w.target_start, array_agg(r.job_id ORDER BY r.created_at)
            FROM collection_workwindow w JOIN collection_run r ON r.window_id=w.id
            GROUP BY w.target_start ORDER BY w.target_start""")
        rows = cursor.fetchall()
        require(
            rows == [(first, [old]), (old_gap, [old]), (after_resume, [new])],
            "shared_pause_window_ownership",
        )
    evidence.update(
        window_ownership=[[label(stamp), owners[0]] for stamp, owners in rows],
        accepted_items=14,
        beat_scans=scans(),
        stage="complete",
    )


def sandbox(env, scenario):
    root = check_environment(env)
    evidence = {"status": "FAIL", "network": check_process_network(env["S06_HOST_NET_NS"])}
    evidence["database"] = check_database(env, empty=True)
    env["S06_RUN"] = env["AQ_INSTANCE_ID"]
    runtime = Runtime(root)
    try:
        with runtime.managed():
            start_broker(runtime, env)
            evidence["broker"] = check_broker(env, empty=True)
            queue, clock_patch = setup_application(env, root)
            from django.core.management import call_command

            call_command("migrate", verbosity=0, interactive=False)
            with clock_patch:
                if scenario == "boundary":
                    verify(root, runtime, queue, evidence)
                else:
                    verify_shared_pause(
                        root, runtime, queue, evidence, scenario.removeprefix("pause-")
                    )
        evidence["status"] = "PASS"
    except (AssertionError, IsolationError, OSError, subprocess.SubprocessError) as exc:
        evidence["error"] = (
            str(exc) if isinstance(exc, (IsolationError, AssertionError)) else type(exc).__name__
        )
        if "jobs" in evidence:
            from collection.models import Job, Run

            fields = ("job_id", "status", "reason", "dispatch", "error_code", "request_state")
            evidence["run_diagnostics"] = list(Run.objects.values(*fields))
            evidence["job_diagnostics"] = list(Job.objects.values("id", "enabled", "pause_reason"))
    finally:
        evidence["lifecycle"] = runtime.lifecycle
        evidence["remaining_processes"] = len(runtime.children)
        (root / "V01" / "schedule.json").write_text(json.dumps(evidence, indent=2) + "\n")
    return int(evidence["status"] != "PASS")
