"""Real host processes and a Redis/PG ledger for S07 V03."""

import json
import os
import re
import signal
import socket
import subprocess
import sys
import traceback
from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
from tempfile import TemporaryFile
from uuid import uuid4

from s01_runtime import ROOT, wait_for
from s06_guard import require

BEAT = ["-m", "celery", "-A", "config", "beat", "-l", "INFO", "--max-interval", "1"]


class Publishers:
    def __init__(self, environment, stack):
        self.env = environment
        self.stack = stack

    def start(self, mode, *options, **overrides):
        publisher = uuid4().hex
        env = self.env | {"AQ_BEAT_PUBLISHER_ID": publisher} | overrides
        if mode == "main":
            env.pop("S02_STAGE_DIR")
            argv = [str(ROOT / "main.py"), "beat", *options]
        elif mode == "embedded":
            argv = ["-m", "celery", "-A", "config", "worker", "--beat"]
        elif mode == "instrumented":
            argv = [str(__file__), mode, *options]
        else:
            argv = [*BEAT, *options]
        log = self.stack.enter_context(TemporaryFile())
        child = subprocess.Popen(
            [sys.executable, *argv],
            cwd=ROOT if mode == "main" else ROOT / "backend",
            env=env,
            stdout=log,
            stderr=log,
            start_new_session=True,
        )
        self.stack.callback(self.stop, child)
        return child, publisher, log

    @staticmethod
    def stop(child, sig=signal.SIGTERM):
        if child.poll() is None:
            child.send_signal(sig)
            if sig == signal.SIGTERM:
                child.send_signal(signal.SIGCONT)
        try:
            return child.wait(timeout=14)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=3)
            raise


def backend_pid(publisher=None):
    from config.beat_guard import LOCK_KEY, LOCK_NAMESPACE
    from django.db import connection

    with connection.cursor() as cursor:
        cursor.execute(
            """SELECT a.pid, a.application_name FROM pg_locks l JOIN pg_stat_activity a USING (pid)
            WHERE l.locktype='advisory' AND l.granted AND l.mode='ExclusiveLock'
              AND l.objsubid=2 AND l.classid=%s AND l.objid=%s
              AND a.datname=current_database() AND a.usename=current_user""",
            [LOCK_NAMESPACE, LOCK_KEY],
        )
        rows = cursor.fetchall()
    require(len(rows) <= 1, "multiple_pg_beat_locks")
    if rows and (publisher is None or rows[0][1] == f"aq-beat-{publisher}"):
        return rows[0][0]
    return None


def messages(broker, queue):
    return [
        {key: message["headers"][key] for key in ("id", "periodic_task_name", "aq_beat_publisher")}
        for raw in broker.lrange(queue, 0, -1)
        if (message := json.loads(raw))
    ]


def due(name, queue):
    from django.utils import timezone
    from django_celery_beat.models import ClockedSchedule, PeriodicTask

    schedule = ClockedSchedule.objects.create(clocked_time=timezone.now() - timedelta(seconds=1))
    return PeriodicTask.objects.create(
        name=name, task="collection.reconcile_due", queue=queue, one_off=True, clocked=schedule
    )


def confirmed(slot):
    slot.refresh_from_db()
    return bool(not slot.enabled or slot.last_run_at)


def wait_message(broker, queue, name, publisher=None):
    def probe():
        found = [x for x in messages(broker, queue) if x["periodic_task_name"] == name]
        require(len(found) <= 1, "duplicate_due_slot_messages")
        if found and publisher:
            require(found[0]["aq_beat_publisher"] == publisher, "unexpected_slot_publisher")
        return found[0] if found else None

    return wait_for(f"queue message for {name}", probe, seconds=13)


def stopped(child):
    try:
        state = open(f"/proc/{child.pid}/status", encoding="ascii").read()
    except FileNotFoundError:
        return False
    return re.search(r"^State:\s*T\b", state, re.MULTILINE)


