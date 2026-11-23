#!/usr/bin/env python3
"""Run S06 verification in a disposable socket-only cluster and private network namespace."""

import argparse
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tarfile
import tempfile
from contextlib import contextmanager, nullcontext
from copy import deepcopy
from pathlib import Path
from time import monotonic, sleep

from s01_runtime import CONTRACT_SPEC, ROOT, source_hash, wait_for
from s06_guard import (
    IsolationError,
    check_database,
    check_environment,
    identity,
    owned_root,
    require,
    run_root,
)
from s06_sandbox import SELECTOR, sandbox_command

LABEL = "airquality.s06.run"
IMAGE = "postgres:16-alpine@sha256:1d5b02d3a9941fbd52437db4107f8c52d6297dac553ca1e5c299ebb3756b4857"
DATA = "/var/lib/postgresql/data"
SOCKET = "/run/postgresql"
BASELINE = "be6681298a28220ac97136087a4bdf316799c93d"
BASELINE_SOURCE = "575a400e610c7b9cfaae0387bdcfc7fcbcf4a83215636c133486bf728d1e005f"
SOURCE_FILES = (
    "Dockerfile",
    ".dockerignore",
    ".env.example",
    ".python-version",
    "compose.yaml",
    "pyproject.toml",
    "uv.lock",
)
SOURCE_DIRS = ("backend", "tests", "scripts", "docker")


def docker(*args, payload=None, timeout=45):
    env = {k: os.environ[k] for k in ("HOME", "LANG", "PATH") if k in os.environ}
    return subprocess.run(
        ["docker", "--host", "unix:///var/run/docker.sock", *args],
        cwd=ROOT,
        env=env,
        input=payload,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=True,
    ).stdout.strip()


def make_root(run):
    root = run_root(run)
    for parent in (root.parent.parent, root.parent):
        parent.mkdir(mode=0o700, exist_ok=True)
        require(parent.resolve(strict=True) == parent and parent.is_dir(), "redirected_parent")
        info = parent.stat()
        require(info.st_uid == os.geteuid() and not info.st_mode & 0o022, "unsafe_parent")
    root.mkdir(mode=0o700)
    owned_root(run)
    require(len(os.fsencode(root / "pg" / ".s.PGSQL.5432")) < 108, "socket_path_length")
    for name in ("pg", "broker", "stage", "V01"):
        (root / name).mkdir(mode=0o700)
    # Only the container's postgres uid needs write access, behind the private run root.
    (root / "pg").chmod(0o777)
    with open(root / "stage" / "beat.lock", "xb"):
        pass
    (root / "stage" / "beat.lock").chmod(0o444)
    return root


def environment(run, root, password):
    db, user = identity(run)
    return {k: os.environ[k] for k in ("PATH", "HOME", "LANG") if k in os.environ} | {
        "S06_RUN": run,
        "AQ_INSTANCE_ID": run,
        "AQ_BEAT_LOCK_FILE": str(root / "stage" / "beat.lock"),
        "S06_WORK_ROOT": str(root),
        "POSTGRES_DB": db,
        "S06_HOST_NET_NS": os.readlink("/proc/self/ns/net"),
        "TMPDIR": "/tmp",
        "POSTGRES_USER": user,
        "POSTGRES_PASSWORD": password,
        "POSTGRES_HOST": str(root / "pg"),
        "POSTGRES_PORT": "5432",
        "DJANGO_SECRET_KEY": secrets.token_hex(48),
        "DJANGO_SETTINGS_MODULE": "s06_settings",
        "DJANGO_ALLOWED_HOSTS": "testserver,localhost,127.0.0.1",
        "CNEMC_MODE": "off",
        "CNEMC_FIXTURE_HTTP": "0",
        "S03_FIXTURE_HTTP": "0",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": os.pathsep.join(str(ROOT / p) for p in ("scripts", "backend", "tests")),
    }


