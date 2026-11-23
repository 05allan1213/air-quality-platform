"""T09 on a V01-verified Compose project. All database writes are limited to its new F dataset."""

import hashlib
import json
import os
import platform
import re
import secrets
import shutil
import socket
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from time import monotonic, sleep

from s06_guard import IsolationError, require
from s07_compose_runtime import applications
from s07_handoff import checkout
from s07_performance import (
    BUDGET,
    HOT_SAMPLES,
    TIMEOUT,
    batch,
    cold_batches,
    login,
    stability,
    verify,
)
from verify_s07 import ROOT, check_config, source_digest


def fingerprint(payload):
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def pin_loopback_port(project):
    # A Docker port of 0 is allocated again on restart. Fix this one run's port so
    # the already authenticated clients retain the same origin during cold probes.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        port = str(listener.getsockname()[1])
    project.env["WEB_PORT"] = port
    cfg = json.loads(project.compose("config", "--format", "json"))
    check_config(cfg, project.name)
    require(str(cfg["services"]["web"]["ports"][0]["published"]) == port, "performance_port")


def effective_configuration(project, apps, modes, pg):
    services = {}
    for name in ("db", "redis", "migrate", "web", "worker", "beat"):
        if name in apps.ids:
            cid = apps.ids[name]
            info = apps.check(name, cid)
        else:
            cid = project.containers[name]
            info = project.owned_container(name, cid)
        require(name == "migrate" or info["State"]["Running"], f"{name}_not_running")
        cfg = info["HostConfig"]
        services[name] = {
            "argv": [info["Path"], *info["Args"]],
            "process_1_argv": project.docker("exec", cid, "cat", "/proc/1/cmdline")
            .strip("\0")
            .split("\0")
            if info["State"]["Running"]
            else [],
            "memory_limit_bytes": cfg["Memory"],
            "nano_cpus": cfg["NanoCpus"],
            "cpu_quota": cfg["CpuQuota"],
            "cpu_period": cfg["CpuPeriod"],
            "cpuset": cfg["CpusetCpus"],
            "network_count": len(info["NetworkSettings"]["Networks"]),
        }
    web = apps.ids["web"]
    actual = services["web"]["process_1_argv"]
    require(actual[1:4] == ["-m", "gunicorn", "config.wsgi:application"], "performance_wsgi")
    versions = project.docker(
        "exec",
        "--user",
        "10001:10001",
        web,
        "python",
        "-c",
        "import importlib.metadata as m, json, platform; "
        "print(json.dumps({'python':platform.python_version(), **{k:m.version(k) "
        "for k in ('gunicorn','Django','celery','psycopg','httpx')}}))",
    )
    stable_services = json.loads(json.dumps(services).replace(project.name[5:], "<run>"))
    effective = {
        "compose_file_order": [str(path.relative_to(ROOT)) for path in project.files],
        "config_files": project.receipt["config_files"],
        "reviewed_inputs": {
            k: project.receipt[k]
            for k in (
                "source_sha256",
                "lock_sha256",
                "migration_files_sha256",
                "images",
                "docker_version",
                "compose_version",
            )
        },
        "services": stable_services,
        "versions": json.loads(versions),
        "wsgi_network": project.receipt["wsgi_network"],
        "pg_settings": pg["pg_settings"],
        "django_settings": pg["django"],
        "client": {
            "same_origin": "http://127.0.0.1:<assigned-port>",
            "compression": "identity",
            "cache_policy": "no-store",
            "pool_max_connections": 1,
            "pool_keepalive_expiry_s": 30,
            "redirect_following": False,
            "request_timeout_s": TIMEOUT,
        },
    }
    cpuinfo = Path("/proc/cpuinfo").read_text()
    model = re.search(r"^model name\s*:\s*(.+)$", cpuinfo, re.M)
    docker_dir = Path(project.docker("info", "--format", "{{.DockerRootDir}}"))
    cgroup = Path("/sys/fs/cgroup")
    return {
        "project": project.name,
        "modes": modes,
        "image": apps.image,
        "loopback_origin": apps.http,
        "effective": effective,
        "effective_sha256": fingerprint(effective),
        "live_services": services,
        "checkout": checkout(ROOT),
        "host": {
            "model": model[1] if model else platform.machine(),
            "kernel": platform.release(),
            "logical_cpus": os.cpu_count(),
            "affinity_cpus": len(os.sched_getaffinity(0)),
            "docker": project.docker(
                "info", "--format", "{{.OSType}}/{{.Architecture}} {{.NCPU}} {{.MemTotal}}"
            ),
            "cgroup_limits": {
                file: (cgroup / file).read_text().strip()
                for file in ("cpu.max", "memory.max", "cpuset.cpus.effective")
                if (cgroup / file).is_file()
            },
            "docker_storage": shutil.disk_usage(docker_dir)._asdict(),
        },
    }


