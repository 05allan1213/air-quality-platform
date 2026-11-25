"""Read-only host preflight, followed by the selected supervised services."""

import os
import socket
from http.client import HTTPConnection, HTTPException
from pathlib import Path
from stat import S_ISREG
from urllib.parse import urlsplit
from uuid import uuid4

from .beat_guard import LOCK_KEY, LOCK_NAMESPACE, unescape_mount_field
from .host_config import (
    QUEUES,
    StartupError,
    broker_ready,
    database,
    endpoints,
    private_root,
    require,
)
from .host_processes import Children


def available_http(port):
    listener = socket.socket()
    try:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", port))
    except OSError:
        listener.close()
        raise StartupError("web_port_unavailable") from None
    return listener


def owns_development_listener(port, pid):
    # runserver binds its own socket after the preflight socket is closed.
    # Require the actual child to own the listening inode before trusting HTTP.
    address = f"0100007F:{port:04X}"
    with open("/proc/net/tcp", encoding="ascii") as table:
        inodes = {
            f"socket:[{fields[9]}]"
            for line in list(table)[1:]
            if (fields := line.split())[1] == address and fields[3] == "0A"
        }
    try:
        for fd in Path(f"/proc/{pid}/fd").iterdir():
            try:
                if os.readlink(fd) in inodes:
                    return True
            except FileNotFoundError:
                continue
    except FileNotFoundError:
        return False
    return False


def ready_http(port, *, development_pid=None):
    if development_pid and not owns_development_listener(port, development_pid):
        return False
    client = HTTPConnection("127.0.0.1", port, timeout=0.75)
    try:
        client.request("GET", "/health/ready/")
        return client.getresponse().status == 200 and (
            development_pid is None or owns_development_listener(port, development_pid)
        )
    except (OSError, HTTPException):
        return False
    finally:
        client.close()


def pg_time(db):
    return db.execute("SELECT clock_timestamp()").fetchone()[0]


def pulse(db, role, after, instance):
    row = db.execute(
        "SELECT instance, observed_at, subscribed_queues FROM accounts_servicepulse "
        "WHERE component = %s AND observed_at >= %s",
        (role, after),
    ).fetchone()
    if not row or row[0] != instance:
        return False
    if role == "worker":
        require(set(row[2] or ()) == QUEUES, "worker_queue_mismatch")
    return True


def beat_ready(db, after, publisher):
    if not pulse(db, "beat", after, publisher):
        return False
    return db.execute(
        """SELECT EXISTS (
            SELECT 1 FROM pg_locks AS l JOIN pg_stat_activity AS a USING (pid)
            WHERE l.locktype = 'advisory' AND l.granted AND l.mode = 'ExclusiveLock'
              AND a.datname = current_database() AND a.usename = current_user
              AND a.application_name = %s AND a.backend_start >= %s
              AND l.classid = %s::oid AND l.objid = %s::oid AND l.objsubid = 2
        )""",
        (f"aq-beat-{publisher}", after, LOCK_NAMESPACE, LOCK_KEY),
    ).fetchone()[0]


def migration_check():
    from django.core import checks
    from django.db import connections
    from django.db.migrations.executor import MigrationExecutor

    conn = connections["default"]
    # Prevent any implicit database write from checks and migration introspection.
    with conn.cursor() as cursor:
        cursor.execute("SET default_transaction_read_only = on")
        cursor.execute("SET statement_timeout = '5s'")
    errors = checks.run_checks()
    require(not any(error.is_serious() for error in errors), "django_system_check_failed")
    executor = MigrationExecutor(conn)
    require(
        not executor.migration_plan(executor.loader.graph.leaf_nodes()),
        "migrations_pending_run_explicit_migrate",
    )
    conn.close()


def check_storage(values, root, *, serve_static):
    from observations.backup_authority import authority_root
    from observations.backup_models import BackupSegment
    from observations.controlled_storage import bound_directory
    from observations.disposition import private_dir
    from observations.models import StagedInput

    paths = {}
    for key in ("AQ_STATIC_ROOT", "S02_STAGE_DIR", "S06_BACKUP_DIR", "S06_AUTHORITY_DIR"):
        if values.get(key):
            path = Path(values[key])
            require(path != root and path.is_relative_to(root), f"outside_host_root_{key}")
            require(path.resolve() == path, f"redirected_{key}")
            if key != "AQ_STATIC_ROOT":
                private_root(path)
            paths[key] = path
    places = list(paths.values())
    if "AQ_SPA_DIST" in values:
        dist = Path(values["AQ_SPA_DIST"])
        require(dist.is_absolute() and dist.resolve() == dist, "invalid_AQ_SPA_DIST")
        places.append(dist)
    if values["POSTGRES_HOST"] != "127.0.0.1":
        places.append(Path(values["POSTGRES_HOST"]))
    broker = urlsplit(values["CELERY_BROKER_URL"])
    if broker.scheme == "redis+socket":
        places.append(Path(broker.path).parent)
    if values.get("AQ_BEAT_LOCK_FILE"):
        places.append(Path(values["AQ_BEAT_LOCK_FILE"]))
    require(
        all(not a.is_relative_to(b) for a in places for b in places if a != b)
        and len(places) == len(set(places)),
        "overlapping_private_and_static_storage",
    )
    static = paths["AQ_STATIC_ROOT"]
    if serve_static:
        require(static.is_dir(), "staticfiles_missing_run_collectstatic")
        require(static.stat().st_uid == os.geteuid(), "staticfiles_owner")
        for line in Path("/proc/self/mountinfo").read_text().splitlines():
            target = Path(unescape_mount_field(line.split()[4]))
            require(not target.is_relative_to(static), "staticfiles_mounted_path")
        for folder, dirs, files in os.walk(static):
            require(
                all(not (Path(folder) / name).is_symlink() for name in dirs)
                and all(
                    S_ISREG(info.st_mode) and info.st_nlink == 1
                    for name in files
                    for info in [(Path(folder) / name).lstat()]
                ),
                "staticfiles_redirected_entry",
            )
    stages = StagedInput.objects.exclude(state="cleared").values_list(
        "volume_generation", flat=True
    )
    generations = list(stages.order_by().distinct()[:2])
    if "S02_STAGE_DIR" in paths or generations:
        require("S02_STAGE_DIR" in paths, "stage_storage_required")
        try:
            stage = private_dir()
            for generation in generations:
                with bound_directory(stage, generation):
                    pass
        except OSError:
            raise StartupError("stage_storage_unavailable") from None
    segments = BackupSegment.objects.exclude(state=BackupSegment.State.REMOVED)
    generations = list(
        segments.order_by().values_list("volume_generation", flat=True).distinct()[:2]
    )
    if "S06_AUTHORITY_DIR" in paths:
        require("S06_BACKUP_DIR" in paths, "backup_storage_required")
        authority_root()
    if generations:
        require(
            "S06_BACKUP_DIR" in paths and "S06_AUTHORITY_DIR" in paths, "backup_storage_required"
        )
        for generation in generations:
            with bound_directory(paths["S06_BACKUP_DIR"], generation):
                pass


