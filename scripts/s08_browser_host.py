#!/usr/bin/env python3
"""Run the formal frontend against disposable F data and isolated local services."""

import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import traceback
from contextlib import ExitStack, contextmanager
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path

from s01_runtime import ROOT, wait_for
from s06_guard import IsolationError, require
from s06_sandbox import check_network
from s08_governance_browser import MODE as GOVERNANCE_MODE
from s08_governance_browser import SPEC as GOVERNANCE_SPEC
from s08_governance_browser import WITNESS as GOVERNANCE_WITNESS
from verify_s06 import DATA, IMAGE, docker, inspect, ready

LABEL = "airquality.s08.run"
# The guarded host entry validates these markers before launching Web/Worker.
MARKER = "airquality.s07_instance"
BROKER_MARKER = "airquality:s07:instance"
PYTHON_FILES = ("backend", "scripts", "tests", "main.py")
DEV_PORTS = {"api": 8000, "vite": 5173}
BROWSER_MODES = {"dev", "production", GOVERNANCE_MODE}
PRODUCTION_MODES = {"production", "national-profile", GOVERNANCE_MODE}


def name(run):
    require(re.fullmatch(r"s08-b-[a-f0-9]{12}", run or "") is not None, "instance_name")
    return f"s08_db_{run[-12:]}", f"s08_app_{run[-12:]}"


def root_for(run):
    name(run)
    return ROOT / "tmp" / "s08-evidence" / run


def directory(path, mode=0o700):
    require(path.resolve(strict=True) == path, "redirected_directory")
    info = path.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid(), "directory_owner")
    require(stat.S_IMODE(info.st_mode) == mode, "directory_mode")
    return path


def docker_environment():
    return {key: os.environ[key] for key in ("HOME", "LANG", "PATH") if key in os.environ}


def container_inventory(run):
    return docker("ps", "-a", "-q", "--filter", f"label={LABEL}={run}").splitlines()


def make_root(run):
    parent = ROOT / "tmp" / "s08-evidence"
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory(parent)
    root = root_for(run)
    root.mkdir(mode=0o700)
    (root / "visual").mkdir(mode=0o700)
    return root


def host_environment(run):
    database, role = name(run)
    runtime = Path("/tmp/s08-host")
    return {
        "AQ_INSTANCE_ID": run,
        "AQ_HOST_ROOT": str(runtime),
        "DJANGO_SECRET_KEY": secrets.token_urlsafe(48),
        "POSTGRES_DB": database,
        "POSTGRES_USER": role,
        "POSTGRES_PASSWORD": secrets.token_urlsafe(32),
        "POSTGRES_HOST": str(runtime / "pg"),
        "POSTGRES_PORT": "5432",
        "CELERY_BROKER_URL": f"redis+socket://{runtime}/broker/redis.sock",
        "DJANGO_ALLOWED_HOSTS": "127.0.0.1,localhost",
        "COOKIE_SECURE": "0",
        "CNEMC_MODE": "off",
        "CNEMC_FIXTURE_HTTP": "0",
        "S03_FIXTURE_HTTP": "0",
        "AQ_S08_FIXTURE_SCHEDULE": "1",
        "AQ_SPA_DIST": str(runtime / "dist"),
        "AQ_STATIC_ROOT": str(runtime / "staticfiles"),
        "WEB_PORT": "8000",
        "AQ_WORKER_NODE": f"worker@{run}",
    }


def check_container(info, run, container, image, runtime):
    expected_root = runtime / "pg"
    require(info["Id"] == container and info["Name"] == f"/{run}-pg", "container_identity")
    require(
        info["Config"]["Labels"].get(LABEL) == run and info["Image"] == image, "container_owner"
    )
    config = info["HostConfig"]
    require(
        config["NetworkMode"] == "none"
        and not config["PortBindings"]
        and not config["PublishAllPorts"]
        and set(info["NetworkSettings"].get("Networks") or {}) <= {"none"},
        "container_network",
    )
    require(
        not config["Privileged"] and not config["Binds"] and not config.get("VolumesFrom"),
        "container_privilege",
    )
    mounts = config.get("Mounts") or []
    require(
        len(mounts) == 1
        and mounts[0]["Type"] == "bind"
        and mounts[0]["Source"] == str(expected_root)
        and mounts[0]["Target"] == "/run/postgresql"
        and not mounts[0].get("ReadOnly", False),
        "container_mount",
    )
    require(set(config.get("Tmpfs") or {}) == {DATA}, "container_data_not_ephemeral")


