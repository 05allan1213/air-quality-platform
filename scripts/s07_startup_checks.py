"""V02 real host startup checks. Run through the guarded S07 B runner."""

import json
import os
import re
import secrets
import signal
import socket
import subprocess
import sys
import tempfile
import traceback
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, build_opener

import psycopg
from psycopg import sql
from s01_runtime import FIXTURE_POST, OBS, ROOT, RUNS, Client, wait_for
from s06_guard import IsolationError, require
from s07_host_guard import check_broker, check_database, check_environment, check_process_network

START = re.compile(r"^\[s07\] started ([a-z0-9_]+) pid=(\d+)$", re.MULTILINE)
SAFE_EVENT = re.compile(r"^\[s07\] [A-Za-z0-9_=-]+(?: [A-Za-z0-9_=-]+)*$", re.MULTILINE)
CSS = re.compile(rb'href="(/static/admin/css/[a-zA-Z0-9_.-]+\.css)"')


def iso_now():
    return datetime.now(UTC).isoformat()


def verify_lock(env):
    from config.beat_guard import lock_identity, lock_mount

    lock = Path(env["AQ_BEAT_LOCK_FILE"])
    require(lock_mount(lock)[0] == lock and "ro" in lock_mount(lock)[2], "lock_not_readonly")
    with connect(env) as db:
        row = db.execute(
            "SELECT current_setting('airquality.beat_lock'), current_setting('airquality.beat_mode')"
        ).fetchone()
        require(row == (lock_identity(lock.stat()), "host"), "lock_registration")


def connect(env, *, write=False):
    return psycopg.connect(
        host=env["POSTGRES_HOST"],
        port=env["POSTGRES_PORT"],
        dbname=env["POSTGRES_DB"],
        user=env["POSTGRES_USER"],
        password=env["POSTGRES_PASSWORD"],
        connect_timeout=2,
        options="-c statement_timeout=2500"
        + ("" if write else " -c default_transaction_read_only=on"),
        autocommit=True,
    )


def prepare(env, work):
    manage = [sys.executable, "-B", str(ROOT / "backend/manage.py")]
    code = (
        "import os; "
        "from django.contrib.auth import get_user_model; "
        "from django.contrib.auth.models import Permission; "
        "actor = get_user_model().objects.create_user(username='fixture_operator', "
        "password=os.environ['V02_FIXTURE_PASSWORD']); "
        "actor.user_permissions.add(Permission.objects.get(codename='run_fixture'))"
    )
    password = secrets.token_hex(24)
    for args in (
        ["migrate", "--noinput"],
        ["collectstatic", "--noinput"],
        ["shell", "--command", code],
    ):
        with tempfile.TemporaryFile(dir=work) as output:
            result = subprocess.run(
                [*manage, *args],
                env=env | {"V02_FIXTURE_PASSWORD": password},
                stdout=output,
                stderr=subprocess.STDOUT,
                timeout=45,
            )
            require(result.returncode == 0, f"prepare_{args[0]}_exit_{result.returncode}")
    return password


def http(path, base):
    opener = build_opener(ProxyHandler({}))
    try:
        with opener.open(base + path, timeout=2) as response:
            return response.status, response.headers.get_content_type(), response.read()
    except HTTPError as error:
        with error:
            return error.code, error.headers.get_content_type(), error.read()
    except (TimeoutError, URLError):
        return None, None, b""


