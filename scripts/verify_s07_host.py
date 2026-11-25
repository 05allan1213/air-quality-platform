#!/usr/bin/env python3
"""V01 host isolation for a fresh B instance, or the existing guarded R runner."""

import argparse
import hashlib
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

import verify_s06
from s01_runtime import ROOT, wait_for
from s06_guard import IsolationError, require
from s06_runtime import Runtime
from s07_handoff import checkout
from s07_handoff import source_files as handoff_source_files
from s07_host_guard import (
    BROKER_MARKER,
    POSTGRES_MARKER,
    broker_client,
    check_broker,
    check_database,
    check_environment,
    check_postgres_container,
    check_process_network,
    directory,
    identity,
    work_root,
)
from verify_s06 import DATA, IMAGE, SOCKET, docker, inspect, ready, rejected

LABEL = "airquality.s07.run"
LOCK_ACTIONS = {"startup", "singleton", "singleton-compose", "offline"}


def make_root(run):
    root = work_root(run)
    for parent in (root.parent.parent, root.parent):
        parent.mkdir(mode=0o700, exist_ok=True)
        info = parent.lstat()
        require(
            parent.resolve(strict=True) == parent
            and parent.is_dir()
            and info.st_uid == os.geteuid()
            and not info.st_mode & 0o022,
            "unsafe_parent",
        )
    root.mkdir(mode=0o700)
    directory(root, 0o700)
    for name in ("pg", "stage", "broker", "V01"):
        (root / name).mkdir(mode=0o700)
    # PG runs as a different uid and reaches only this directory via bind mount.
    (root / "pg").chmod(0o777)
    for socket in (root / "pg/.s.PGSQL.5432", root / "broker/redis.sock"):
        require(len(os.fsencode(socket)) < 108, "socket_path_length")
    return root


def environment(run, root):
    db, user = identity(run)
    return {key: os.environ[key] for key in ("PATH", "LANG") if key in os.environ} | {
        "AQ_INSTANCE_ID": run,
        "AQ_HOST_ROOT": str(root),
        "S06_HOST_NET_NS": os.readlink("/proc/self/ns/net"),
        "HOME": "/tmp",
        "TMPDIR": "/tmp",
        "POSTGRES_DB": db,
        "POSTGRES_USER": user,
        "POSTGRES_PASSWORD": secrets.token_hex(32),
        "POSTGRES_HOST": str(root / "pg"),
        "POSTGRES_PORT": "5432",
        "DJANGO_SECRET_KEY": secrets.token_hex(48),
        "DJANGO_ALLOWED_HOSTS": "127.0.0.1,localhost",
        "CNEMC_MODE": "off",
        "CNEMC_FIXTURE_HTTP": "0",
        "S03_FIXTURE_HTTP": "0",
        "S02_STAGE_DIR": str(root / "stage"),
        "CELERY_BROKER_URL": f"redis+socket://{root}/broker/redis.sock",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": os.pathsep.join(str(ROOT / p) for p in ("scripts", "backend", "tests")),
    }


def inventory(run):
    # The filter selects resources created by this invocation only.
    return {
        kind: docker(*cmd, "-q", "--filter", f"label={LABEL}={run}").splitlines()
        for kind, cmd in {
            "containers": ("ps", "-a"),
            "volumes": ("volume", "ls"),
            "networks": ("network", "ls"),
        }.items()
    }


