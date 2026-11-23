"""Drive the process-level S06 smoke test in the guarded network namespace."""

import json
import os
import re
import secrets
import stat
import subprocess
import sys
from contextlib import contextmanager
from datetime import timedelta
from tempfile import TemporaryFile
from threading import Lock
from time import monotonic
from wsgiref.simple_server import WSGIRequestHandler, make_server

from s01_runtime import FIXTURE_POST, OBS, ROOT, RUNS, Client, wait_for
from s06_guard import IsolationError, check_database, check_environment, require

HEALTH = "/api/v1/admin/operations/health/"
READY = "/health/ready/"
COMPONENTS = {
    "web": "http_request",
    "pg": "postgres_clock",
    "redis": "redis_ping",
    "worker": "worker_heartbeat",
    "beat": "scheduler_tick",
}


class Runtime:
    def __init__(self, root):
        self.root = root
        self.children = {}
        self.stderr = {}
        self.queue = os.environ["S06_RUN"] + ".fixture"
        self.node = "worker@" + os.environ["S06_RUN"]
        self.socket = root / "broker" / "redis.sock"
        self.stage = root / "stage"
        self.namespace = os.readlink("/proc/self/ns/net")
        self.lock = Lock()
        self.lifecycle = []

    def start(self, name, *command):
        with self.lock:
            require(name not in self.children, "duplicate_runtime_component")
            stderr = TemporaryFile()
            try:
                pid = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=stderr)
            except BaseException:
                stderr.close()
                raise
            self.children[name] = pid
            self.stderr[name] = stderr
            self.check_process(pid)
            self.lifecycle.append({"component": name, "started": True})

    def check_process(self, process):
        require(process.poll() is None, "runtime_process_exited")
        namespace = os.readlink(f"/proc/{process.pid}/ns/net")
        require(namespace == self.namespace, "runtime_process_foreign_network")

    def stop(self, name):
        process = self.children[name]
        try:
            if process.poll() is None:
                self.check_process(process)
                process.terminate()
                try:
                    process.wait(timeout=12)
                except subprocess.TimeoutExpired:
                    self.check_process(process)
                    process.kill()
                    process.wait(timeout=4)
                    raise IsolationError("runtime_process_forced_stop") from None
        finally:
            if process.poll() is not None:
                self.children.pop(name)
                entry = {"component": name, "exit": process.returncode}
                with self.stderr.pop(name) as stderr:
                    if process.returncode not in (0, -15):
                        stderr.seek(max(0, stderr.seek(0, os.SEEK_END) - 8192))
                        tail = stderr.read().decode(errors="replace")
                        entry["exception_types"] = sorted(
                            set(
                                re.findall(
                                    r"^(?:[a-z_][a-z0-9_]*\.)*([A-Z][A-Za-z0-9]*(?:Error|Exception))[:\n]",
                                    tail,
                                    re.MULTILINE,
                                )
                            )
                        )
                        entry["frames"] = re.findall(
                            r'File "[^"\n]*/((?:backend|scripts)/[a-zA-Z0-9_/]+\.py)", line (\d+)',
                            tail,
                        )[-6:]
                self.lifecycle.append(entry)

    @contextmanager
    def managed(self):
        try:
            yield self
        finally:
            errors = []
            for name in ("beat", "worker", "web", "redis"):
                if name not in self.children:
                    continue
                try:
                    self.stop(name)
                except (IsolationError, OSError, subprocess.SubprocessError) as exc:
                    errors.append({"component": name, "error": type(exc).__name__})
            if self.children or errors:
                self.lifecycle.append({"cleanup_errors": errors})
                if sys.exc_info()[0] is None:
                    raise IsolationError("runtime_cleanup_failed")

    def broker(self):
        from redis import Redis

        return Redis(unix_socket_path=str(self.socket), socket_timeout=1, socket_connect_timeout=1)

    def redis(self):
        import redis

        require(not self.socket.exists(), "redis_socket_already_exists")
        self.start(
            "redis",
            "/usr/bin/redis-server",
            "--port",
            "0",
            "--unixsocket",
            str(self.socket),
            "--unixsocketperm",
            "700",
            "--save",
            "",
            "--appendonly",
            "no",
            "--dir",
            str(self.socket.parent),
        )

        def ready():
            try:
                info = self.socket.lstat()
                require(
                    stat.S_ISSOCK(info.st_mode) and info.st_uid == os.geteuid(),
                    "redis_socket_owner",
                )
                with self.broker() as client:
                    return client.ping() and client.config_get("port").get("port") == "0"
            except (FileNotFoundError, redis.ConnectionError):
                return False

        wait_for("isolated Unix socket Redis", ready, seconds=8)
        instance = os.environ.get("AQ_INSTANCE_ID")
        if instance and os.environ.get("S06_WORK_ROOT") == str(self.root):
            require(instance == os.environ["S06_RUN"], "runtime_instance_identity")
            self.check_process(self.children["redis"])
            with self.broker() as client:
                require(
                    client.info("server")["process_id"] == self.children["redis"].pid
                    and client.dbsize() == 0,
                    "runtime_broker_identity",
                )
                require(client.set("airquality:s07:instance", instance, nx=True), "broker_marked")

    def worker(self, *, queues=None):
        from config.celery import app
        from django.conf import settings

        queues = (
            sorted({self.queue} | {opts["queue"] for opts in settings.CELERY_TASK_ROUTES.values()})
            if queues is None
            else sorted(queues)
        )
        self.start(
            "worker",
            sys.executable,
            "-m",
            "celery",
            "-A",
            "config",
            "worker",
            "-l",
            "WARNING",
            "-n",
            self.node,
            "--pool",
            "solo",
            "-Q",
            ",".join(queues),
            "--without-gossip",
            "--without-mingle",
            "--prefetch-multiplier",
            "1",
        )

        def ping():
            self.check_process(self.children["worker"])
            with app.connection_for_write() as broker:
                return app.control.ping(destination=[self.node], timeout=1, connection=broker)

        wait_for("isolated Celery Worker", ping, seconds=12)
        with app.connection_for_write() as broker:
            actual = app.control.inspect(
                destination=[self.node], timeout=2, connection=broker
            ).active_queues()
        require(
            actual and sorted(q["name"] for q in actual[self.node]) == queues,
            "worker_queue_mismatch",
        )
        return queues

    def web(self):
        port = self.stage / "web-port"
        port.unlink(missing_ok=True)
        self.start("web", sys.executable, str(ROOT / "scripts/s06_runtime.py"), "web")
        wait_for("isolated HTTP Web", port.is_file, seconds=8)
        return Client(f"http://127.0.0.1:{int(port.read_text())}")

    def beat(self):
        self.start(
            "beat",
            sys.executable,
            "-m",
            "celery",
            "-A",
            "config",
            "beat",
            "-l",
            "WARNING",
            "--pidfile",
            str(self.stage / "beat.pid"),
            "--max-interval",
            "1",
            "--schedule",
            str(self.stage / "beat-schedule"),
        )