class StartupChecks:
    def __init__(self, root, work, env, receipt):
        self.root = root
        self.work = work
        self.env = env
        self.receipt = receipt
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            self.port = listener.getsockname()[1]
        self.base = f"http://127.0.0.1:{self.port}"
        self.env["WEB_PORT"] = str(self.port)

    @contextmanager
    def launch(self, label, *args, env=None, inject=None):
        from config.host_processes import group_alive

        command = [sys.executable, "-B", str(ROOT / "main.py"), *args]
        if inject:
            command = [sys.executable, "-B", __file__, inject, *args]
        entry = {
            "case": label,
            "argv": ["python", "main.py", *map(str, args)]
            if "--env-file" not in args
            else ["python", "main.py", "check", "--env-file", "<0600 host config>"],
            "start_utc": iso_now(),
        }
        if inject:
            entry["injected"] = inject
        self.receipt["cases"].append(entry)
        with tempfile.TemporaryFile(dir=self.work) as output:
            proc = subprocess.Popen(
                command,
                cwd=ROOT,
                env=self.env if env is None else env,
                stdout=output,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            entry["pid"] = proc.pid

            def events():
                # pread leaves the append position of the child's stdout intact.
                return os.pread(output.fileno(), 262144, 0).decode(errors="replace")

            try:
                yield proc, events, entry
                entry["status"] = "PASS"
            finally:
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=22)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait(timeout=5)
                entry["exit"] = proc.returncode
                entry["finish_utc"] = iso_now()
                log = events()
                entry["events"] = SAFE_EVENT.findall(log)
                entry["exceptions"] = re.findall(r"^([A-Za-z]+(?:Error|Exception)):", log, re.M)
                entry["frames"] = re.findall(
                    r'File "[^"\n]*/((?:(?:backend|scripts)/[a-z0-9_/]+|main)\.py)", line (\d+)',
                    log,
                )[-8:]
                groups = {int(pid) for _, pid in START.findall(log)}
                entry["remaining_groups"] = sorted(pid for pid in groups if group_alive(pid))
                if entry["remaining_groups"]:
                    entry["status"] = "FAIL"
                    for group in entry["remaining_groups"]:
                        # The isolated PID namespace and real launcher records bound ownership.
                        os.killpg(group, signal.SIGKILL)
                    raise IsolationError("host_children_remaining")

    def negative(self, label, reason, *args, env=None, inject=None):
        with self.launch(label, *args, env=env, inject=inject) as (proc, events, entry):
            require(proc.wait(timeout=28) == 1, label + "_exit")
            require(f"[s07] {reason}" in events(), label + "_diagnostic")
            require("[s07] ready " not in events(), label + "_became_ready")
            entry["reason"] = reason

    def wait_ready(self, proc, events, entry, components):
        def observed():
            require(proc.poll() is None, entry["case"] + "_premature_exit")
            return all(f"[s07] ready {name}\n" in events() for name in components)

        wait_for(entry["case"] + " ready", observed, 27)
        entry["ready_utc"] = iso_now()
        entry["components"] = list(components)

    def terminate(self, proc, sig, entry):
        proc.send_signal(sig)
        require(proc.wait(timeout=18) == 0, entry["case"] + "_stop_exit")
        entry["stop_signal"] = sig.name

    def http_checks(self, db, entry):
        entry["ready_http"] = http("/health/ready/", self.base)[0]
        require(entry["ready_http"] == 200, "http_ready")
        status, _, html = http("/admin/login/", self.base)
        stylesheet = CSS.search(html)
        require(status == 200 and stylesheet, "admin_css_reference")
        path = stylesheet[1].decode("ascii")
        status, media, data = http(path, self.base)
        require(status == 200 and media == "text/css" and len(data) > 100, "admin_css_http")
        entry["admin_css"] = {"path": path, "media": media, "status": status, "bytes": len(data)}
        for path in (
            "/static/stage-private-check.txt",
            "/static/backup-private-check.txt",
            "/static/../stage/stage-private-check.txt",
            "/static/%2e%2e/backup/backup-private-check.txt",
        ):
            status, _, body = http(path, self.base)
            require(status in {400, 404} and b"fixture-private-check" not in body, "private_static")
            entry.setdefault("private_static", []).append(status)
        limit = sql.SQL("ALTER DATABASE {} CONNECTION LIMIT {}").format(
            sql.Identifier(self.env["POSTGRES_DB"]), sql.Literal(0)
        )
        db.execute(limit)
        try:
            entry["db_outage_http"] = http("/health/ready/", self.base)[0]
            require(entry["db_outage_http"] == 503, "readiness_during_db_outage")
        finally:
            db.execute(
                sql.SQL("ALTER DATABASE {} CONNECTION LIMIT -1").format(
                    sql.Identifier(self.env["POSTGRES_DB"])
                )
            )
        require(http("/health/ready/", self.base)[0] == 200, "readiness_recovered")

    def task(self, db, password, entry):
        client = Client(self.base).login("fixture_operator", password)
        status, result = client.call(RUNS, FIXTURE_POST)
        require(status == 202, "fixture_session_submit")
        run = result["run_id"]

        def done():
            row = db.execute(
                """SELECT r.state, r.dispatch, r.actor_id, o.product, o.value, o.unit,
                          o.synthetic, r.started_at, r.finished_at
                   FROM samples_fixturerun r LEFT JOIN samples_fixtureobservation o
                   ON r.observation_id = o.id WHERE r.id = %s""",
                (run,),
            ).fetchone()
            require(row is not None, "fixture_run_missing")
            return row if row[0] != "pending" else None

        row = wait_for("real Worker fixture committed", done, 12)
        require(
            row[:2] == ("succeeded", "sent")
            and row[3:7] == ("F", 1.25, "mg/m³", True)
            and row[7] <= row[8],
            "fixture_sql_reconciliation",
        )
        status, _ = client.call(OBS)
        require(status == 200, "fixture_session_read")
        entry["fixture"] = {
            "run_id": run,
            "state": row[0],
            "dispatch": row[1],
            "product": row[3],
            "value": str(row[4]),
            "unit": row[5],
            "synthetic": row[6],
            "finished_at": row[8].isoformat(),
            "get_status": status,
        }

    def pulse(self, db, entry):
        from config.beat_guard import LOCK_KEY, LOCK_NAMESPACE

        row = db.execute(
            "SELECT instance, observed_at, subscribed_queues FROM accounts_servicepulse "
            "WHERE component = 'worker'"
        ).fetchone()
        require(row and set(row[2]) == {"s01.fixture", "s02.history", "s03.collection"}, "queues")
        entry["worker"] = {"pulse": row[1].isoformat(), "queues": row[2]}
        beat = db.execute(
            """SELECT p.instance, p.observed_at, a.pid FROM accounts_servicepulse AS p
               JOIN pg_stat_activity AS a ON a.application_name = 'aq-beat-' || p.instance
               JOIN pg_locks AS l ON l.pid = a.pid
               WHERE p.component = 'beat' AND a.datname = current_database()
                 AND l.locktype = 'advisory' AND l.mode = 'ExclusiveLock' AND l.granted
                 AND l.classid = %s AND l.objid = %s AND l.objsubid = 2""",
            (LOCK_NAMESPACE, LOCK_KEY),
        ).fetchone()
        require(beat is not None, "beat_missing_pg_lock_and_pulse")
        entry["beat"] = {"publisher": beat[0], "tick": beat[1].isoformat(), "pg_pid": beat[2]}

    def run(self, password):
        from config.host_config import CONFIG_KEYS

        self.negative(
            "wrong_database",
            "database_identity",
            "check",
            env=self.env | {"POSTGRES_DB": "postgres"},
        )
        short = {key: value for key, value in self.env.items() if key != "DJANGO_SECRET_KEY"}
        self.negative("missing_secret", "missing_DJANGO_SECRET_KEY", "check", env=short)
        self.negative(
            "regular_staging_dir",
            "stage_storage_unavailable",
            "check",
            env=self.env | {"S02_STAGE_DIR": str(self.root / "stage")},
        )
        self.negative(
            "broker_static_overlap",
            "overlapping_private_and_static_storage",
            "web",
            env=self.env | {"AQ_STATIC_ROOT": str(self.root / "broker")},
        )
        conf = self.work / "backend.env"
        conf.touch(mode=0o600, exist_ok=False)
        conf.write_text("".join(f"{k}={v}\n" for k, v in self.env.items() if k in CONFIG_KEYS))
        without_config = {k: v for k, v in self.env.items() if k not in CONFIG_KEYS}
        with self.launch(
            "explicit_config_check", "check", "--env-file", conf, env=without_config
        ) as (proc, events, _):
            require(proc.wait(timeout=8) == 0 and "migrations ready" in events(), "check_ready")
        conf.unlink()
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", self.port))
            listener.listen()
            self.negative("wrong_port", "web_port_unavailable", "web")
            self.negative("dev_wrong_port", "web_port_unavailable", "web", "--dev")
        for command in ("s02_dispose_due", "s03_reconcile_due"):
            self.negative(command + "_failure", command + "_exited_1", "worker", inject=command)
        self.negative("wrong_queue", "worker_queue_mismatch", "worker", inject="wrong_queue")
        with connect(self.env, write=True) as db:
            with self.launch("default_all") as (proc, events, entry):
                self.wait_ready(proc, events, entry, ("web", "worker", "beat"))
                self.pulse(db, entry)
                self.http_checks(db, entry)
                self.task(db, password, entry)
                self.terminate(proc, signal.SIGTERM, entry)
        for role in ("beat", "worker"):
            with self.launch("standalone_" + role, role) as (proc, events, entry):
                self.wait_ready(proc, events, entry, (role,))
                self.terminate(proc, signal.SIGINT, entry)
        sock = self.root / "broker" / "redis.sock"
        temp = sock.with_suffix(".held")
        sock.rename(temp)
        try:
            with self.launch("standalone_web_without_broker", "web") as (proc, events, entry):
                self.wait_ready(proc, events, entry, ("web",))
                self.terminate(proc, signal.SIGTERM, entry)
        finally:
            temp.rename(sock)
        with self.launch("standalone_dev", "web", "--dev") as (proc, events, entry):
            self.wait_ready(proc, events, entry, ("web",))
            self.terminate(proc, signal.SIGINT, entry)
        with self.launch("web_child_failure") as (proc, events, entry):
            self.wait_ready(proc, events, entry, ("web", "worker", "beat"))
            web = int(dict(START.findall(events()))["web"])
            os.kill(web, signal.SIGKILL)
            require(proc.wait(timeout=18) == 1, "failed_child_parent_exit")
            require("[s07] web_exited_-9" in events(), "failed_child_no_diagnostic")
        self.parent_loss()

    def parent_loss(self):
        from config.host_processes import group_alive, process_alive

        with self.launch("parent_sigkill", "beat") as (proc, events, entry):
            self.wait_ready(proc, events, entry, ("beat",))
            child = int(dict(START.findall(events()))["beat"])
            [[pid, start]] = json.loads((self.root / "launch-beat.lock").read_text())
            require(pid == child and process_alive(pid, start), "beat_identity")
            proc.kill()
            require(proc.wait(timeout=5) == -signal.SIGKILL, "parent_kill")
            try:
                self.negative("residual_beat", "beat_already_supervised", "beat")
                require(process_alive(pid, start), "foreign_cleanup")
            finally:
                if process_alive(pid, start):
                    os.kill(pid, signal.SIGTERM)
                wait_for("identified Beat exit", lambda: not group_alive(pid), 15)
        with self.launch("restart_after_parent_loss", "beat") as (proc, events, entry):
            self.wait_ready(proc, events, entry, ("beat",))
            self.terminate(proc, signal.SIGTERM, entry)