@contextmanager
def cluster(env, receipt, volume=None):
    from psycopg import sql

    run = env["AQ_INSTANCE_ID"]
    root = check_environment(env)
    image = docker("image", "inspect", "--format", "{{.Id}}", IMAGE)
    require(image == IMAGE.split("@", 1)[1], "unexpected_image")
    receipt["postgres_image"] = image
    persistence = (
        ("--mount", f"type=volume,source={volume},target={DATA}")
        if volume
        else ("--tmpfs", f"{DATA}:rw,size=768m")
    )
    with tempfile.NamedTemporaryFile(dir=root, mode="w") as private:
        private.write(
            f"POSTGRES_PASSWORD={secrets.token_hex(32)}\n"
            "POSTGRES_INITDB_ARGS=--auth-local=scram-sha-256\n"
        )
        private.flush()
        container = docker(
            "create",
            "--pull=never",
            "--network=none",
            "--name",
            f"{run}-pg",
            "--label",
            f"{LABEL}={run}",
            *persistence,
            "--mount",
            f"type=bind,source={root / 'pg'},target={SOCKET}",
            "--env-file",
            private.name,
            image,
            "-c",
            "listen_addresses=",
            "-c",
            f"{POSTGRES_MARKER}={run}",
        )
    receipt["postgres_container"] = container
    try:
        info = inspect(container)
        check_postgres_container(info, run, container, image, volume)
        for field, data, reason in (
            ("NetworkMode", "host", "external_network"),
            ("Tmpfs", {DATA: "rw"} if volume else {}, "persistent_data"),
            (
                "Mounts",
                [{"Type": "bind", "Source": str(root / "stage"), "Target": SOCKET}],
                "foreign_bind",
            ),
        ):
            fake = deepcopy(info)
            fake["HostConfig"][field] = data
            receipt["rejections"].append(
                rejected(
                    lambda fake=fake: check_postgres_container(fake, run, container, image, volume),
                    reason,
                )
            )
        if volume:
            fake = deepcopy(info)
            next(m for m in fake["HostConfig"]["Mounts"] if m["Type"] == "volume")["Source"] = (
                f"{run}-foreign"
            )
            receipt["rejections"].append(
                rejected(
                    lambda: check_postgres_container(fake, run, container, image, volume),
                    "foreign_bind",
                )
            )
        fake = deepcopy(info)
        fake["Config"]["Labels"][LABEL] = "foreign-project"
        receipt["rejections"].append(
            rejected(
                lambda: check_postgres_container(fake, run, container, image, volume),
                "container_owner",
            )
        )
        docker("start", container)
        wait_for("S07 host PostgreSQL", lambda: ready(container))
        check_postgres_container(inspect(container), run, container, image, volume)
        db, user = identity(run)
        bootstrap = sql.SQL(
            "CREATE EXTENSION btree_gist;\n"
            "CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEROLE NOCREATEDB PASSWORD {};\n"
            "CREATE DATABASE {} OWNER {};"
        ).format(
            sql.Identifier(user),
            sql.Literal(env["POSTGRES_PASSWORD"]),
            sql.Identifier(db),
            sql.Identifier(user),
        )
        docker(
            "exec",
            "-i",
            container,
            "sh",
            "-ec",
            'PGPASSWORD="$POSTGRES_PASSWORD" exec psql -Xq -v ON_ERROR_STOP=1 '
            "-h /run/postgresql -U postgres -d template1",
            payload=bootstrap.as_string(),
        )
        yield
    finally:
        current = inspect(container)
        check_postgres_container(current, run, container, image, volume)
        if current["State"]["Running"]:
            docker("exec", "-u", "0", container, "chown", f"{os.geteuid()}", SOCKET)
            docker("stop", "--time", "15", container)
        current = inspect(container)
        check_postgres_container(current, run, container, image, volume)
        require(not current["State"]["Running"], "postgres_still_running")
        receipt["postgres_exit"] = current["State"]["ExitCode"]
        docker("rm", container)


def register_startup_lock(env, receipt, *, mode="host"):
    from psycopg import sql
    from s07_beat_setup import lock_identity

    root = check_environment(env)
    lock = root / "V02" / "beat.lock"
    container = receipt["postgres_container"]
    check_postgres_container(
        inspect(container),
        env["AQ_INSTANCE_ID"],
        container,
        receipt["postgres_image"],
        receipt.get("postgres_volume", {}).get("name"),
    )
    check_database(env, empty=True)
    binding = lock_identity(lock.stat())
    commands = sql.SQL("\n").join(
        sql.SQL("ALTER DATABASE {} SET {} TO {};").format(
            sql.Identifier(env["POSTGRES_DB"]), sql.SQL(key), sql.Literal(value)
        )
        for key, value in (
            (POSTGRES_MARKER, env["AQ_INSTANCE_ID"]),
            ("airquality.beat_lock", binding),
            ("airquality.beat_mode", mode),
        )
    )
    docker(
        "exec",
        "-i",
        container,
        "sh",
        "-ec",
        'PGPASSWORD="$POSTGRES_PASSWORD" exec psql -Xq -v ON_ERROR_STOP=1 '
        "-h /run/postgresql -U postgres -d template1",
        payload=commands.as_string(),
    )
    receipt["beat_registration"] = {"mode": mode, "identity": binding}


