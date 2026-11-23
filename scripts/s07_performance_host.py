"""Supplementary V13 run within the existing V01-B sandbox and Redis lifecycle."""

import json
import os
import platform
import secrets
import socket
import sys

import httpx
from s01_runtime import wait_for
from s06_guard import IsolationError, require
from s07_performance import run
from s07_performance_diagnostics import diagnose
from s07_performance_diagnostics import runtime as postgres_snapshot
from s07_performance_oracle import Mismatch, collect
from s07_performance_seed import guard, seed, setup_django


def measure(root, runtime):
    setup_django()
    from django.core.management import call_command

    guard()
    folder = root / "performance"
    folder.mkdir(mode=0o700)

    reports = {}

    def emit(name, result):
        with (folder / (name + ".json")).open("x") as file:
            json.dump(result, file, ensure_ascii=False, default=str)
        if "shape" in result and "summary" in result:
            reports[result["shape"]] = result["summary"] | {
                "warmup_errors": sum(row["error"] is not None for row in result["warmup"]),
                "within_hot_budget": result["status"] == "PASS",
            }

    password = secrets.token_urlsafe(32)
    os.environ["S07_PERF_PASSWORD"] = password
    step = "migrate"
    try:
        call_command("migrate", interactive=False, verbosity=0)
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        step = "seed"
        manifest = seed()
        emit("manifest", manifest)
        step = "oracle"
        oracle = collect(manifest)
        emit("oracle", oracle)
        step = "web"
        command = [
            sys.executable,
            "-m",
            "gunicorn",
            "config.wsgi:application",
            "--config",
            "python:config.gunicorn_local",
            "--bind",
            f"127.0.0.1:{port}",
        ]
        runtime.start("web", *command)
        from config import gunicorn_local

        available = os.statvfs(root)
        emit(
            "configuration",
            {
                "argv": ["python", *command[1:]],
                "network": "private loopback",
                "python": platform.python_version(),
                "kernel": platform.release(),
                "machine": platform.machine(),
                "available_cpu_ids": sorted(os.sched_getaffinity(0)),
                "evidence_disk_available_bytes": available.f_bavail * available.f_frsize,
                "wsgi": {
                    "workers": gunicorn_local.workers,
                    "worker_class": gunicorn_local.worker_class,
                    "threads": gunicorn_local.threads,
                },
            },
        )
        base = f"http://127.0.0.1:{port}"

        def ready():
            try:
                return (
                    httpx.get(base + "/health/ready/", timeout=0.5, trust_env=False).status_code
                    == 200
                )
            except httpx.TransportError:
                return False

        wait_for("S07 B Web", ready)
        step = "http_measurement"
        emit("pg-before", postgres_snapshot())
        run(base, manifest, oracle, mode="B", emit=emit, password=password)
        emit("pg-after", postgres_snapshot())
        step = "diagnostics"
        emit("diagnostics", diagnose(manifest))
        correct = len(reports) == 4 and all(
            row["N"] == row["correct"] == 50
            and row["overlap"]["max"] == 5
            and row["warmup_errors"] == 0
            for row in reports.values()
        )
        emit(
            "result",
            {
                "status": "PASS" if correct else "FAIL",
                "shapes": reports,
                "origin": "F",
                "run": manifest["run"],
            },
        )
        require(correct, "v13_host_sample_or_content")
    except Exception as exc:
        if not (folder / "result.json").exists():
            emit(
                "result",
                {
                    "status": "FAIL",
                    "step": step,
                    "error_type": type(exc).__name__,
                    "check": str(exc) if isinstance(exc, (Mismatch, IsolationError)) else None,
                    "sqlstate": getattr(getattr(exc, "__cause__", None), "sqlstate", None),
                },
            )
        raise
    finally:
        os.environ.pop("S07_PERF_PASSWORD", None)