def verify(root, guards):
    require(guards.get("status") == "PASS", "v01_required")
    env = os.environ.copy()
    require(check_environment(env) == root, "v01_root")
    check_process_network(env["S06_HOST_NET_NS"])
    check_database(env, empty=True)
    check_broker(env, empty=True)
    result = root / "V02" / "startup.json"
    report = {"run": env["AQ_INSTANCE_ID"], "origin": "F/I", "status": "FAIL", "cases": []}
    env.pop("S02_STAGE_DIR")
    env["AQ_BEAT_LOCK_FILE"] = str(root / "V02" / "beat.lock")
    stage = root / "stage" / "stage-private-check.txt"
    locks = [root / f"launch-{role}.lock" for role in ("beat", "worker", "web")]
    try:
        with tempfile.TemporaryDirectory(prefix="startup-", dir=root) as temporary:
            work = Path(temporary)
            env["AQ_STATIC_ROOT"] = str(work / "public-static")
            env["S06_BACKUP_DIR"] = str(work / "backup")
            (work / "backup").mkdir(mode=0o700)
            (work / "backup" / "backup-private-check.txt").write_text("fixture-private-check")
            stage.write_text("fixture-private-check")
            suite = StartupChecks(root, work, env, report)
            suite.negative("pending_migrations", "migrations_pending_run_explicit_migrate", "check")
            verify_lock(env)
            password = prepare(env, work)
            suite.run(password)
        report["status"] = "PASS"
    except (
        IsolationError,
        AssertionError,
        OSError,
        ValueError,
        subprocess.SubprocessError,
        psycopg.Error,
    ) as error:
        report["error"] = str(error) if isinstance(error, IsolationError) else type(error).__name__
        if isinstance(error, psycopg.Error):
            report["sqlstate"] = error.sqlstate
        report["frames"] = [
            [Path(frame.filename).name, frame.lineno, frame.name]
            for frame in traceback.extract_tb(error.__traceback__)
        ]
        raise
    finally:
        for lock in locks:
            lock.unlink(missing_ok=True)
        stage.unlink(missing_ok=True)
        result.write_text(json.dumps(report, indent=2) + "\n")