def source_files():
    return handoff_source_files(ROOT)


def source_digest(root, files):
    digest = hashlib.sha256()
    for name in files:
        path = root / name
        require(path.resolve(strict=True) == path and path.is_file(), "unexpected_source")
        digest.update(os.fsencode(name) + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()


@contextmanager
def freeze_source(root):
    files = source_files()
    digest = source_digest(ROOT, files)
    with tempfile.TemporaryDirectory(prefix="source-", dir=root) as temporary:
        snapshot = Path(temporary)
        for name in files:
            path = ROOT / name
            require(path.resolve(strict=True) == path and path.is_file(), "unexpected_source")
            (snapshot / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, snapshot / name, follow_symlinks=False)
        require(source_digest(snapshot, files) == digest, "snapshot_source_changed")
        yield snapshot, digest


def sandbox_command(env, snapshot, action):
    root = check_environment(env)
    require(snapshot.parent == root and snapshot.resolve(strict=True) == snapshot, "snapshot_root")
    bwrap = shutil.which("bwrap", path="/usr/bin:/bin")
    require(bwrap is not None, "missing_bwrap")
    python = Path(sys.executable)
    interpreter_home = python.resolve().parents[1]
    command = [
        bwrap,
        "--unshare-user",
        "--disable-userns",
        "--unshare-net",
        "--unshare-pid",
        "--unshare-ipc",
        "--new-session",
        "--die-with-parent",
        "--dev",
        "/dev",
        "--proc",
        "/proc",
        "--tmpfs",
        "/tmp",
        "--ro-bind",
        "/usr",
        "/usr",
        "--symlink",
        "usr/bin",
        "/bin",
        "--symlink",
        "usr/lib",
        "/lib",
        "--symlink",
        "usr/lib64",
        "/lib64",
        "--ro-bind",
        "/etc/ld.so.cache",
        "/etc/ld.so.cache",
    ]
    for path in snapshot.iterdir():
        require(path.resolve(strict=True) == path, "redirected_source_path")
        command += ["--ro-bind", str(path), str(ROOT / path.name)]
    command += ["--ro-bind", str(ROOT / ".venv"), str(ROOT / ".venv")]
    if not interpreter_home.is_relative_to("/usr"):
        require(interpreter_home.name.startswith("cpython-"), "unexpected_interpreter_home")
        command += ["--ro-bind", str(interpreter_home), str(interpreter_home)]
    command += ["--bind", str(root), str(root)]
    if action in LOCK_ACTIONS:
        lock = root / "V02" / "beat.lock"
        require(lock.is_file() and lock.resolve(strict=True) == lock, "startup_lock_file")
        command += ["--ro-bind", str(lock), str(lock)]
    command += [
        "--tmpfs",
        str(snapshot),
        "--chdir",
        str(ROOT),
        "--",
    ]
    return command + [str(python), str(ROOT / "scripts/verify_s07_host.py"), "_sandbox", action]


def host_negative(env, receipt):
    root = check_environment(env)
    for key, wrong, reason in (
        ("AQ_INSTANCE_ID", "air-quality-platform", "invalid_s07_host_identity"),
        ("POSTGRES_DB", "unexpected", "invalid_postgres_db"),
        ("POSTGRES_HOST", str(root / "stage"), "invalid_postgres_host"),
        ("S02_STAGE_DIR", str(root / "broker"), "invalid_s02_stage_dir"),
        (
            "CELERY_BROKER_URL",
            f"redis+socket://{root}/stage/redis.sock",
            "invalid_celery_broker_url",
        ),
        ("CNEMC_MODE", "limited", "invalid_cnemc_mode"),
        ("PGSERVICE", "inherited", "environment_override"),
        ("HTTPS_PROXY", "http://127.0.0.1:1", "environment_override"),
        ("DJANGO_SETTINGS_MODULE", "config.settings", "environment_override"),
    ):
        receipt["rejections"].append(
            rejected(lambda key=key, wrong=wrong: check_environment(env | {key: wrong}), reason)
        )
    receipt["rejections"].append(
        rejected(lambda: check_database(env, empty=True, marker="foreign"), "database_identity")
    )
    receipt["rejections"].append(
        rejected(lambda: check_process_network(env["S06_HOST_NET_NS"]), "host_network_namespace")
    )


def start_broker(runtime, env):
    runtime.redis()
    with broker_client(env) as broker:
        server = broker.info("server")
        require(server["process_id"] == runtime.children["redis"].pid, "redis_process_owner")
        require(broker.dbsize() == 0, "new_broker_not_empty")
        require(broker.set(BROKER_MARKER, env["AQ_INSTANCE_ID"], nx=True), "redis_marker_exists")


def run_sandbox(action):
    # The parent hands in an allowlisted environment and read-only source snapshot.
    env = os.environ
    root = check_environment(env)
    network = check_process_network(env["S06_HOST_NET_NS"])
    env["S06_RUN"] = env["AQ_INSTANCE_ID"]  # Runtime uses the label only to name processes.
    checks = {
        "network": network,
        "rejections": [],
        "database_before": check_database(env, empty=True),
    }
    runtime = Runtime(root)
    try:
        with runtime.managed():
            start_broker(runtime, env)
            checks["broker_before"] = check_broker(env, empty=True)
            checks["rejections"].append(
                rejected(lambda: check_broker(env, empty=True, marker="foreign"), "broker_identity")
            )
            checks["broker_after_rejections"] = check_broker(env, empty=True)
            checks["database_after_rejections"] = check_database(env, empty=True)
            checks["status"] = "PASS"
            if action == "startup":
                from s07_startup_checks import verify

                verify(root, checks)
            if action in {"singleton", "singleton-compose"}:
                from s07_beat_checks import verify

                verify(root, checks, compose=action == "singleton-compose")
            if action == "contract":
                from s07_contract_admin import verify

                verify(root, runtime)
            if action == "offline":
                from s07_offline_host import verify

                verify(root, checks, runtime, start_broker)
            if action == "performance":
                from s07_performance_host import measure

                measure(root, runtime)
            if action == "national-profile":
                from s08_national_profile import measure

                measure(root, runtime)
    finally:
        checks["lifecycle"] = runtime.lifecycle
        checks["remaining_processes"] = len(runtime.children)
        (root / "V01" / "sandbox.json").write_text(json.dumps(checks, indent=2) + "\n")


def run_host(action):
    run = f"s07-b-{secrets.token_hex(6)}"
    root = make_root(run)
    env = environment(run, root)
    receipt = {
        "run": run,
        "scope": "V01-B",
        "origin": "F/I",
        "status": "FAIL",
        "rejections": [],
        "started_utc": datetime.now(UTC).isoformat(),
        "cleanup": False,
    }
    from s07_offline_host import owned_volume, postgres_bridge

    print(root.relative_to(ROOT), flush=True)
    try:
        receipt["before"] = inventory(run)
        require(not any(receipt["before"].values()), "run_exists")
        receipt["code_commit"] = checkout(ROOT)["head"]
        receipt["lock_sha256"] = hashlib.sha256((ROOT / "uv.lock").read_bytes()).hexdigest()
        with (
            freeze_source(root) as (snapshot, digest),
            owned_volume(env, receipt, enabled=action in {"offline", "performance"}) as volume,
            cluster(env, receipt, volume),
        ):
            receipt["source_sha256"] = digest
            receipt["database_before"] = check_database(env, empty=True)
            host_negative(env, receipt)
            if action in LOCK_ACTIONS:
                (root / "V02").mkdir(mode=0o700)
                (root / "V02" / "beat.lock").touch(mode=0o444, exist_ok=False)
                register_startup_lock(
                    env, receipt, mode="shared" if action == "singleton-compose" else "host"
                )
            command = sandbox_command(env, snapshot, action)
            if action == "singleton-compose":
                from s07_beat_compose import compose_bridge

                code = compose_bridge(command, env, snapshot, receipt)
            elif action == "offline":
                code = postgres_bridge(command, env, receipt)
            else:
                code = subprocess.run(
                    command,
                    cwd=ROOT,
                    env=env,
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=900 if action == "performance" else 300,
                ).returncode
            receipt["sandbox_exit"] = code
            if (root / "V01" / "sandbox.json").exists():
                receipt["sandbox"] = json.loads((root / "V01" / "sandbox.json").read_text())
            require(code == 0, "sandbox_probe_failed")
            require(receipt["sandbox"]["status"] == "PASS", "sandbox_receipt_failed")
            if action == "guard":
                receipt["database_after"] = check_database(env, empty=True)
    except (IsolationError, OSError, subprocess.SubprocessError, ValueError, AssertionError) as exc:
        receipt["error"] = str(exc) if isinstance(exc, IsolationError) else type(exc).__name__
    finally:
        try:
            receipt["after"] = inventory(run)
            require(not any(receipt["after"].values()), "resources_remaining")
            for name, mode in (("pg", None), ("stage", 0o700), ("broker", 0o700)):
                path = (root / name) if name == "pg" else directory(root / name, mode)
                require(path.resolve(strict=True) == path and path.is_dir(), "cleanup_path")
                shutil.rmtree(path)
            receipt["cleanup"] = set(p.name for p in root.iterdir()) <= {
                "V01",
                "V02",
                "V03",
                "V12",
                "V14",
                "performance",
            }
        except (IsolationError, OSError, subprocess.SubprocessError) as exc:
            receipt["cleanup_error"] = (
                str(exc) if isinstance(exc, IsolationError) else type(exc).__name__
            )
        if "error" not in receipt and receipt["cleanup"]:
            receipt["status"] = "PASS"
        receipt["finished_utc"] = datetime.now(UTC).isoformat()
        (root / "V01" / "result.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(f"{receipt['scope']} {receipt['status']}")
    return int(receipt["status"] != "PASS")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=(
            "guard",
            "startup",
            "singleton",
            "singleton-compose",
            "offline",
            "contract",
            "performance",
            "national-profile",
            "r",
            "restore",
            "_sandbox",
        ),
    )
    parser.add_argument("options", nargs="*")
    args = parser.parse_args()
    if args.action in {"r", "restore"}:
        # These runners already create and verify a new per-invocation R environment.
        # Keep their settings and restore custodian compatible with existing tests.
        name = "verify_s06.py" if args.action == "r" else "s06_restore_verify.py"
        argv = args.options or (["guard"] if args.action == "r" else [])
        if args.action == "r" and argv[0] not in {
            "guard",
            "pytest",
            "services",
            "collection",
            "upgrade",
        }:
            parser.error("invalid R action")
        if args.action == "restore" and not all(verify_s06.SELECTOR.fullmatch(arg) for arg in argv):
            parser.error("invalid restore test selector")
        clean_env = {k: os.environ[k] for k in ("PATH", "HOME", "LANG") if k in os.environ}
        return subprocess.run(
            [sys.executable, "-B", str(ROOT / "scripts" / name), *argv],
            cwd=ROOT,
            env=clean_env,
            check=False,
            timeout=1300,
        ).returncode
    if args.action == "_sandbox" and args.options in (
        ["guard"],
        ["startup"],
        ["singleton"],
        ["singleton-compose"],
        ["offline"],
        ["contract"],
        ["performance"],
        ["national-profile"],
    ):
        run_sandbox(*args.options)
        return 0
    if args.options or args.action == "_sandbox":
        parser.error("invalid host acceptance action")
    return run_host(args.action)


if __name__ == "__main__":
    sys.exit(main())
