#!/usr/bin/env python3
"""Run the S08 schedule boundary probe in a disposable S07-B instance."""

import json
import os
import secrets
import shutil
import subprocess
import sys
from datetime import UTC, datetime

from s01_runtime import ROOT
from s06_guard import IsolationError, require
from s07_host_guard import check_database, directory
from verify_s07_host import (
    cluster,
    environment,
    freeze_source,
    host_negative,
    inventory,
    make_root,
    register_startup_lock,
    sandbox_command,
)


def host(scenario):
    run = f"s07-b-{secrets.token_hex(6)}"
    root = make_root(run)
    env = environment(run, root)
    target = root / "V01" / "result.json"
    receipt = {
        "run": run,
        "scope": "S08-schedule-B",
        "scenario": scenario,
        "origin": "F/I",
        "status": "FAIL",
        "started_utc": datetime.now(UTC).isoformat(),
        "rejections": [],
        "cleanup": False,
    }
    print(target.relative_to(ROOT), flush=True)
    try:
        require(not any(inventory(run).values()), "run_exists")
        with freeze_source(root) as (snapshot, digest), cluster(env, receipt):
            receipt["source_sha256"] = digest
            receipt["database_before"] = check_database(env, empty=True)
            host_negative(env, receipt)
            lock_dir = root / "V02"
            lock_dir.mkdir(mode=0o700)
            (lock_dir / "beat.lock").touch(mode=0o444, exist_ok=False)
            register_startup_lock(env, receipt)
            # Reuse the V03 mounts and locks with a fixed, local sandbox entry point.
            args = sandbox_command(env, snapshot, "singleton")[:-3] + [
                str(ROOT / "scripts/s08_schedule_runtime.py"),
                "_sandbox",
                scenario,
            ]
            receipt["sandbox_exit"] = subprocess.run(
                args, cwd=ROOT, env=env, check=False, timeout=180
            ).returncode
            observed = root / "V01" / "schedule.json"
            if observed.exists():
                receipt["sandbox"] = json.loads(observed.read_text())
            require(receipt["sandbox_exit"] == 0, "sandbox_probe_failed")
            require(receipt["sandbox"]["status"] == "PASS", "sandbox_receipt_failed")
    except (IsolationError, OSError, subprocess.SubprocessError, ValueError) as exc:
        receipt["error"] = str(exc) if isinstance(exc, IsolationError) else type(exc).__name__
    finally:
        try:
            receipt["after"] = inventory(run)
            require(not any(receipt["after"].values()), "resources_remaining")
            for name in ("pg", "stage", "broker"):
                path = root / name if name == "pg" else directory(root / name, 0o700)
                require(path.resolve(strict=True) == path and path.is_dir(), "cleanup_path")
                shutil.rmtree(path)
            receipt["cleanup"] = set(p.name for p in root.iterdir()) == {"V01", "V02"}
            receipt["cleanup"] &= receipt.get("sandbox", {}).get("remaining_processes") == 0
        except (IsolationError, OSError, subprocess.SubprocessError) as exc:
            receipt["cleanup_error"] = type(exc).__name__
        if "error" not in receipt and receipt["cleanup"]:
            receipt["status"] = "PASS"
        receipt["finished_utc"] = datetime.now(UTC).isoformat()
        target.write_text(json.dumps(receipt, indent=2) + "\n")
    print(f"S08 schedule runtime {receipt['status']}")
    return int(receipt["status"] != "PASS")


def main():
    scenarios = {"boundary", "pause-source", "pause-product"}
    if sys.argv[1:] == []:
        return host("boundary")
    if len(sys.argv) == 2 and sys.argv[1] in scenarios:
        return host(sys.argv[1])
    from s08_schedule_runtime_cases import sandbox, worker

    if len(sys.argv) == 3 and sys.argv[1] == "_sandbox" and sys.argv[2] in scenarios:
        return sandbox(os.environ, sys.argv[2])
    if len(sys.argv) == 4 and sys.argv[1] == "_worker" and sys.argv[3] in {"scan", "all"}:
        return worker(os.environ, int(sys.argv[2]), scan_only=sys.argv[3] == "scan")
    raise SystemExit("invalid S08 schedule runtime command")


if __name__ == "__main__":
    raise SystemExit(main())