def inspect(container):
    (info,) = json.loads(docker("inspect", "--type", "container", container))
    return info


def owner(info, run, container, image):
    require(re.fullmatch(r"[0-9a-f]{64}", container), "invalid_container_id")
    require(info["Id"] == container and info["Name"] == f"/{run}-pg", "container_identity")
    require(
        info["Config"]["Labels"].get(LABEL) == run and info["Image"] == image, "container_owner"
    )


def isolation(info, run, container, image):
    owner(info, run, container, image)
    path = str(owned_root(run) / "pg")
    cfg = info["HostConfig"]
    require(
        cfg["NetworkMode"] == "none" and not cfg["PortBindings"] and not cfg["PublishAllPorts"],
        "external_network",
    )
    require(
        not cfg["Privileged"] and not cfg["Binds"] and not cfg.get("VolumesFrom"),
        "unsafe_container",
    )
    mounts = cfg.get("Mounts") or []
    require(
        len(mounts) == 1
        and (mounts[0]["Type"], mounts[0]["Source"], mounts[0]["Target"]) == ("bind", path, SOCKET),
        "foreign_bind",
    )
    require(set(cfg.get("Tmpfs") or {}) == {DATA}, "persistent_data")
    for mount in info["Mounts"]:
        require(
            (mount["Type"], mount["Destination"]) == ("tmpfs", DATA)
            or (mount["Type"], mount.get("Source"), mount["Destination"]) == ("bind", path, SOCKET),
            "foreign_mount",
        )
    require(set(info["NetworkSettings"].get("Networks") or {}) <= {"none"}, "attached_network")


def ready(container):
    try:
        docker(
            "exec",
            container,
            "sh",
            "-ec",
            'test "$(cat /proc/1/comm)" = postgres && '
            "pg_isready -q -h /run/postgresql -U postgres -d postgres",
        )
    except subprocess.CalledProcessError:
        return False
    return True


def rejected(call, reason):
    try:
        call()
    except IsolationError as exc:
        require(str(exc) == reason, "unexpected_rejection")
        return reason
    raise IsolationError("accepted_unsafe_configuration")