def launch(values, backend, action, development):
    import django
    from django.db import connections

    root, port, socket_only = endpoints(values, allow_missing_broker=action == "web")
    roles = {"web", "worker", "beat"} if action == "all" else {action} - {"check"}
    if values.get("AQ_S08_FIXTURE_SCHEDULE") == "1":
        require("beat" not in roles, "schedule_fixture_refuses_beat")
        if roles != {"web"}:
            values = values | {"AQ_S08_FIXTURE_SCHEDULE": "0"}
    if "beat" in roles:
        require(values.get("AQ_BEAT_LOCK_FILE"), "missing_AQ_BEAT_LOCK_FILE")
    environment = (
        {"PATH": os.defpath, "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1"}
        | values
        | {
            "DJANGO_SETTINGS_MODULE": "config.settings",
            "AQ_HOST_OFFLINE": "1",
            "PYTHONPATH": str(backend),
            "S02_STAGE_DIR": values.get("S02_STAGE_DIR", str(root / "history-stage")),
        }
    )
    with database(values, socket_only) as db:
        # Read-only autocommit keeps each polling snapshot current.
        broker_ready(values, allow_unavailable=action == "web")
        os.environ.clear()
        os.environ.update(environment)
        django.setup()
        migration_check()
        # Runtime children get normal DB sessions. Parent introspection never writes.
        conn = connections["default"]
        conn.ensure_connection()
        with conn.cursor() as cursor:
            cursor.execute("SET default_transaction_read_only = on")
        try:
            check_storage(values, root, serve_static=bool(roles & {"web"}))
        finally:
            conn.close()
        if action == "check":
            print("[s07] config, database, broker and migrations ready", flush=True)
            return 0
        children = Children(root, backend, environment)
        success = True
        with children.lock(roles), children.signals():
            try:
                if "web" in roles:
                    development_pid = None
                    with available_http(port) as listener:
                        if development:
                            listener.close()
                            development_pid = children.start(
                                "web",
                                ["manage.py", "runserver", f"127.0.0.1:{port}", "--noreload"],
                                quiet=True,
                            ).pid
                        else:
                            listener.listen()
                            children.start(
                                "web",
                                [
                                    "-m",
                                    "gunicorn",
                                    "config.wsgi:application",
                                    "-c",
                                    "python:config.gunicorn_local",
                                    "--bind",
                                    f"fd://{listener.fileno()}",
                                ],
                                pass_fds=(listener.fileno(),),
                            )
                    children.ready(
                        "web", lambda: ready_http(port, development_pid=development_pid), 16
                    )
                if "worker" in roles:
                    for command in ("s02_dispose_due", "s03_reconcile_due"):
                        children.start(command, ["manage.py", command], role="worker", quiet=True)
                        children.wait(command, 30)
                    after = pg_time(db)
                    node = values["AQ_WORKER_NODE"]
                    children.start(
                        "worker",
                        [
                            "-m",
                            "celery",
                            "-A",
                            "config",
                            "worker",
                            "-l",
                            "WARNING",
                            "-n",
                            node,
                            "-Q",
                            ",".join(sorted(QUEUES)),
                            "--concurrency=1",
                        ],
                        quiet=True,
                    )
                    children.ready("worker", lambda: pulse(db, "worker", after, node), 20)
                if "beat" in roles:
                    after = pg_time(db)
                    publisher = uuid4().hex
                    children.start(
                        "beat",
                        ["-m", "celery", "-A", "config", "beat", "-l", "WARNING"],
                        extra_env={"AQ_BEAT_PUBLISHER_ID": publisher},
                        quiet=True,
                    )
                    children.ready("beat", lambda: beat_ready(db, after, publisher), 16)
                children.serve()
            except InterruptedError:
                pass
            finally:
                success = children.stop()
        return int(not success)