@contextmanager
def postgres(run, runtime, env):
    from psycopg import sql

    image = docker("image", "inspect", "--format", "{{.Id}}", IMAGE)
    require(image == IMAGE.split("@", 1)[1], "unexpected_postgres_image")
    container_name = f"{run}-pg"
    require(
        not docker("ps", "-a", "-q", "--filter", f"name=^{container_name}$"),
        "container_name_exists",
    )
    with tempfile.NamedTemporaryFile(
        mode="w", dir=runtime, prefix="pg-env-", delete=False
    ) as private:
        os.chmod(private.name, 0o600)
        private.write(
            f"POSTGRES_PASSWORD={secrets.token_urlsafe(32)}\n"
            "POSTGRES_INITDB_ARGS=--auth-local=scram-sha-256\n"
        )
        private.flush()
        env_file = Path(private.name)
    container = None
    try:
        container = docker(
            "create",
            "--pull=never",
            "--network=none",
            "--name",
            container_name,
            "--label",
            f"{LABEL}={run}",
            "--tmpfs",
            f"{DATA}:rw,size=768m",
            "--mount",
            f"type=bind,source={runtime / 'pg'},target=/run/postgresql",
            "--env-file",
            str(env_file),
            image,
            "-c",
            "listen_addresses=",
            "-c",
            f"{MARKER}={run}",
        )
        require(re.fullmatch(r"[0-9a-f]{64}", container) is not None, "container_id")
        check_container(inspect(container), run, container, image, runtime)
        docker("start", container)
        wait_for("S08 isolated PostgreSQL", lambda: ready(container), seconds=30)
        check_container(inspect(container), run, container, image, runtime)
        db, role = name(run)
        bootstrap = sql.SQL(
            "CREATE EXTENSION btree_gist;\nCREATE ROLE {} LOGIN NOSUPERUSER NOCREATEROLE "
            "NOCREATEDB NOREPLICATION PASSWORD {};\nCREATE DATABASE {} OWNER {};"
        ).format(
            sql.Identifier(role),
            sql.Literal(env["POSTGRES_PASSWORD"]),
            sql.Identifier(db),
            sql.Identifier(role),
        )
        docker(
            "exec",
            "-i",
            container,
            "sh",
            "-ec",
            'PGPASSWORD="$POSTGRES_PASSWORD" exec psql -Xq -v ON_ERROR_STOP=1 -h /run/postgresql -U postgres -d template1',
            payload=bootstrap.as_string(),
        )
        env_file.unlink()
        yield
    finally:
        env_file.unlink(missing_ok=True)
        if container is not None:
            current = inspect(container)
            check_container(current, run, container, image, runtime)
            if current["State"]["Running"]:
                docker("exec", "-u", "0", container, "chown", str(os.geteuid()), "/run/postgresql")
                docker("stop", "--time", "15", container)
            current = inspect(container)
            check_container(current, run, container, image, runtime)
            require(not current["State"]["Running"], "postgres_still_running")
            docker("rm", container)


def verify_database(env, run, *, empty=False):
    import psycopg

    with (
        psycopg.connect(
            host=env["POSTGRES_HOST"],
            port=5432,
            dbname=env["POSTGRES_DB"],
            user=env["POSTGRES_USER"],
            password=env["POSTGRES_PASSWORD"],
            connect_timeout=3,
            options="-c default_transaction_read_only=on -c statement_timeout=2500",
        ) as db,
        db.cursor() as cur,
    ):
        cur.execute(
            "SELECT current_database(), current_user, inet_server_addr() IS NULL, "
            "current_setting('listen_addresses'), current_setting(%s, true), "
            "current_setting('transaction_read_only'), r.rolsuper, r.rolcreaterole, "
            "r.rolcreatedb, r.rolreplication, r.rolbypassrls "
            "FROM pg_roles r WHERE r.rolname=current_user",
            (MARKER,),
        )
        expected = (*name(run), True, "", run, "on", False, False, False, False, False)
        row = cur.fetchone()
        require(row == expected, "database_identity")
        cur.execute(
            "SELECT count(*) FROM pg_tables WHERE schemaname NOT IN "
            "('pg_catalog','information_schema') AND schemaname !~ '^pg_(toast|temp)'"
        )
        tables = cur.fetchone()[0]
        require(not empty or tables == 0, "database_not_empty")
        return {"socket_only": True, "application_role": True, "business_tables": tables}


def reserve_port(port):
    with socket.socket() as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
        try:
            listener.bind(("127.0.0.1", port))
        except OSError as exc:
            raise IsolationError(f"loopback_port_unavailable_{port}") from exc


def minimal_snapshot(private, run):
    snapshot = private / "source"
    snapshot.mkdir(mode=0o700)
    for relative in PYTHON_FILES:
        source = ROOT / relative
        require(source.exists() and source.resolve(strict=True) == source, "source_path")
        destination = snapshot / relative
        if source.is_dir():
            shutil.copytree(source, destination, ignore=shutil.ignore_patterns("__pycache__"))
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination, follow_symlinks=False)
    (snapshot / "tmp" / "s08-evidence" / run / "visual").mkdir(mode=0o700, parents=True)
    return snapshot


def snapshot_frontend(snapshot):
    source = ROOT / "frontend"
    require(source.is_dir() and source.resolve(strict=True) == source, "frontend_source")
    target = snapshot / "frontend"
    shutil.copytree(
        source,
        target,
        ignore=shutil.ignore_patterns(
            "node_modules", "test-results", "playwright-report", "dist", ".env*", ".git"
        ),
    )
    (target / "node_modules").mkdir()
    return target