class QuietHTTP(WSGIRequestHandler):
    def log_message(self, *_args):
        pass


def serve_web():
    root = check_environment(os.environ)
    check_database(os.environ, expected_marker=os.environ["S06_RUN"], empty=False)
    from django.core.wsgi import get_wsgi_application

    with make_server("127.0.0.1", 0, get_wsgi_application(), handler_class=QuietHTTP) as server:
        (root / "stage" / "web-port").write_text(str(server.server_port))
        server.serve_forever()


def read_health(web):
    from django.utils.dateparse import parse_datetime

    status, body = web.call(HEALTH)
    require(status == 200 and set(body) == {"components", "window"}, "health_http_contract")
    components = body["components"]
    require(set(components) == set(COMPONENTS), "health_component_names")
    for name, kind in COMPONENTS.items():
        row = components[name]
        require(row["evidence_kind"] == kind, "health_evidence_kind")
        if row["observed_at"] is None:
            require(
                row["state"] == "unknown" and row["valid_until"] is None,
                "health_missing_timestamp",
            )
        else:
            observed, until = map(parse_datetime, (row["observed_at"], row["valid_until"]))
            require(until - observed == timedelta(seconds=3), "health_invalid_expiry")
    return components


def expect_health(web, /, **expected):
    def matches():
        components = read_health(web)
        if all(
            (components[name]["state"], components[name]["reason_code"]) == state
            for name, state in expected.items()
        ):
            return components
        return None

    return wait_for("S06 component health", matches, seconds=8)