def instrumented(phase, queue):
    import django

    os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings"
    django.setup()
    from config.scheduler import GuardedProducer
    from kombu.transport.redis import Channel

    before = GuardedProducer.publish_to_broker
    put = Channel._put

    def gate(self, message, **kwargs):
        if (
            phase in {"before_verify", "term_before_send", "kill_before_send"}
            and kwargs.get("routing_key") == queue
        ):
            os.kill(os.getpid(), signal.SIGSTOP)
        return before(self, message, **kwargs)

    def publish(self, route, message, **kwargs):
        if phase == "inside_transport" and route == queue:
            # This has passed the final PG check and entered Kombu. The old process
            # still owns flock throughout the window of uncertain delivery.
            os.kill(os.getpid(), signal.SIGSTOP)
        result = put(self, route, message, **kwargs)
        if phase == "after_put" and route == queue:
            os.kill(os.getpid(), signal.SIGSTOP)
        return result

    GuardedProducer.publish_to_broker = gate
    Channel._put = publish
    from config.celery import app

    app.start(BEAT[4:])


def verify_business_window(root, environment, broker, queue, captured):
    """Allow the exact queued IDs to reach a real Worker and a loopback F origin."""
    from datetime import date
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread

    from collection.models import Job, Run, RunItem, WorkWindow
    from django.contrib.auth import get_user_model
    from django.contrib.auth.models import Permission
    from django.utils import timezone
    from observations.models import City, CityIdentifier, Source
    from psycopg.types.range import TimestamptzRange
    from s02_support import START, allow, create_product, observe_db
    from s03_runtime import LIMITS, city_packet

    slot = timezone.now().replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)
    user = get_user_model().objects.create_user("s07-synthetic-beat")
    user.user_permissions.add(
        *Permission.objects.filter(
            content_type__app_label="observations",
            codename__in=("manage_grants", "manage_collection", "run_collection"),
        )
    )
    source = Source.objects.create(
        namespace="s03.fixture", publisher="S07 F", collection_limits=LIMITS
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
    job = Job.objects.create(
        product=product,
        city=city,
        executor=user,
        profile="f-exact-v1",
        expected_codes=["123"],
        roster_basis="F",
        enabled=True,
        next_due_at=slot,
        cadence_seconds=86_400,
    )
    for operation in ("acquire_online", "retain_normalized", "process_local"):
        options = {"collection_limits": LIMITS} if operation == "acquire_online" else {}
        allow(
            user,
            product,
            operation,
            data_from=date.min,
            data_until=date.max,
            fields=["*"],
            **options,
        )
    packet = json.dumps(city_packet(slot, "123")).encode()
    requests = []

    class Origin(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append(self.headers.get("X-S03-Target"))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(packet)))
            self.end_headers()
            self.wfile.write(packet)

        def log_message(self, *_args):
            pass

    with ExitStack() as stack:
        origin = stack.enter_context(ThreadingHTTPServer(("127.0.0.1", 0), Origin))
        thread = Thread(target=origin.serve_forever)
        thread.start()
        stack.callback(thread.join, 3)
        stack.callback(origin.shutdown)
        report = root / "V03" / "consumed.jsonl"
        log = stack.enter_context(TemporaryFile())
        worker = subprocess.Popen(
            [sys.executable, str(__file__), "worker", str(origin.server_port), queue, str(report)],
            cwd=ROOT / "backend",
            env=environment,
            stdout=log,
            stderr=log,
        )
        stack.callback(Publishers.stop, worker)

        def settled():
            require(worker.poll() is None, "singleton_real_worker_exited")
            job.refresh_from_db()
            require(job.enabled, "singleton_f_job_paused_" + job.pause_reason)
            current = Run.objects.filter(job=job).exclude(status__in=("pending", "running")).first()
            if current:
                require(
                    current.status == "finished" and current.result == "new",
                    "singleton_f_run_" + (current.error_code or current.result or current.status),
                )
            return current

        run = wait_for("S07 F business window", settled, seconds=14)
        planned = {item["id"] for item in captured}

        def consumed():
            if not report.is_file():
                return False
            seen = report.read_text().splitlines()
            return sorted(seen) if set(seen) >= planned else False

        seen = wait_for("real Worker receipts for Beat task IDs", consumed, seconds=14)
        require(set(seen) == planned and len(seen) == len(planned), "redis_worker_disagreement")
        require(broker.llen(queue) == 0, "singleton_queue_not_consumed")
        with observe_db() as db:
            summary = db.execute(
                f"""SELECT r.id, r.scheduled_for,
                     (SELECT count(*) FROM {WorkWindow._meta.db_table} w WHERE w.product_id=%s),
                     (SELECT count(*) FROM {RunItem._meta.db_table} i WHERE i.run_id=r.id AND i.accepted)
                     FROM {Run._meta.db_table} r WHERE r.job_id=%s""",
                (product.pk, job.pk),
            ).fetchall()
        require(summary == [(run.pk, slot, 1, 7)] and len(requests) == 1, "pg_business_window")
        return {
            "job": job.pk,
            "scheduled_for": slot.isoformat(),
            "accepted_cells": 7,
            "run": str(run.pk),
            "window": run.window_id,
            "fixture_requests": len(requests),
            "consumed_ids": seen,
        }