def node_binary():
    candidate = shutil.which("node")
    require(candidate is not None, "node_unavailable")
    resolved = Path(candidate).resolve(strict=True)
    require(resolved.is_file() and os.access(resolved, os.X_OK), "node_unavailable")
    return resolved


def clean_environment(run, runtime, evidence, env, mode):
    seed_file = runtime / "browser-seed.json"
    minimal = {
        "PATH": "/tmp/s08-host:/usr/local/bin:/usr/bin:/bin",
        "LANG": "C.UTF-8",
    }
    minimal.update(env)
    minimal.update(
        S06_RUN="s06-" + run[-12:],
        S06_HOST_NET_NS=os.readlink("/proc/self/ns/net"),
        HOME="/tmp",
        TMPDIR="/tmp",
        PYTHONDONTWRITEBYTECODE="1",
        PYTHONPATH=os.pathsep.join(str(ROOT / path) for path in ("scripts", "backend", "tests")),
        DJANGO_SETTINGS_MODULE="config.settings",
        PLAYWRIGHT_CHROMIUM_EXECUTABLE="/opt/google/chrome/google-chrome",
        S08_BASE_URL=f"http://127.0.0.1:{DEV_PORTS['vite'] if mode == 'dev' else DEV_PORTS['api']}",
        S08_BROWSER_SEED_FILE=str(seed_file),
        S08_EVIDENCE_DIR=str(evidence),
        AQ_VITE_CACHE_DIR=str(runtime / "vite-cache"),
        AQ_DEV_BACKEND=f"http://127.0.0.1:{DEV_PORTS['api']}",
        S08_RUN=run,
    )
    return minimal


def frontend_environment(*keys):
    return {key: os.environ[key] for key in ("PATH", "LANG", "HOME", "TMPDIR", *keys)}


def sandbox_command(snapshot, runtime, evidence, env, mode, selectors):
    bwrap = shutil.which("bwrap", path="/usr/bin:/bin")
    require(bwrap is not None, "missing_bwrap")
    python = Path(sys.executable)
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
        "--ro-bind",
        "/etc/ssl/certs",
        "/etc/ssl/certs",
        "--ro-bind",
        "/etc/fonts",
        "/etc/fonts",
        "--ro-bind",
        "/usr/share/fonts",
        "/usr/share/fonts",
        "--ro-bind",
        "/etc/localtime",
        "/etc/localtime",
    ]
    if mode in BROWSER_MODES:
        require(Path("/opt/google/chrome/google-chrome").is_file(), "chrome_unavailable")
        command += ["--ro-bind", "/opt/google/chrome", "/opt/google/chrome"]
    interpreter_home = python.resolve(strict=True).parents[1]
    if not interpreter_home.is_relative_to("/usr"):
        require(interpreter_home.name.startswith("cpython-"), "unexpected_interpreter_home")
        command.extend(["--ro-bind", str(interpreter_home), str(interpreter_home)])
    # Bind explicit project entries; never expose the enclosing checkout or host /run.
    for path in snapshot.iterdir():
        command += ["--ro-bind", str(path), str(ROOT / path.name)]
    command += ["--ro-bind", str(ROOT / ".venv"), str(ROOT / ".venv")]
    modules = ROOT / "frontend" / "node_modules"
    command += ["--ro-bind", str(modules), str(modules)]
    project_evidence = root_for(env["AQ_INSTANCE_ID"])
    command += [
        "--dir",
        "/tmp/s08-host",
        "--bind",
        str(runtime),
        "/tmp/s08-host",
        "--bind",
        str(evidence),
        str(project_evidence / "visual"),
        "--chdir",
        str(ROOT),
        "--clearenv",
    ]
    child_env = clean_environment(
        env["AQ_INSTANCE_ID"], Path("/tmp/s08-host"), project_evidence / "visual", env, mode
    )
    for key, value in child_env.items():
        command.extend(["--setenv", key, value])
    return command + [
        "--",
        str(python),
        str(ROOT / "scripts/s08_browser_host.py"),
        "_sandbox",
        mode,
        *selectors,
    ]


def selectors_valid(mode, selectors):
    if mode in {"smoke", "national-profile", GOVERNANCE_MODE}:
        require(not selectors, "mode_does_not_accept_selectors")
        return
    allowed_flags = {"--grep", "--project", "--workers", "--timeout"}
    expect_value = False
    for value in selectors:
        if expect_value:
            require(
                value and not value.startswith("-") and len(value) <= 200, "invalid_selector_value"
            )
            expect_value = False
            continue
        if value in allowed_flags:
            expect_value = True
            continue
        if value.startswith("--"):
            flag, separator, inline = value.partition("=")
            require(
                flag in allowed_flags and separator and inline and len(inline) <= 200,
                "invalid_selector_flag",
            )
            continue
        require(
            value.startswith("e2e/")
            and value.endswith((".spec.ts", ".spec.js"))
            and ".." not in Path(value).parts
            and Path(value).parts[0] == "e2e",
            "selector_outside_e2e",
        )
    require(not expect_value, "selector_missing_value")