def postgres_failure(runtime, web, checks):
    from django.utils.dateparse import parse_datetime
    from observations.policy import clock

    def fresh_tick(after):
        beat = read_health(web)["beat"]
        return beat["state"] == "ok" and parse_datetime(beat["observed_at"]) >= after

    before_pause = clock()
    wait_for("Beat tick before PostgreSQL pause", lambda: fresh_tick(before_pause), seconds=8)
    stage = runtime.stage
    anonymous = Client(web.base)
    (stage / "pg-check-armed").touch(exist_ok=False)
    wait_for("owned PostgreSQL pause", (stage / "pg-paused").is_file, seconds=15)
    observed = checks["postgres_down"] = {}
    started = monotonic()
    try:
        status, body = anonymous.call(READY)
        observed["readiness_http"] = status
        require(status == 503 and body == {"web": "ok", "pg": "unavailable"}, "pg_readiness_503")
        observed["readiness"] = body
        status, body = web.call(HEALTH)
        observed["management_http"] = status
        require(status == 503 and body == {"code": "database_unavailable"}, "pg_management_503")
        observed["management_code"] = body["code"]
    finally:
        observed["seconds"] = round(monotonic() - started, 3)
        (stage / "pg-check-done").touch(exist_ok=False)
        wait_for("owned PostgreSQL restore", (stage / "pg-restored").is_file, seconds=15)
    require(anonymous.call(READY) == (200, {"web": "ok", "pg": "ok"}), "pg_readiness_recovery")
    recovered_at = clock()
    if runtime.children["beat"].poll() is not None:
        runtime.stop("beat")
        runtime.beat()
    wait_for("Beat tick after PostgreSQL recovery", lambda: fresh_tick(recovered_at), seconds=8)
    checks["postgres_recovered"] = expect_health(
        web, web=("ok", ""), pg=("ok", ""), beat=("ok", "")
    )


