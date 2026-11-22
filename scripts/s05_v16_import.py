"""Deliver the official S02 staged import to an isolated Redis/Worker over Unix sockets."""

import os
import subprocess
import sys
from pathlib import Path

import django
import redis
from s01_runtime import ROOT, wait_for
from s04_checks import check
from s05_runtime_checks import owned_database, process


def import_stdin(actor):
    from django.conf import settings
    from observations.history import stage_uci, submit
    from observations.models import ImportAttempt

    owned_database()
    stage_dir = Path(settings.S02_STAGE_DIR)
    check(stage_dir.is_mount(), "private stage tmpfs")
    stage = stage_uci(actor, sys.stdin.buffer)
    with process(
        [
            sys.executable,
            "-m",
            "celery",
            "-A",
            "config",
            "worker",
            "--pool=solo",
            "--concurrency=1",
            "-Q",
            "s02.history",
            "--without-mingle",
            "--without-gossip",
            "--without-heartbeat",
            "-n",
            "v16@localhost",
        ]
    ) as worker:
        attempt, dispatch = submit(
            actor,
            {
                "stage_id": str(stage),
                "profile": "uci501-edges-v1",
                "reason": "historical_verification",
            },
        )
        check(dispatch == "sent", "S02 published to owned Redis")
        wait_for(
            "S02 historical reconciliation",
            lambda: ImportAttempt.objects.filter(pk=attempt).exclude(state="pending").exists(),
            seconds=120,
        )
        row = ImportAttempt.objects.select_related("stage").get(pk=attempt)
        check(
            worker.poll() is None and row.state == "succeeded" and row.deliveries == 1,
            f"S02 Worker completed import ({row.state}, {row.error_code})",
        )
        wait_for(
            "stage cleared",
            lambda: (
                not any(stage_dir.iterdir())
                and ImportAttempt.objects.filter(pk=attempt, stage__state="cleared").exists()
            ),
        )


def run_import(evidence, actor, raw):
    """Only stdin carries original bytes. Neither the source directory nor evidence is mounted."""
    socket_dir = evidence / "pg"
    sock = socket_dir / "v16.sock"
    client = redis.Redis(unix_socket_path=str(sock), socket_connect_timeout=1)

    def ready():
        try:
            return client.ping()
        except redis.ConnectionError:
            return False

    image = subprocess.check_output(
        ["docker", "image", "inspect", "--format", "{{.Id}}", "air-quality-platform-app:local"],
        text=True,
    ).strip()
    uid, gid = os.getuid(), os.getgid()
    with process(
        [
            "redis-server",
            "--port",
            "0",
            "--unixsocket",
            str(sock),
            "--unixsocketperm",
            "700",
            "--save",
            "",
            "--appendonly",
            "no",
            "--dir",
            str(evidence),
        ]
    ) as server:
        wait_for("owned redis", ready)
        check(server.poll() is None, "owned Redis running")
        args = [
            "docker",
            "run",
            "--rm",
            "-i",
            "--pull=never",
            "--network=none",
            "--read-only",
            "--label",
            f"airquality.s05.verification={evidence.name}",
            "--user",
            f"{uid}:{gid}",
            "--tmpfs",
            f"/run/s02-history:rw,noexec,nosuid,nodev,size=32m,mode=0700,uid={uid},gid={gid}",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,nodev,size=16m,mode=1777",
            "-v",
            f"{socket_dir}:/run/postgresql:ro",
            "-w",
            "/workspace",
            "-e",
            "POSTGRES_HOST=/run/postgresql",
            "-e",
            "CELERY_BROKER_URL=redis+socket:///run/postgresql/v16.sock",
            "-e",
            f"S05_EVIDENCE_DIR=/workspace/tmp/s05-evidence/{evidence.name}",
            "-e",
            "PYTHONPATH=/workspace/backend:/workspace/tests:/workspace/scripts",
        ]
        for folder in ("backend", "scripts", "tests"):
            args += ["-v", f"{ROOT / folder}:/workspace/{folder}:ro"]
        for key in (
            "DJANGO_SETTINGS_MODULE",
            "DJANGO_SECRET_KEY",
            "POSTGRES_USER",
            "POSTGRES_DB",
            "POSTGRES_PASSWORD",
            "POSTGRES_PORT",
            "CNEMC_MODE",
            "S05_VERIFY_RUN",
        ):
            args += ["-e", key]
        subprocess.run(
            [*args, image, "python", "scripts/s05_v16_import.py", str(actor)],
            input=raw,
            check=True,
            timeout=180,
        )
    return image


if __name__ == "__main__":
    django.setup()
    import_stdin(int(sys.argv[1]))