def measure(project, mode):
    folder = project.root / "performance"
    folder.mkdir(mode=0o700)
    password = secrets.token_urlsafe(36)

    def write(name, payload):
        with (folder / (name + ".json")).open("x") as output:
            json.dump(payload, output, separators=(",", ":"))
        print(f"{project.name}/performance/{name}", flush=True)

    with applications(project) as apps:
        apps.start("migrate")
        pin_loopback_port(project)
        web = apps.start("web")
        base = apps.ready()
        apps.start("worker")
        apps.start("beat")

        def fixture(script, *args, stdin=None, timeout=900):
            command = project.cmd + [
                "exec",
                "--user",
                "10001:10001",
                "-i",
                "--env",
                "AQ_INSTANCE_ID",
                "--env",
                "S07_PERF_PASSWORD",
                web,
                "python",
                "-B",
                f"scripts/s07_performance_{script}.py",
                *args,
            ]
            result = subprocess.run(
                command,
                cwd=project.root,
                env=project.env | {"AQ_INSTANCE_ID": project.name, "S07_PERF_PASSWORD": password},
                input=json.dumps(stdin) if stdin else None,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            if result.returncode:
                frames = re.findall(
                    r'File "/app/((?:scripts|backend|tests)/[a-zA-Z0-9_./]+\.py)", line (\d+)',
                    result.stderr,
                )
                codes = re.findall(
                    r"^(?:s06_guard.IsolationError|s07_performance_oracle.Mismatch|RuntimeError): ([a-z_0-9]{1,80})$",
                    result.stderr,
                    re.M,
                )
                types = re.findall(
                    r"^([a-zA-Z0-9_.]+(?:Error|Violation|Mismatch)):", result.stderr, re.M
                )
                write(
                    "-".join((script, *args, "failure")),
                    {
                        "exit": result.returncode,
                        "codes": codes,
                        "types": types,
                        "frames": frames[-10:],
                    },
                )
                raise IsolationError(f"performance_{script}_failed")
            return json.loads(result.stdout)

        ids = {**project.containers, **{name: apps.ids[name] for name in ("web", "worker", "beat")}}

        def resources():
            output = project.docker(
                "stats", "--no-stream", "--format", "{{json .}}", *ids.values(), timeout=30
            )
            return {
                "docker": [json.loads(line) for line in output.splitlines()],
                "host_loadavg": os.getloadavg(),
                "observed_utc": datetime.now(UTC).isoformat(),
            }

        def runtime_sample():
            pg = fixture("diagnostics", "runtime")
            pulses = pg["service_pulse_age_s"]
            require(
                set(pulses) == {"worker", "beat"}
                and all(0 <= age <= 30 for age in pulses.values()),
                "performance_background_pulse",
            )
            return resources() | {"postgres": pg}

        phases = ["heat", "stable", "cold"] if mode == "heat" else [mode]
        write(
            "configuration",
            effective_configuration(project, apps, phases, fixture("diagnostics", "runtime")),
        )
        manifest = fixture("seed", *(["--smoke"] if mode == "smoke" else []))
        write("manifest", manifest)
        require(
            manifest["logical_hours"] == (400 if mode == "smoke" else 1_000_000),
            "fixture_cardinality",
        )
        if mode == "smoke":
            return
        sql = fixture("oracle", stdin=manifest)
        write("oracle", sql)
        with login(base, manifest, password) as (readers, auth):
            write("five-sessions", auth)
            write("correctness", verify(readers, manifest, sql))
            if mode == "diagnose":
                write("diagnostics", fixture("diagnostics", "explain", stdin=manifest))
                with ThreadPoolExecutor(max_workers=2) as pool:
                    waits = pool.submit(fixture, "diagnostics", "monitor")
                    usage = pool.submit(resources)
                    write("r4-probe", batch(readers, "R4", n=5, warm=2))
                    write("r4-pg-waits", waits.result())
                    write("r4-resources", usage.result())
                return
            if mode == "check":
                return

            if mode in ("heat", "reverse"):
                shapes = list(BUDGET) if mode == "heat" else list(reversed(BUDGET))
                failed = []
                for i, shape in enumerate(shapes, 1):
                    write(f"{i}-{shape}-before", runtime_sample())
                    start = datetime.now(UTC).isoformat()
                    result = batch(readers, shape, n=HOT_SAMPLES)
                    write(
                        f"{i}-{shape}",
                        result | {"start_utc": start, "end_utc": datetime.now(UTC).isoformat()},
                    )
                    write(f"{i}-{shape}-after", runtime_sample())
                    if result["status"] != "PASS":
                        failed.append(shape)
                require(not failed, "hot_budget_or_data_failed")

            if "stable" in phases:
                finished = Event()

                def monitor_stability():
                    minute = 0
                    while not finished.is_set():
                        write(f"stability-resources-{minute:02d}", runtime_sample())
                        minute += 1
                        finished.wait(60)

                with ThreadPoolExecutor(max_workers=1) as pool:
                    monitor = pool.submit(monitor_stability)
                    observed_start = datetime.now(UTC).isoformat()
                    try:
                        observed = stability(readers)
                    finally:
                        finished.set()
                    monitor.result()
                write(
                    "stability",
                    observed
                    | {
                        "start_utc": observed_start,
                        "end_utc": datetime.now(UTC).isoformat(),
                    },
                )
                require(observed["status"] == "PASS", "stability_failed")

            if "cold" in phases:
                passed = cold_batches(
                    readers, restarts=restart_for_cold(project, apps, base), emit=write
                )
                require(passed, "cold_batches_failed")

        require(source_digest() == project.receipt["source_sha256"], "performance_source_changed")


def restart_for_cold(project, apps, base):
    web, beat = apps.ids["web"], apps.ids["beat"]
    db = project.containers["db"]
    for shape in BUDGET:
        for _ in range(3):
            old_web = apps.check("web", web)
            old_db = project.owned_container("db", db)
            apps.check("beat", beat)
            project.docker("stop", "--time", "12", beat, web)
            project.docker("stop", "--time", "12", db)
            begin = monotonic()
            started_utc = datetime.now(UTC).isoformat()
            project.docker("start", db)
            deadline = monotonic() + 30
            while True:
                new_db = project.owned_container("db", db)
                if new_db["State"]["Health"]["Status"] == "healthy":
                    break
                require(monotonic() < deadline, "pg_restart_timeout")
                sleep(0.25)
            pg_ready = monotonic()
            project.docker("start", beat)
            project.docker("start", web)
            require(apps.ready() == base, "cold_http_origin_changed")
            web_ready = monotonic()
            new_web = apps.check("web", web)
            require(
                all(
                    old["State"]["StartedAt"] != new["State"]["StartedAt"]
                    for old, new in (
                        (old_web, new_web),
                        (old_db, new_db),
                    )
                ),
                "cold_process_unchanged",
            )
            yield (
                shape,
                {
                    "started_utc": started_utc,
                    "pg_ready_ms": (pg_ready - begin) * 1000,
                    "web_ready_ms": (web_ready - pg_ready) * 1000,
                    "db_process_started_utc": new_db["State"]["StartedAt"],
                    "web_process_started_utc": new_web["State"]["StartedAt"],
                    "beat_restarted_after_pg_ready": True,
                    "preflight": [
                        "pg_isready",
                        "GET /health/ready/",
                        "container ownership and isolation",
                    ],
                    "temperature": "PG buffers and WSGI restarted; kernel page cache retained",
                },
            )


if __name__ == "__main__":
    sys.exit("Use scripts/verify_s07.py performance <mode>")