def worker(port, queue, report):
    from pathlib import Path

    import django
    from celery.signals import task_postrun

    os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings"
    django.setup()
    from collection.adapters import transport
    from config.celery import app
    from django.conf import settings

    transport.ORIGIN = f"http://127.0.0.1:{int(port)}"
    settings.S03_FIXTURE_HTTP = True

    @task_postrun.connect(weak=False)
    def consumed(task_id, task, state, **kwargs):
        if task.name == "collection.reconcile_due" and state == "SUCCESS":
            with Path(report).open("a") as handle:
                handle.write(task_id + "\n")

    app.start(["worker", "-l", "WARNING", "-P", "solo", "-Q", f"{queue},s03.collection"])


def verify(root, checks, *, compose=False):
    import django
    from django.core.management import call_command
    from django.db import connection
    from s07_host_guard import broker_client

    environment = dict(os.environ)
    if not environment.get("AQ_BEAT_LOCK_FILE"):
        environment["AQ_BEAT_LOCK_FILE"] = str(root / "V02" / "beat.lock")
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    django.setup()
    path = root / "V03"
    path.mkdir(mode=0o700, exist_ok=True)
    result = {
        "run": environment["AQ_INSTANCE_ID"],
        "status": "FAIL",
        "origin": "F/I",
        "started_utc": datetime.now(UTC).isoformat(),
        "checks": {},
    }
    observed = result["checks"]
    try:
        call_command("migrate", verbosity=0, interactive=False)
        queue = environment["AQ_INSTANCE_ID"] + ".due"
        with ExitStack() as stack, broker_client(environment) as broker:
            pub = Publishers(environment, stack)
            from config.beat_guard import LOCK_KEY, LOCK_NAMESPACE

            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_lock(%s, %s)", [LOCK_NAMESPACE, LOCK_KEY])
                try:
                    child, _, log = pub.start("cli")
                    require(child.wait(timeout=8) != 0, "missing_pg_lock_rejection")
                    log.seek(0)
                    require(b"beat_database_busy" in log.read(), "wrong_pg_lock_diagnostic")
                    observed["independent_pg_denial"] = child.returncode
                finally:
                    cursor.execute("SELECT pg_advisory_unlock(%s, %s)", [LOCK_NAMESPACE, LOCK_KEY])
            for mode in ("cli", "main"):
                owner, tag, _ = pub.start(mode)
                # main.py supplies its own unique publisher tag.
                wait_for(
                    f"{mode} PG Beat lock",
                    lambda tag=(tag if mode == "cli" else None): backend_pid(tag),
                    seconds=14,
                )
                for other in ("cli", "main"):
                    competitor, _, _ = pub.start(other)
                    require(competitor.wait(timeout=14) != 0, "competing_host_publisher_admitted")
                    observed[f"{mode}_holds_{other}_rejected"] = competitor.returncode
                slot = due(f"s07.host.{mode}", queue)
                observed[slot.name] = wait_message(
                    broker, queue, slot.name, tag if mode == "cli" else None
                )
                wait_for(
                    "confirmed schedule state",
                    lambda slot=slot: confirmed(slot),
                    seconds=8,
                )
                require(pub.stop(owner) == 0 and backend_pid() is None, "host_beat_did_not_release")
            bad, _, log = pub.start("cli", "--scheduler", "celery.beat:PersistentScheduler")
            require(bad.wait(timeout=8) != 0, "unexpected_scheduler_started")
            log.seek(0)
            require(b"beat_scheduler_not_supported" in log.read(), "wrong_scheduler_diagnostic")
            observed["alternate_scheduler"] = bad.returncode
            bad, _, log = pub.start("embedded")
            require(bad.wait(timeout=8) != 0, "unexpected_embedded_scheduler_started")
            log.seek(0)
            require(
                b"worker_embedded_beat_not_supported" in log.read(), "wrong_embedded_diagnostic"
            )
            observed["embedded_scheduler"] = bad.returncode

            for phase, signal_only in (
                ("before_verify", None),
                ("inside_transport", None),
                ("after_put", None),
                ("term_before_send", signal.SIGTERM),
                ("kill_before_send", signal.SIGKILL),
            ):
                old, tag, _ = pub.start("instrumented", phase, queue)
                pid = wait_for(
                    "injected old publisher PG lock", lambda tag=tag: backend_pid(tag), seconds=12
                )
                slot = due(f"s07.{phase}", queue)
                wait_for("paused publishing process", lambda old=old: stopped(old), seconds=12)
                if signal_only is None:
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT pg_terminate_backend(%s)", [pid])
                        require(cursor.fetchone() == (True,), "pg_lease_termination")
                    wait_for("old PG lease gone", lambda: backend_pid() is None, seconds=5)
                    contender, _, log = pub.start("cli")
                    require(contender.wait(timeout=8) != 0, "stopped_old_publisher_was_taken_over")
                    log.seek(0)
                    require(b"beat_file_busy" in log.read(), "wrong_takeover_denial")
                    denied = contender.returncode
                else:
                    denied = None
                existing = [
                    entry
                    for entry in messages(broker, queue)
                    if entry["periodic_task_name"] == slot.name
                ]
                require(bool(existing) == (phase == "after_put"), "incorrect_publish_boundary")
                if signal_only is None:
                    old.send_signal(signal.SIGCONT)
                    require(old.wait(timeout=12) != 0, "lost_session_publisher_continued")
                else:
                    expected = 0 if signal_only == signal.SIGTERM else -9
                    require(pub.stop(old, signal_only) == expected, "paused_publisher_signal_exit")
                    wait_for("old PG lease released", lambda: backend_pid() is None, seconds=5)
                queued_before_resume = len(existing)
                existing = [
                    entry
                    for entry in messages(broker, queue)
                    if entry["periodic_task_name"] == slot.name
                ]
                if phase in {"inside_transport", "after_put"}:
                    require(
                        len(existing) == 1 and existing[0]["aq_beat_publisher"] == tag,
                        "unknown_original_task",
                    )
                else:
                    require(not existing, "loss_before_transport_published")
                slot.refresh_from_db()
                require(
                    slot.last_run_at is None and slot.total_run_count == 0,
                    "unsent_reservation_retired",
                )
                if existing:
                    # The queue proves the uncertain ID arrived. Keep it for the Worker and
                    # explicitly clear this test slot before permitting the operator's takeover.
                    slot.enabled = False
                    slot.save()
                first, first_tag, _ = pub.start("cli")
                second, second_tag, _ = pub.start("cli")
                selected = wait_for(
                    "one published successor or no PG contender",
                    lambda first=first_tag, second=second_tag: (
                        backend_pid(first) or backend_pid(second)
                    ),
                    seconds=12,
                )
                require(selected is not None, "no_successor")
                winner, winner_tag, loser = (
                    (first, first_tag, second)
                    if backend_pid(first_tag)
                    else (second, second_tag, first)
                )
                require(loser.wait(timeout=10) != 0, "multiple_successors")
                disposition = (
                    existing[0] if existing else wait_message(broker, queue, slot.name, winner_tag)
                )
                if not existing:
                    wait_for(
                        "confirmed successor schedule state",
                        lambda slot=slot: confirmed(slot),
                        seconds=8,
                    )
                exit_code = pub.stop(
                    winner, signal.SIGKILL if phase == "after_put" else signal.SIGTERM
                )
                wait_for(
                    "successor release of PG advisory lock",
                    lambda: backend_pid() is None,
                    seconds=5,
                )
                observed[phase] = {
                    "old_publisher": tag,
                    "old_exit": old.returncode,
                    "contender_exit": denied,
                    "successor": winner_tag,
                    "successor_exit": exit_code,
                    "message": disposition,
                    "in_flight": bool(existing),
                    "redis_count_before_resume": queued_before_resume,
                    "redis_count_after_exit": len(existing),
                }
            if compose:
                observed["compose"] = verify_compose(root, pub, broker, queue)
            captured = messages(broker, queue)
            require(len(captured) == (9 if compose else 7), "unexpected_due_message_count")
            observed["business_window"] = verify_business_window(
                root, environment, broker, queue, captured
            )
            result["status"] = "PASS"
    except Exception as error:
        result["failure"] = (
            str(error)
            if type(error).__name__ == "IsolationError"
            or isinstance(error, AssertionError)
            and str(error).startswith("Timed out waiting for ")
            else type(error).__name__
        )
        result["frames"] = [
            [frame.filename.rsplit("/", 1)[-1], frame.lineno, frame.name]
            for frame in traceback.extract_tb(error.__traceback__)[-5:]
        ]
        raise
    finally:
        (root / "launch-beat.lock").unlink(missing_ok=True)
        result["finished_utc"] = datetime.now(UTC).isoformat()
        checks["v03_compose" if compose else "v03_host_subset"] = result["status"]
        (path / ("host-compose.json" if compose else "host.json")).write_text(
            json.dumps(result, indent=2) + "\n"
        )