def run_host(mode, selectors):
    run = f"s08-b-{secrets.token_hex(6)}"
    root = make_root(run)
    evidence = root / "visual"
    result = {
        "run": run,
        "status": "FAIL",
        "origin": "F",
        "scope": "S08 browser host",
        "mode": mode,
        "started_utc": datetime.now(UTC).isoformat(),
        "external_collection": "off",
        "network": "isolated loopback namespace",
        "postgres": "task-owned network:none container / temporary data",
        "redis": "task-owned unix socket / port 0",
        "selectors": [
            value if value.startswith("e2e/") else value.partition("=")[0]
            for value in selectors
            if value.startswith("e2e/") or value.startswith("--")
        ],
        "cleanup": False,
    }
    private = None
    sandbox_started = False
    try:
        selectors_valid(mode, selectors)
        require((ROOT / "frontend" / "package.json").is_file(), "formal_frontend_not_ready")
        require(
            (ROOT / "frontend" / "node_modules" / "vite" / "bin" / "vite.js").is_file(),
            "vite_not_installed",
        )
        if mode in BROWSER_MODES:
            require(
                (ROOT / "frontend" / "node_modules" / "@playwright" / "test" / "cli.js").is_file(),
                "playwright_not_installed",
            )
            require(Path("/opt/google/chrome/google-chrome").is_file(), "chrome_unavailable")
        require(not container_inventory(run), "run_already_exists")
        db, role = name(run)
        result["database_identity"] = {"database": db, "role": role}
        # Linux AF_UNIX socket names must fit in 108 bytes including the terminator.
        with tempfile.TemporaryDirectory(prefix=f"{run}-", dir="/tmp") as path:
            private = directory(Path(path))
            runtime = private / "runtime"
            runtime.mkdir(mode=0o700)
            directory(runtime)
            env = host_environment(run)
            for part in ("pg", "broker", "history-stage"):
                (runtime / part).mkdir(mode=0o700)
            (runtime / "pg").chmod(0o777)
            (runtime / "dist").mkdir(mode=0o700)
            node = node_binary()
            shutil.copyfile(node, runtime / "node")
            os.chmod(runtime / "node", stat.S_IMODE(node.stat().st_mode))
            snapshot = minimal_snapshot(private, run)
            snapshot_frontend(snapshot)
            digest = hashlib.sha256()
            for item in sorted(snapshot.rglob("*")):
                if item.is_file():
                    digest.update(str(item.relative_to(snapshot)).encode() + b"\0")
                    digest.update(item.read_bytes() + b"\0")
            result["source_sha256"] = digest.hexdigest()
            command = sandbox_command(snapshot, runtime, evidence, env, mode, selectors)
            with postgres(run, runtime, env):
                sandbox_started = True
                process = subprocess.run(
                    command,
                    cwd=ROOT,
                    env=docker_environment(),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=720,
                    check=False,
                )
                result["sandbox_exit"] = process.returncode
                receipt = evidence / "sandbox.json"
                if receipt.is_file():
                    result["sandbox"] = json.loads(receipt.read_text(encoding="utf-8"))
                elif process.stderr:
                    first_line = process.stderr.splitlines()[0]
                    if first_line.startswith("bwrap:"):
                        result["sandbox_launcher_error"] = first_line[:300]
                require(process.returncode == 0, "sandbox_failed")
                require(receipt.is_file(), "sandbox_receipt_missing")
                require(result["sandbox"].get("status") == "PASS", "sandbox_failed")
                require(result["sandbox"].get("cleanup") is True, "sandbox_cleanup_incomplete")
        result["status"] = "PASS"
    except BaseException as exc:
        result["failure_type"] = type(exc).__name__
        if isinstance(exc, IsolationError):
            result["failure"] = str(exc)
    finally:
        try:
            result["cleanup"] = (
                private is not None
                and not private.exists()
                and not container_inventory(run)
                and (not sandbox_started or result.get("sandbox", {}).get("cleanup") is True)
            )
        except (IsolationError, OSError, subprocess.SubprocessError):
            result["cleanup"] = False
        if not result["cleanup"] and result["status"] == "PASS":
            result.update(status="FAIL", failure="cleanup_unconfirmed")
        result["finished_utc"] = datetime.now(UTC).isoformat()
        report = evidence / "result.json"
        report.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.chmod(report, 0o600)
    print(f"{result['scope']} {result['status']} {evidence.relative_to(ROOT)}", flush=True)
    return 0 if result["status"] == "PASS" else 1


def write_seed(path, content):
    with path.open("x", encoding="utf-8") as handle:
        os.chmod(path, 0o600)
        json.dump(content, handle, ensure_ascii=False)
        handle.write("\n")
    require(stat.S_IMODE(path.stat().st_mode) == 0o600, "seed_file_mode")