@contextmanager
def cluster(env, receipt):
    run, root = env["S06_RUN"], check_environment(env)
    image = docker("image", "inspect", "--format", "{{.Id}}", IMAGE)
    require(re.fullmatch(r"sha256:[0-9a-f]{64}", image), "unknown_image")
    receipt["image"] = image
    with tempfile.NamedTemporaryFile(dir=root, mode="w") as private:
        private.write(
            f"POSTGRES_PASSWORD={secrets.token_hex(32)}\nPOSTGRES_INITDB_ARGS=--auth-local=scram-sha-256\n"
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
            "--tmpfs",
            f"{DATA}:rw,size=768m",
            "--mount",
            f"type=bind,source={root / 'pg'},target={SOCKET}",
            "--env-file",
            private.name,
            image,
            "-c",
            "listen_addresses=",
        )
    receipt["container_id"] = container
    try:
        info = inspect(container)
        isolation(info, run, container, image)
        cases = receipt["rejected"]
        fake = deepcopy(info)
        fake["HostConfig"]["NetworkMode"] = "bridge"
        cases.append(rejected(lambda: isolation(fake, run, container, image), "external_network"))
        fake = deepcopy(info)
        fake["HostConfig"]["Mounts"][0]["Source"] = "/not-task-owned"
        cases.append(rejected(lambda: isolation(fake, run, container, image), "foreign_bind"))
        docker("start", container)
        wait_for("S06 owned PostgreSQL", lambda: ready(container))
        isolation(inspect(container), run, container, image)
        receipt["mounts"] = {"socket": "private_task_bind", "data": "tmpfs"}
        receipt["network"] = "none"
        db, user = identity(run)
        from psycopg import sql
        from s07_beat_setup import lock_identity

        bootstrap = sql.SQL(
            "CREATE EXTENSION btree_gist;\nCREATE ROLE {} LOGIN NOSUPERUSER CREATEDB PASSWORD {};\n"
            "CREATE DATABASE {} OWNER {};\nALTER DATABASE {} SET airquality.s06_run = {};\n"
            "ALTER ROLE {} SET airquality.s06_run = {};\n"
            "ALTER DATABASE {} SET airquality.s07_instance = {};\n"
            "ALTER DATABASE {} SET airquality.beat_mode = 'host';\n"
            "ALTER DATABASE {} SET airquality.beat_lock = {};\n"
        ).format(
            sql.Identifier(user),
            sql.Literal(env["POSTGRES_PASSWORD"]),
            sql.Identifier(db),
            sql.Identifier(user),
            sql.Identifier(db),
            sql.Literal(run),
            sql.Identifier(user),
            sql.Literal(run),
            sql.Identifier(db),
            sql.Literal(run),
            sql.Identifier(db),
            sql.Identifier(db),
            sql.Literal(lock_identity((root / "stage" / "beat.lock").stat())),
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
        yield container, image
    finally:
        info = inspect(container)
        owner(info, run, container, image)
        if info["State"]["Paused"]:
            isolation(info, run, container, image)
            docker("unpause", container, timeout=10)
        if info["State"]["Running"]:
            docker("stop", "--time", "20", container)
        stopped = inspect(container)
        owner(stopped, run, container, image)
        require(not stopped["State"]["Running"], "owned_postgres_still_running")
        receipt["postgres_stop_exit"] = stopped["State"]["ExitCode"]
        docker("rm", container)


def probe(env, receipt, snapshot):
    run = env["S06_RUN"]
    receipt["pg_before"] = check_database(env, expected_marker=run, empty=True)
    for fn, reason in (
        (
            lambda: check_database(env, expected_marker="wrong-marker", empty=True),
            "database_identity",
        ),
        (lambda: check_environment(env | {"POSTGRES_HOST": "/not-owned"}), "invalid_postgres_host"),
        (lambda: check_environment(env | {"S06_WORK_ROOT": "/not-owned"}), "invalid_s06_work_root"),
        (lambda: check_environment(env | {"CNEMC_MODE": "limited"}), "invalid_cnemc_mode"),
        (lambda: check_environment(env | {"CELERY_BROKER_URL": "forbidden"}), "override"),
    ):
        receipt["rejected"].append(rejected(fn, reason))
    child = subprocess.run(
        sandbox_command(env, snapshot, "probe"),
        env=env,
        cwd=ROOT,
        check=False,
        text=True,
        capture_output=True,
        timeout=20,
    )
    if child.returncode:
        # The probe supplies only fixed error codes. Never propagate subprocess
        # diagnostics, which can contain credentials or arbitrary environment values.
        require(False, "sandbox_probe_failed")
    config = json.loads(child.stdout)
    receipt["rejected"].extend(config["rejected"])
    require(config["source"] == receipt["source_before"], "snapshot_digest_mismatch")
    require(
        config["broker"] == f"redis+socket://{env['S06_WORK_ROOT']}/broker/redis.sock"
        and config["mode"] == "off"
        and all(q.startswith(run + ".") for q in config["queues"]),
        "runtime_routes",
    )
    receipt["migration_leaves"] = config["leaves"]
    receipt["queues"] = config["queues"]
    receipt["sandbox"] = config["sandbox"]
    receipt["pg_after_rejections"] = check_database(env, expected_marker=run, empty=True)


@contextmanager
def freeze_source(root):
    """Copy the hash manifest once and bind the resulting test inputs read-only."""
    previous = source_hash()
    with tempfile.TemporaryDirectory(prefix="source-", dir=root) as temporary:
        snapshot = Path(temporary)
        for name in SOURCE_DIRS:
            shutil.copytree(
                ROOT / name, snapshot / name, ignore=shutil.ignore_patterns("__pycache__")
            )
        for name in SOURCE_FILES:
            shutil.copy2(ROOT / name, snapshot / name)
        for path in (*ROOT.glob("compose.*.yaml"), ROOT / "main.py"):
            if path.is_file():
                require(path.resolve(strict=True) == path, "invalid_root_source")
                shutil.copyfile(path, snapshot / path.name, follow_symlinks=False)
        optional = [ROOT / CONTRACT_SPEC, ROOT / "main.py", *ROOT.glob("compose.*.yaml")]
        for path in optional:
            if not path.is_file():
                continue
            require(path.resolve(strict=True) == path, "invalid_source_manifest")
            target = snapshot / path.relative_to(ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target, follow_symlinks=False)
        require(source_hash() == previous, "source_changed_during_snapshot")
        yield snapshot, previous


@contextmanager
def baseline_code(root, receipt):
    """Validate the frozen S05 source and expose only its backend package tree."""
    from io import BytesIO

    commit = (
        subprocess.check_output(
            ["git", "rev-parse", "--verify", f"{BASELINE}^{{commit}}"], cwd=ROOT, timeout=10
        )
        .decode()
        .strip()
    )
    require(commit == BASELINE, "s05_baseline_commit")
    archive = subprocess.check_output(
        ["git", "archive", "--format=tar", commit, "--", *SOURCE_FILES, *SOURCE_DIRS],
        cwd=ROOT,
        timeout=20,
    )
    with tempfile.TemporaryDirectory(prefix="baseline-", dir=root) as temporary:
        tree = Path(temporary)
        with tarfile.open(fileobj=BytesIO(archive), mode="r:") as tar:
            tar.extractall(tree, filter="data")
        hashed = (
            subprocess.check_output(
                [
                    sys.executable,
                    "-I",
                    "-B",
                    "-c",
                    "import runpy, sys; print(runpy.run_path(sys.argv[1])['source_hash']())",
                    str(tree / "scripts/s01_runtime.py"),
                ],
                cwd=tree,
                timeout=10,
            )
            .decode()
            .strip()
        )
        require(hashed == BASELINE_SOURCE, "s05_baseline_fingerprint")
        receipt["baseline"] = {"commit": commit, "source": hashed, "mounted_subtree": "backend"}
        yield tree / "backend"


def evidence_only(root):
    files = {"runtime.json", "pytest.json", "upgrade.json"}
    cost_files = {
        "V12/json-byte-bound.json",
        "V12/csv-byte-bound.json",
        "V12/export-row-and-cell-bounds.json",
        "V17/query-cost.json",
        "V17/csv-query-cost.json",
    }
    for path in root.iterdir():
        if path.is_symlink():
            return False
        if path.name == "V01" and path.is_dir() or path.name in files and path.is_file():
            continue
        if path.name != "s05-regression" or not path.is_dir():
            return False
        for entry in path.rglob("*"):
            name = entry.relative_to(path).as_posix()
            if entry.is_symlink() or not (
                (entry.is_dir() and name in {"V12", "V17"})
                or (entry.is_file() and name in cost_files)
            ):
                return False
    return True


def phase(child, stage, name, *, seconds):
    deadline = monotonic() + seconds
    while monotonic() < deadline:
        if (stage / name).is_file():
            return True
        if child.poll() is not None:
            return False
        sleep(0.1)
    raise IsolationError(f"runtime_phase_timeout_{name}")


def verify_processes(env, snapshot, container, image, receipt):
    run = env["S06_RUN"]
    stage = owned_root(run) / "stage"
    injection = receipt["postgres_injection"] = {}
    child = subprocess.Popen(sandbox_command(env, snapshot, "services"), cwd=ROOT, env=env)
    try:
        if phase(child, stage, "pg-check-armed", seconds=90):
            check_database(env, expected_marker=run, empty=False)
            isolation(inspect(container), run, container, image)
            try:
                docker("pause", container, timeout=10)
                paused = inspect(container)
                isolation(paused, run, container, image)
                require(paused["State"]["Paused"], "postgres_not_paused")
                injection["owned_container_paused"] = True
                (stage / "pg-paused").touch(exist_ok=False)
                injection["http_acknowledged"] = phase(child, stage, "pg-check-done", seconds=25)
            finally:
                current = inspect(container)
                isolation(current, run, container, image)
                if current["State"]["Paused"]:
                    docker("unpause", container, timeout=10)
            check_database(env, expected_marker=run, empty=False)
            injection["owned_database_restored"] = True
            (stage / "pg-restored").touch(exist_ok=False)
        return child.wait(timeout=30)
    finally:
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=8)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("guard", "pytest", "services", "collection", "upgrade"))
    parser.add_argument("tests", nargs="*")
    args = parser.parse_args()
    if args.tests and (
        args.action != "pytest" or not all(SELECTOR.fullmatch(selector) for selector in args.tests)
    ):
        parser.error("Only repository test selectors are accepted")
    run = f"s06-{secrets.token_hex(6)}"
    root = make_root(run)
    env = environment(run, root, secrets.token_hex(32))
    receipt = {
        "run": run,
        "source_before": None,
        "origin": "F/I",
        "rejected": [],
        "action": args.action,
        "guard": "NOT RUN",
        "action_exit": None,
        "cleanup": False,
    }
    print(f"S06: {root.relative_to(ROOT)}", flush=True)
    try:
        with (
            freeze_source(root) as (snapshot, digest),
            baseline_code(root, receipt) if args.action == "upgrade" else nullcontext() as old,
            cluster(env, receipt) as (container, image),
        ):
            receipt["source_before"] = digest
            probe(env, receipt, snapshot)
            isolation(inspect(container), run, container, image)
            receipt["guard"] = "PASS"
            if args.action == "guard":
                receipt["action_exit"] = 0
            elif args.action == "services":
                receipt["action_exit"] = verify_processes(env, snapshot, container, image, receipt)
            else:
                receipt["action_exit"] = subprocess.run(
                    sandbox_command(env, snapshot, args.action, *args.tests, old=old),
                    cwd=ROOT,
                    env=env,
                    timeout=1200,
                ).returncode
    except (IsolationError, OSError, subprocess.SubprocessError, AssertionError) as exc:
        receipt["failure"] = str(exc) if isinstance(exc, IsolationError) else type(exc).__name__
        if receipt["guard"] != "PASS":
            receipt["guard"] = "FAIL"
    finally:
        try:
            remaining = docker("ps", "-aq", "--filter", f"label={LABEL}={run}")
            require(not remaining, "owned_container_remaining")
            receipt["remaining_containers"] = 0
            owned_root(run)
            for name in ("pg", "stage", "broker"):
                path = root / name
                require(path.resolve(strict=True) == path and path.is_dir(), "cleanup_path")
                shutil.rmtree(path)
            receipt["cleanup"] = evidence_only(root)
        except (IsolationError, OSError, subprocess.SubprocessError) as exc:
            receipt["cleanup"] = False
            receipt["cleanup_failure"] = (
                str(exc) if isinstance(exc, IsolationError) else type(exc).__name__
            )
        receipt["workspace_source_after"] = source_hash()
        receipt["source_mode"] = "readonly_snapshot"
        receipt["result"] = (
            "PASS"
            if (
                receipt["guard"] == "PASS"
                and receipt["cleanup"]
                and "failure" not in receipt
                and receipt["action_exit"] == 0
            )
            else "FAIL"
        )
        (root / "V01" / "result.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(
        f"{receipt['result']} (guard={receipt['guard']}, cleanup={receipt['cleanup']})", flush=True
    )
    return int(receipt["result"] != "PASS")


if __name__ == "__main__":
    sys.exit(main())