def verify_compose(root, local, broker, queue):
    def request(operation, name):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as peer:
            peer.settimeout(40)
            peer.connect(str(root / "rpc"))
            peer.sendall(json.dumps([operation, name]).encode() + b"\n")
            with peer.makefile("rb") as stream:
                response = json.loads(stream.readline(1024))
        require("error" not in response, response.get("error", "singleton_compose_bridge"))
        return response["value"]

    def reject(mode, code):
        name, publisher = request("start", mode)
        state = wait_for(
            "Compose Beat startup rejection", lambda: request("state", name), seconds=12
        )
        require(state["exit"] != 0 and state["error"] == code, "singleton_wrong_compose_denial")
        return state | {"publisher": publisher}

    results = {}
    for mode in ("base", "formal"):
        name, publisher = request("start", mode)
        pid = wait_for(
            f"{mode} real advisory lock", lambda tag=publisher: backend_pid(tag), seconds=12
        )
        denials = {other: reject(other, "beat_file_busy") for other in ("base", "formal")}
        for other in ("main", "cli"):
            child, rejected_tag, log = local.start(other)
            require(child.wait(timeout=14) != 0, "compose_host_bidirectional_flock")
            if other == "cli":
                log.seek(0)
                require(b"beat_file_busy" in log.read(), "compose_host_expected_flock_denial")
            denials[other] = {"exit": child.returncode, "publisher": rejected_tag}
        slot = due(f"s07.compose.{mode}", queue)
        queued = wait_message(broker, queue, slot.name, publisher)
        wait_for("confirmed Compose reservation", lambda slot=slot: confirmed(slot), seconds=8)
        require(request("stop", name)["exit"] == 0, "compose_beat_cleanup")
        wait_for("Compose advisory lock released", lambda: backend_pid() is None, seconds=5)
        results[mode] = {"pg_pid": pid, "message": queued, "rejections": denials}

    for mode in ("main", "cli"):
        child, _, _ = local.start(mode)
        wait_for(f"{mode} holding Beat", backend_pid, seconds=12)
        results[f"{mode}_holder"] = {
            other: reject(other, "beat_file_busy") for other in ("base", "formal")
        }
        require(local.stop(child) == 0, "singleton_native_stop")
        wait_for("native holder released", lambda: backend_pid() is None, seconds=5)

    results["same_target_foreign_file"] = reject("unknown-formal", "beat_lock_identity")
    child, publisher, log = local.start("cli")
    wait_for("native publisher before lock replacement", lambda: backend_pid(publisher), seconds=12)
    identities = request("replace", "registered")
    require(child.wait(timeout=10) != 0, "original_publisher_ignored_lock_replacement")
    log.seek(0)
    require(b"beat_lock_identity" in log.read(), "expected_replaced_file_diagnostic")
    results["replaced_file"] = {
        "inodes": identities,
        "exit": child.returncode,
        "successor": reject("formal", "beat_lock_identity"),
    }
    return results


if __name__ == "__main__":
    # Internal subprocess entry; the same guarded B snapshot and private socket root apply.
    from s07_host_guard import check_database, check_process_network

    require(sys.argv[1] in {"instrumented", "worker"}, "invalid_singleton_subprocess")
    check_process_network(os.environ["S06_HOST_NET_NS"])
    check_database(os.environ, empty=False)
    (instrumented if sys.argv[1] == "instrumented" else worker)(*sys.argv[2:])