def seed_f(run):
    from s08_browser_fixture import seed_f as seed_fixture

    return seed_fixture(run)


def marker_broker(runtime, run):
    from redis import Redis

    client = Redis(
        unix_socket_path=str(runtime / "broker" / "redis.sock"),
        socket_timeout=1,
        socket_connect_timeout=1,
    )
    try:
        server = client.info("server")
        require(server["tcp_port"] == 0 and client.dbsize() == 0, "broker_identity")
        require(client.set(BROKER_MARKER, run, nx=True), "broker_marker")
        return {"socket_only": True, "redis_version": server["redis_version"]}
    finally:
        client.close()


def sandbox_run(mode, selectors):
    run = os.environ["AQ_INSTANCE_ID"]
    runtime_path = Path(os.environ["AQ_HOST_ROOT"])
    evidence = root_for(run) / "visual"
    directory(evidence)
    from s06_runtime import Runtime

    env = os.environ.copy()
    result = {
        "status": "FAIL",
        "origin": "F",
        "mode": mode,
        "started_utc": datetime.now(UTC).isoformat(),
        "services": {},
    }
    stage = "network_guard"
    runtime = Runtime(runtime_path)
    seed_path = Path(os.environ["S08_BROWSER_SEED_FILE"])
    test_output = runtime_path / "playwright-output"
    try:
        require(runtime_path == Path("/tmp/s08-host"), "sandbox_runtime")
        directory(runtime_path)
        result["network"] = check_network()
        stage = "database_guard"
        result["database_before"] = verify_database(env, run, empty=True)
        reserve_port(DEV_PORTS["api"])
        if mode == "dev":
            reserve_port(DEV_PORTS["vite"])
        with ExitStack() as services, runtime.managed():
            stage = "redis_start"
            runtime.redis()
            result["services"]["redis"] = marker_broker(runtime_path, run)
            stage = "frontend_build"
            spa_dist = runtime_path / "dist"
            vite = str(ROOT / "frontend" / "node_modules" / "vite" / "bin" / "vite.js")
            # The default config bundler writes into the read-only node_modules mount.
            vite_env = frontend_environment("AQ_VITE_CACHE_DIR", "AQ_DEV_BACKEND", "AQ_SPA_DIST")
            built = subprocess.run(
                ["/tmp/s08-host/node", vite, "build", "--configLoader", "native"],
                cwd=ROOT / "frontend",
                env=vite_env,
                capture_output=True,
                text=True,
                timeout=240,
                check=False,
            )
            if built.returncode:
                lines = (built.stdout + "\n" + built.stderr).splitlines()
                error_index = next(
                    (
                        index
                        for index, line in enumerate(lines)
                        if re.search(
                            r"(?i)(error during build|(?:^|\s)error:|failed to load|cannot find|could not resolve|not exported)",
                            line,
                        )
                    ),
                    None,
                )
                if error_index is None:
                    detail = [line for line in lines if line.strip() and " at " not in line][:6]
                else:
                    detail = [
                        line
                        for line in lines[error_index : error_index + 6]
                        if line.strip() and " at " not in line
                    ]
                result["frontend_build_diagnostic"] = [
                    line.replace(env["POSTGRES_PASSWORD"], "<redacted>").replace(
                        env["DJANGO_SECRET_KEY"], "<redacted>"
                    )[:300]
                    for line in detail
                ]
            require(built.returncode == 0, "frontend_build_failed")
            result["frontend_build"] = "PASS"

            # STATICFILES_DIRS is determined when Django first loads its settings.
            stage = "fixture_seed"
            seed = seed_f(run)
            if mode == GOVERNANCE_MODE:
                from s08_governance_browser import fixed_origin, seed_chain

                seed_chain(seed, run)
                origin_calls = services.enter_context(fixed_origin())
            write_seed(seed_path, seed)
            from django.conf import settings
            from django.core.management import call_command

            stage = "collectstatic"
            static_root = Path(env["AQ_STATIC_ROOT"])
            require(settings.SPA_DIST == spa_dist, "spa_dist_build_output_mismatch")
            require(
                settings.STATIC_ROOT == static_root == runtime_path / "staticfiles",
                "static_root_not_isolated",
            )
            call_command("collectstatic", interactive=False, verbosity=0)
            for name in ("assets", "licenses"):
                require(
                    any(path.is_file() for path in (static_root / "app" / name).rglob("*")),
                    f"collected_{name}_unavailable",
                )
            result["collectstatic"] = "PASS"
            result["frontend_paths"] = {
                "vite_out_dir": str(spa_dist),
                "django_spa_dist": str(settings.SPA_DIST),
                "static_root": str(static_root),
            }
            result["products"] = {key: int(value) for key, value in seed["products"].items()}
            result["city_count"] = len(seed["cities"])
            result["snapshot_at"] = seed["snapshot_at"]
            from accounts.models import ServicePulse
            from collection.models import Run, WorkWindow

            # Seeded historical governance receipts may exist. Configuration must
            # neither add work nor send a previously unsent request to a worker.
            def execution_counts():
                return [
                    Run.objects.count(),
                    WorkWindow.objects.count(),
                    Run.objects.exclude(request_state="unsent").count(),
                ]

            execution_before = execution_counts()
            config = runtime_path / "host.env"
            with config.open("x", encoding="utf-8") as handle:
                os.chmod(config, 0o600)
                for key, value in env.items():
                    if key in {
                        "AQ_INSTANCE_ID",
                        "AQ_HOST_ROOT",
                        "DJANGO_SECRET_KEY",
                        "POSTGRES_DB",
                        "POSTGRES_USER",
                        "POSTGRES_PASSWORD",
                        "POSTGRES_HOST",
                        "POSTGRES_PORT",
                        "CELERY_BROKER_URL",
                        "DJANGO_ALLOWED_HOSTS",
                        "COOKIE_SECURE",
                        "CNEMC_MODE",
                        "CNEMC_FIXTURE_HTTP",
                        "S03_FIXTURE_HTTP",
                        "AQ_STATIC_ROOT",
                        "AQ_SPA_DIST",
                        "WEB_PORT",
                        "AQ_WORKER_NODE",
                        "AQ_S08_FIXTURE_SCHEDULE",
                    }:
                        handle.write(f"{key}={value}\n")
            # The dedicated chain has an independent instance guard in its fixed service entry.
            saved_settings = os.environ.pop("DJANGO_SETTINGS_MODULE", None)
            try:
                stage = "web_start"

                def service_command(role):
                    if mode == GOVERNANCE_MODE:
                        return [
                            sys.executable,
                            str(ROOT / "scripts/s08_governance_browser.py"),
                            role,
                        ]
                    command = [sys.executable, str(ROOT / "main.py"), role]
                    if role == "web" and mode not in PRODUCTION_MODES:
                        command.append("--dev")
                    return [*command, "--env-file", str(config)]

                runtime.start("web", *service_command("web"))
                base_api = f"http://127.0.0.1:{DEV_PORTS['api']}"
                import httpx

                def web_ready():
                    try:
                        return (
                            httpx.get(
                                base_api + "/health/ready/", timeout=0.5, trust_env=False
                            ).status_code
                            == 200
                        )
                    except httpx.TransportError:
                        return False

                stage = "web_ready"
                wait_for("S08 Django Web", web_ready, seconds=20)
                result["services"]["web"] = "PASS"
                stage = "worker_start"
                runtime.start("worker", *service_command("worker"))

                def worker_ready():
                    if runtime.children["worker"].poll() is not None:
                        return False
                    try:
                        import django

                        django.setup()
                        from config.celery import app

                        with app.connection_for_write() as connection:
                            return bool(
                                app.control.ping(
                                    destination=[f"worker@{run}"],
                                    timeout=0.5,
                                    connection=connection,
                                )
                            )
                    except Exception:
                        return False

                stage = "worker_ready"
                wait_for("S08 Celery Worker", worker_ready, seconds=30)
                result["services"]["worker"] = "PASS"
            finally:
                if saved_settings is not None:
                    os.environ["DJANGO_SETTINGS_MODULE"] = saved_settings
            if mode == "dev":
                stage = "vite_start"
                base_url = f"http://127.0.0.1:{DEV_PORTS['vite']}"
                runtime.start(
                    "vite",
                    "/usr/bin/env",
                    "-i",
                    *(f"{key}={value}" for key, value in vite_env.items()),
                    "/tmp/s08-host/node",
                    vite,
                    str(ROOT / "frontend"),
                    "--configLoader",
                    "native",
                    "--host",
                    "127.0.0.1",
                    "--strictPort",
                    "--port",
                    str(DEV_PORTS["vite"]),
                )
                try:
                    stage = "vite_ready"

                    def vite_ready():
                        runtime.check_process(runtime.children["vite"])
                        try:
                            response = httpx.get(base_url + "/app/", timeout=1, trust_env=False)
                            return response.status_code == 200 and "/@vite/client" in response.text
                        except httpx.TransportError:
                            return False

                    wait_for("S08 Vite", vite_ready, seconds=20)
                    result["base_url"] = base_url
                    stage = "playwright"
                    result["browser_run"] = run_playwright(selectors, test_output)
                finally:
                    if "vite" in runtime.children:
                        runtime.stop("vite")
            elif mode in {"production", GOVERNANCE_MODE}:
                stage = "spa_http"
                result["base_url"] = base_api
                result["spa_http"] = smoke_spa(base_api, spa_dist)
                stage = "playwright"
                browser_selectors = [GOVERNANCE_SPEC] if mode == GOVERNANCE_MODE else selectors
                result["browser_run"] = run_playwright(browser_selectors, test_output)
                if mode == GOVERNANCE_MODE:
                    require(
                        result["browser_run"]["exit_code"] == 0
                        and result["browser_run"]["counts"]
                        == {
                            "expected": 1,
                            "unexpected": 0,
                            "skipped": 0,
                            "flaky": 0,
                        }
                        and [case["file"] for case in result["browser_run"]["cases"]]
                        == [Path(GOVERNANCE_SPEC).name],
                        "governance_browser_failed",
                    )
            elif mode == "national-profile":
                from s08_national_profile import profile

                stage = "national_profile"
                profile(base_api, seed, evidence)
            stage = "execution_guard"
            require(
                not ServicePulse.objects.filter(component="beat").exists(), "unexpected_beat_pulse"
            )
            if mode == GOVERNANCE_MODE:
                from s08_governance_browser import verify_chain

                result["execution_guard"] = verify_chain(
                    env, seed, origin_calls, execution_before, execution_counts()
                )
            else:
                require(execution_counts() == execution_before, "unexpected_runtime_collection")
                result["execution_guard"] = {
                    "unchanged_runs_windows_requests": True,
                    "beat_started": False,
                }
            stage = "database_verify"
            result["database"] = verify_database(env, run, empty=False)
            stage = "http_smoke"
            result["http"] = smoke_http(base_api, seed)
            if mode in BROWSER_MODES:
                stage = "playwright"
                browser_run = result["browser_run"]
                require(browser_run["exit_code"] == 0, "playwright_failed")
                counts = browser_run["counts"]
                require(
                    counts["expected"] > 0 and counts["unexpected"] == counts["flaky"] == 0,
                    "playwright_result_missing_or_unstable",
                )
        result["status"] = "PASS"
    except BaseException as exc:
        result["failure_type"] = type(exc).__name__
        result["failure_stage"] = stage
        result["frames"] = [
            [str(frame_path.relative_to(ROOT)), frame.lineno, frame.name]
            for frame in traceback.extract_tb(exc.__traceback__)
            if (frame_path := Path(frame.filename)).is_relative_to(ROOT)
            and not frame_path.is_relative_to(ROOT / ".venv")
        ][-10:]
        if isinstance(exc, IsolationError):
            result["failure"] = str(exc)
        raise
    finally:
        result["finished_utc"] = datetime.now(UTC).isoformat()
        result["lifecycle"] = runtime.lifecycle
        seed_path.unlink(missing_ok=True)
        GOVERNANCE_WITNESS.unlink(missing_ok=True)
        (runtime_path / "host.env").unlink(missing_ok=True)
        shutil.rmtree(test_output, ignore_errors=True)
        result["cleanup"] = (
            not runtime.children
            and not seed_path.exists()
            and not GOVERNANCE_WITNESS.exists()
            and not (runtime_path / "host.env").exists()
            and not test_output.exists()
        )
        write_seed(evidence / "sandbox.json", result)
    return result