def verify_services(root, network):
    import django

    (root / "stage" / "collection-fixture").touch(exist_ok=False)
    django.setup()
    from accounts.models import SavedQuery, ServicePulse
    from config.celery import app
    from django.conf import settings
    from django.contrib.auth import get_user_model
    from django.contrib.auth.models import Permission
    from django.core.management import call_command
    from django.utils import timezone
    from django_celery_beat.models import IntervalSchedule, PeriodicTask
    from redis.exceptions import ConnectionError as RedisConnectionError
    from s06_runtime_cases import verify_file_disposition, verify_window_wait
    from samples.models import FixtureObservation, FixtureRun

    receipt = {
        "run": os.environ["S06_RUN"],
        "origin": "F/I",
        "network": network,
        "scope": "V15_process_health",
        "checks": {},
        "result": "FAIL",
    }
    checks = receipt["checks"]
    runtime = Runtime(root)
    try:
        call_command("migrate", verbosity=0, interactive=False)
        call_command("check", verbosity=0)
        password = secrets.token_urlsafe(24)
        user = get_user_model().objects.create_user("s06-runtime", password=password)
        user.user_permissions.add(
            Permission.objects.get(content_type__app_label="samples", codename="run_fixture"),
            Permission.objects.get(
                content_type__app_label="accounts", codename="diagnose_operations"
            ),
        )
        baseline = SavedQuery.objects.create(
            owner=user,
            synthetic_scope=True,
            title="F runtime check",
            intent={},
            baseline="f" * 64,
            baseline_until=timezone.now() - timedelta(seconds=1),
        )
        interval, _ = IntervalSchedule.objects.get_or_create(
            every=1, period=IntervalSchedule.SECONDS
        )
        task = PeriodicTask.objects.create(
            name="s06.runtime-expiry",
            interval=interval,
            task="observations.expire_research",
            queue=os.environ["S06_RUN"] + ".history",
        )
        with runtime.managed():
            runtime.redis()
            with runtime.broker() as broker:
                checks["redis_unix_ping"] = broker.ping()
                checks["redis_version"] = broker.info("server")["redis_version"]
            web = runtime.web()
            status, body = web.call(READY)
            require(status == 200 and body == {"web": "ok", "pg": "ok"}, "runtime_readiness")
            checks["readiness_http"] = status
            checks["unauthenticated_health_http"] = web.call(HEALTH)[0]
            require(checks["unauthenticated_health_http"] == 403, "health_unauthenticated")
            web.login(user.username, password)
            status, body = web.call(RUNS, FIXTURE_POST, token=False)
            require(status == 403 and body == {"code": "csrf_failed"}, "runtime_csrf")
            checks["csrf_failure_http"] = status
            healthy = ("ok", "")
            stale = ("unknown", "stale_evidence")
            absent = ("unknown", "no_evidence")
            checks["initial_components"] = expect_health(
                web, web=healthy, pg=healthy, redis=healthy, worker=absent, beat=absent
            )
            queue = settings.CELERY_TASK_ROUTES["collection.execute_run"]["queue"]
            mismatch = runtime.worker(queues=[runtime.queue])
            wrong = expect_health(web, worker=healthy, beat=absent)["worker"]
            snapshot = ServicePulse.objects.get(component="worker")
            require(
                snapshot.instance == runtime.node
                and snapshot.subscribed_queues == mismatch
                and wrong["queue_readiness"]["queue"] == queue
                and wrong["queue_readiness"]["state"] == "unavailable"
                and wrong["queue_readiness"]["reason_code"] == "queue_not_subscribed"
                and wrong["last_consumption"] is None,
                "same_name_wrong_queue_reported_ready",
            )
            app.send_task("collection.reconcile_due", queue=queue, retry=False)
            with runtime.broker() as broker:
                require(broker.llen(queue) == 1, "wrong_worker_collection_backlog")
            checks["same_node_wrong_queue"] = {
                "subscriptions": mismatch,
                "worker": wrong,
                "redis_collection_messages": 1,
            }
            runtime.stop("worker")
            checks["worker_queues"] = runtime.worker()
            checks["worker_ready_after_subscription"] = expect_health(
                web, worker=healthy, beat=absent
            )
            require(
                checks["worker_ready_after_subscription"]["worker"]["queue_readiness"]["state"]
                == "ok",
                "worker_expected_queue_unready",
            )

            def expected_consumption():
                worker = read_health(web)["worker"]
                consumed = worker["last_consumption"]
                return worker if consumed and consumed["queue"] == queue else None

            checks["same_node_expected_queue"] = wait_for(
                "expected collection queue consumption", expected_consumption, seconds=8
            )
            runtime.beat()
            wait_for(
                "Beat to deliver expired metadata sweep",
                lambda: SavedQuery.objects.filter(pk=baseline.pk, baseline="").exists(),
                seconds=14,
            )
            checks["beat_worker_pg_effect"] = True
            checks["all_components"] = expect_health(
                web, web=healthy, pg=healthy, redis=healthy, worker=healthy, beat=healthy
            )
            try:
                runtime.beat()
            except IsolationError as exc:
                require(str(exc) == "duplicate_runtime_component", "unexpected_beat_rejection")
                checks["second_beat_rejected"] = True
            else:
                raise IsolationError("second_beat_started")
            runtime.stop("beat")
            task.refresh_from_db()
            task.enabled = False
            task.save(update_fields=["enabled"])
            checks["beat_dispatch_count"] = task.total_run_count
            require(task.total_run_count > 0, "no_persisted_beat_run")
            require(
                app.control.ping(destination=[runtime.node], timeout=2), "worker_lost_with_beat"
            )
            checks["worker_alive_after_beat_stop"] = True
            checks["beat_down"] = expect_health(web, worker=healthy, beat=stale)
            runtime.beat()
            checks["beat_recovered"] = expect_health(web, worker=healthy, beat=healthy)
            runtime.stop("worker")
            checks["worker_down_components"] = expect_health(web, worker=stale, beat=healthy)
            status, body = web.call(RUNS, FIXTURE_POST)
            require(status == 202 and body.get("run_id"), "fixture_not_accepted")
            run = FixtureRun.objects.get(pk=body["run_id"])
            require(run.state == "pending", "fixture_consumed_without_worker")
            with runtime.broker() as broker:
                queued = broker.llen(runtime.queue)
            require(queued == 1, "fixture_queue_expected_one")
            checks["worker_down"] = {"http": status, "run_state": run.state, "queue_length": queued}
            runtime.worker()
            wait_for(
                "Worker to commit F",
                lambda: FixtureRun.objects.filter(pk=run.pk, state="succeeded").exists(),
                seconds=10,
            )
            status, body = web.call(OBS)
            require(
                status == 200 and body["count"] == FixtureObservation.objects.count() == 1,
                "fixture_observation_not_visible",
            )
            checks["worker_http_pg_effect"] = {"http": status, "rows": 1}

            def consumption():
                components = read_health(web)
                proof = components["worker"]
                consumed = proof.get("last_consumption")
                if proof["state"] == "ok" and consumed and consumed["queue"] == runtime.queue:
                    return components
                return None

            # Prevent a later Beat-published history task from replacing this queue's evidence.
            checks["worker_recovered"] = wait_for("F queue consumption", consumption, seconds=8)
            runtime.stop("redis")
            try:
                with runtime.broker() as broker:
                    broker.ping()
            except RedisConnectionError:
                checks["redis_failure_detected"] = True
            else:
                raise IsolationError("redis_alive_after_stop")
            checks["redis_down_components"] = expect_health(
                web, web=healthy, pg=healthy, redis=("unavailable", "probe_failed")
            )
            checks["redis_down_read_readiness"] = web.call(READY)[0]
            require(
                checks["redis_down_read_readiness"] == 200, "redis_failure_affects_pg_readiness"
            )
            status, body = web.call(OBS)
            require(status == 200 and body["count"] == 1, "redis_failure_affects_pg_read")
            checks["redis_down_http_observation"] = {"http": 200, "rows": 1}
            runtime.redis()
            with runtime.broker() as broker:
                checks["redis_restarted"] = broker.ping()
            checks["redis_recovered"] = expect_health(
                web, web=healthy, pg=healthy, redis=healthy, worker=healthy, beat=healthy
            )
            verify_window_wait(runtime, web, user, checks)
            verify_file_disposition(runtime, user, checks)
            postgres_failure(runtime, web, checks)
        receipt["result"] = "PASS"
    except (AssertionError, IsolationError, OSError, subprocess.SubprocessError) as exc:
        safe_timeout = isinstance(exc, AssertionError) and str(exc).startswith(
            "Timed out waiting for "
        )
        receipt["failure"] = (
            str(exc) if isinstance(exc, IsolationError) or safe_timeout else type(exc).__name__
        )
    finally:
        receipt["lifecycle"] = runtime.lifecycle
        receipt["remaining_children"] = len(runtime.children)
        (root / "runtime.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(f"S06 process smoke {receipt['result']}", flush=True)
    return int(receipt["result"] != "PASS")


def verify_collection_services(root, network):
    import django

    (root / "stage" / "collection-fixture").touch(exist_ok=False)
    django.setup()
    from django.contrib.auth import get_user_model
    from django.contrib.auth.models import Permission
    from django.core.management import call_command
    from s06_collection_runtime import verify_collection

    receipt = {
        "run": os.environ["S06_RUN"],
        "origin": "F/I",
        "network": network,
        "scope": "V16_collection_failure",
        "checks": {},
        "result": "FAIL",
    }
    runtime = Runtime(root)
    try:
        call_command("migrate", verbosity=0, interactive=False)
        password = secrets.token_urlsafe(24)
        user = get_user_model().objects.create_user("s06-runtime", password=password)
        user.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="accounts", codename="diagnose_operations"
            )
        )
        with runtime.managed():
            runtime.redis()
            receipt["checks"]["worker_queues"] = runtime.worker()
            runtime.beat()
            web = runtime.web().login(user.username, password)
            verify_collection(runtime, web, password, receipt["checks"])
        receipt["result"] = "PASS"
    except (AssertionError, IsolationError, OSError, subprocess.SubprocessError) as exc:
        safe_timeout = isinstance(exc, AssertionError) and str(exc).startswith(
            "Timed out waiting for "
        )
        receipt["failure"] = (
            str(exc) if isinstance(exc, IsolationError) or safe_timeout else type(exc).__name__
        )
    finally:
        receipt["lifecycle"] = runtime.lifecycle
        receipt["remaining_children"] = len(runtime.children)
        (root / "runtime.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(f"S06 V16 collection {receipt['result']}", flush=True)
    return int(receipt["result"] != "PASS")


if __name__ == "__main__":
    require(sys.argv[1:] == ["web"], "bad_runtime_command")
    serve_web()