def injected_main(fault, args):
    """Run the same launcher with one failing real subprocess configuration."""
    require(fault in {"wrong_queue", "s02_dispose_due", "s03_reconcile_due"}, "unknown_fault")
    require(args == ["worker"], "invalid_fault_target")
    original_environment = os.environ.copy()
    env = original_environment | {"S02_STAGE_DIR": original_environment["AQ_HOST_ROOT"] + "/stage"}
    env.pop("S06_BACKUP_DIR", None)
    check_environment(env)
    check_process_network(env["S06_HOST_NET_NS"])
    check_database(env, empty=False)
    check_broker(env, empty=False)
    from config.host_processes import Children

    # Importing config installs the Celery app. The launcher starts from its original env.
    os.environ.clear()
    os.environ.update(original_environment)
    start = Children.start

    def injected_start(self, component, argv, **options):
        if fault == component and component in {"s02_dispose_due", "s03_reconcile_due"}:
            options["extra_env"] = {"POSTGRES_PASSWORD": secrets.token_hex(24)}
        if component == "worker" and fault == "wrong_queue":
            argv = list(argv)
            argv[argv.index("-Q") + 1] = "s01.fixture,s02.history"
        return start(self, component, argv, **options)

    Children.start = injected_start
    sys.argv = [str(ROOT / "main.py"), *args]
    sys.path.insert(0, str(ROOT))
    from main import main

    return main()


if __name__ == "__main__":
    sys.exit(injected_main(sys.argv[1], sys.argv[2:]))