def smoke_http(base, seed):
    import httpx

    result = {}
    with httpx.Client(base_url=base, trust_env=False, timeout=5) as client:
        ready_response = client.get("/health/ready/")
        require(
            ready_response.status_code == 200
            and ready_response.json() == {"web": "ok", "pg": "ok"},
            "web_health",
        )
        csrf = client.get("/api/v1/auth/csrf/")
        require(
            csrf.status_code == 200 and csrf.headers.get("cache-control", "").find("no-store") >= 0,
            "csrf_bootstrap",
        )
        token = csrf.json()["csrf_token"]
        account = seed["accounts"]["reader"]
        response = client.post(
            "/api/v1/auth/login/",
            json={"username": account["username"], "password": account["password"]},
            headers={"X-CSRFToken": token},
        )
        require(response.status_code == 200, "session_login")
        cookie_count = len(client.cookies)
        capabilities = client.get("/api/v1/me/capabilities/")
        require(
            capabilities.status_code == 200 and capabilities.json()["workspaces"] == ["user"],
            "capabilities",
        )
        by_shape = {}
        for shape in ("L", "C", "T", "S"):
            catalog = client.get(
                "/api/v1/catalog/objects/",
                params={
                    "scope": "synthetic",
                    "product": seed["products"][shape],
                    "page_size": 1,
                },
            )
            require(catalog.status_code == 200, f"synthetic_catalog_{shape}")
            rows = catalog.json()["results"]
            require(
                rows
                and rows[0].get("product") == seed["products"][shape]
                and rows[0].get("shape") == shape,
                f"synthetic_product_{shape}",
            )
            by_shape[shape] = rows[0]
        overview = client.get(by_shape["L"]["link"])
        require(overview.status_code == 200, "national_overview")
        result = {
            "ready": "PASS",
            "session_login": "PASS",
            "capabilities": "PASS",
            "synthetic_catalog": "PASS",
            "national_overview": "PASS",
            "cookie_count": cookie_count,
            "catalog_shapes": sorted(by_shape),
            "national_row_count": len(overview.json()["results"]),
        }
    return result


