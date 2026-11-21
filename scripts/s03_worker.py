"""Run one test worker with a barrier after task execution, before its acknowledgement."""

import os
import signal
from pathlib import Path

from celery.signals import task_postrun, task_prerun
from config.celery import app


@task_prerun.connect
def record_active(sender=None, task_id=None, **_kwargs):
    if sender is not None and sender.name == "collection.execute_run":
        Path("/tmp/s03-active-task").write_text(f"{os.getpid()} {task_id}\n")


@task_postrun.connect
def before_ack(sender=None, task_id=None, **_kwargs):
    if sender is None or sender.name != "collection.execute_run":
        return
    arm = Path("/tmp/s03-after-task")
    if arm.exists() and arm.read_text().strip() in {task_id, "next"}:
        arm.unlink()
        Path("/tmp/s03-before-ack").write_text(f"{os.getpid()} {task_id}\n")
        os.kill(os.getpid(), signal.SIGSTOP)


if __name__ == "__main__":
    app.worker_main(
        [
            "worker",
            "-l",
            "INFO",
            "-Q",
            "s03.collection",
            "--concurrency=1",
            "--prefetch-multiplier=1",
            "-n",
            "collection-b@%h",
        ]
    )