class ShellAssets(HTMLParser):
    def __init__(self):
        super().__init__()
        self.assets = []
        self.has_script = False

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "script" and ("src" in values or values.get("type") == "module"):
            self.assets.append(("script", values.get("src")))
            self.has_script = True
        if tag == "link":
            relations = (values.get("rel") or "").lower().split()
            for kind in ("stylesheet", "modulepreload"):
                if kind in relations:
                    self.assets.append((kind, values.get("href")))


def smoke_spa(base, spa_dist):
    import httpx

    with httpx.Client(base_url=base, trust_env=False, timeout=5) as client:
        shell = client.get("/app/")
        require(shell.status_code == 200, f"production_spa_shell_http_{shell.status_code}")
        require(
            shell.headers.get("server", "").lower() == "gunicorn", "production_gunicorn_missing"
        )
        require(
            shell.headers.get("content-type", "").startswith("text/html"),
            "production_spa_content_type",
        )
        require(
            shell.content == (spa_dist / "index.html").read_bytes(), "production_spa_build_mismatch"
        )
        references = ShellAssets()
        references.feed(shell.text)
        references.close()
        require(references.assets and references.has_script, "production_hashed_asset_missing")
        checked = []
        for kind, path in references.assets:
            extension = "css" if kind == "stylesheet" else "js"
            require(
                path
                and re.fullmatch(
                    rf"/static/app/assets/[A-Za-z0-9_.-]+-[A-Za-z0-9_-]{{8,}}\.{extension}", path
                ),
                "production_shell_asset_path",
            )
            if path in checked:
                continue
            built_asset = spa_dist / "assets" / Path(path).name
            require(built_asset.is_file(), "production_hashed_asset_build_missing")
            asset = client.get(path)
            require(asset.status_code == 200, "production_hashed_asset_unavailable")
            require(
                bool(asset.content) and asset.content == built_asset.read_bytes(),
                "production_hashed_asset_build_mismatch",
            )
            require(
                "immutable" in asset.headers.get("cache-control", "").lower(),
                "production_hashed_asset_not_immutable",
            )
            checked.append(path)
        return {
            "web_server": "gunicorn",
            "shell": "PASS",
            "hashed_asset": "PASS",
            "immutable_cache": "PASS",
            "asset_path": checked[0],
            "asset_paths": checked,
        }


def run_playwright(selectors, output):
    cli = ROOT / "frontend" / "node_modules" / "@playwright" / "test" / "cli.js"
    require(cli.is_file(), "playwright_package_unavailable")
    args = ["/tmp/s08-host/node", str(cli), "test", "--reporter=json", f"--output={output}"]
    args.extend(selectors)
    test_env = frontend_environment(
        "PLAYWRIGHT_CHROMIUM_EXECUTABLE",
        "S08_BASE_URL",
        "S08_BROWSER_SEED_FILE",
        "S08_EVIDENCE_DIR",
    )
    completed = subprocess.run(
        args,
        cwd=ROOT / "frontend",
        env=test_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        timeout=480,
        check=False,
    )
    # The raw reporter includes page content and credential fields on some failures.
    # Keep only counts, safe test filenames/line numbers and the failing assertion's location.
    try:
        report = json.loads(completed.stdout)
    except ValueError:
        report = {}
    counts = {
        key: report.get("stats", {}).get(key, 0)
        for key in ("expected", "unexpected", "skipped", "flaky")
    }
    cases = []
    suites = list(report.get("suites", []))
    while suites:
        suite = suites.pop()
        suites.extend(suite.get("suites", []))
        for spec in suite.get("specs", []):
            file = Path(spec["file"]).name
            if not re.fullmatch(r"[a-zA-Z0-9_-]+\.spec\.[jt]s", file):
                continue
            for test in spec["tests"]:
                for attempt in test["results"]:
                    item = {"file": file, "line": spec["line"], "status": attempt["status"]}
                    error = attempt.get("error", {})
                    location = error.get("location", {})
                    if Path(location.get("file", "")).name == file:
                        item["assertion_line"] = location.get("line")
                    if "browserType.launch" in error.get("message", ""):
                        item["phase"] = "chromium_launch"
                    cases.append(item)
    return {
        "exit_code": completed.returncode,
        "counts": counts,
        "cases": cases,
        "selectors": [
            value if value.startswith("e2e/") else value.partition("=")[0]
            for value in selectors
            if value.startswith("e2e/") or value.startswith("--")
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "mode",
        choices=("smoke", "dev", "production", "national-profile", GOVERNANCE_MODE, "_sandbox"),
    )
    parser.add_argument("selectors", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.mode == "_sandbox":
        run_mode, selectors = "smoke", []
        if args.selectors and args.selectors[0] in (BROWSER_MODES | {"national-profile"}):
            run_mode, selectors = args.selectors[0], args.selectors[1:]
        selectors_valid(run_mode, selectors)
        require(
            re.fullmatch(r"s08-b-[a-f0-9]{12}", os.environ.get("AQ_INSTANCE_ID", "")) is not None,
            "sandbox_identity",
        )
        sandbox_run(run_mode, selectors)
        return 0
    selectors_valid(args.mode, args.selectors)
    return run_host(args.mode, args.selectors)


if __name__ == "__main__":
    sys.exit(main())
