#!/usr/bin/env python3
"""Run the H segment rehearsal in a fresh R environment and retain a public receipt."""

import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPLAY = Path(__file__).resolve().parent
ROOT = REPLAY.parents[3]
sys.path.insert(0, str(ROOT / "scripts"))

import verify_s06  # noqa: E402
from h_restore_custody import custodian, license_evidence  # noqa: E402
from s06_guard import check_environment, owned_root  # noqa: E402
from s06_restore_provision import target_bootstrap  # noqa: E402

ARCHIVE_BYTES = 7_959_991
ARCHIVE_SHA256 = "d1b9261c54132f04c374f762f1e5e512af19f95c95fd6bfa1e8ac7e927e3b0b8"
FILES = ("h_case.py", "h_restore.py", "h_restore_case.py", "h_restore_custody.py")


def migrate_targets(container, env):
    sql = target_bootstrap(env) + target_bootstrap(env, "2")
    verify_s06.docker(
        "exec",
        "-i",
        container,
        "sh",
        "-ec",
        'PGPASSWORD="$POSTGRES_PASSWORD" exec psql -Xq -v ON_ERROR_STOP=1 '
        "-h /run/postgresql -U postgres -d template1",
        payload=sql.as_string(),
    )


def freeze_replay(source):
    frozen = source / "replay"
    frozen.mkdir()
    hashes = {}
    for name in FILES:
        path = REPLAY / name
        verify_s06.require(path.resolve(strict=True) == path, "invalid_h_replay_path")
        data = path.read_bytes()
        (frozen / name).write_bytes(data)
        hashes[name] = hashlib.sha256(data).hexdigest()
    return hashes


def guarded_command(env, source, published):
    verify_s06.require(check_environment(env) == Path(env["S06_WORK_ROOT"]), "h_rehearsal_root")
    command = verify_s06.sandbox_command(env, source, "probe")
    end = command.index("--")
    return command[:end] + [
        "--ro-bind",
        str(published),
        "/s07-h-authority",
        "--size",
        str(32 * 1024 * 1024),
        "--perms",
        "0700",
        "--tmpfs",
        str(Path(env["S06_WORK_ROOT"]) / "stage"),
        "--",
        str(Path(sys.executable).absolute()),
        str(ROOT / "replay" / "h_restore_case.py"),
    ]


def verify(args, evidence, receipt):
    receipt["phase"] = "license_review"
    receipt["license_review"] = license_evidence(args.uci_metadata, args.license_metadata)
    with args.archive.open("rb") as archive:
        size = os.fstat(archive.fileno()).st_size
        verify_s06.require(
            size == ARCHIVE_BYTES
            and hashlib.file_digest(archive, "sha256").hexdigest() == ARCHIVE_SHA256,
            "h_archive_identity",
        )
        archive.seek(0)
        receipt["input_material"] = {
            "bytes": size,
            "source_sha256": ARCHIVE_SHA256,
            "profile": "uci501-edges-v1",
        }
        run = receipt["run"]
        receipt["phase"] = "isolated_environment"
        root = verify_s06.make_root(run)
        env = verify_s06.environment(run, root, secrets.token_hex(32))
        try:
            with (
                verify_s06.freeze_source(root) as (source, digest),
                verify_s06.cluster(env, receipt) as (container, image),
            ):
                receipt["source_before"] = digest
                receipt["replay_sources"] = freeze_replay(source)
                verify_s06.probe(env, receipt, source)
                verify_s06.isolation(verify_s06.inspect(container), run, container, image)
                receipt["guard"] = "PASS"
                receipt["phase"] = "isolated_targets"
                migrate_targets(container, env)
                receipt["restore_target"] = {
                    "databases": 2,
                    "separate_role": True,
                    "socket_only": True,
                }
                with tempfile.TemporaryDirectory(prefix=f"{run}-custody-", dir=evidence) as holder:
                    custody_dir = Path(holder)
                    with custodian(root / "custody.sock", custody_dir, env, receipt) as (
                        published,
                        key,
                    ):
                        env.update(
                            S06_BACKUP_DIR=str(root / "backup"),
                            S06_AUTHORITY_DIR="/s07-h-authority",
                            S07_H_KEY_PROBE=str(key),
                        )
                        receipt["phase"] = "application"
                        child = subprocess.run(
                            guarded_command(env, source, published),
                            cwd=ROOT,
                            env=env,
                            stdin=archive,
                            capture_output=True,
                            text=True,
                            timeout=420,
                        )
                        receipt["child_exit"] = child.returncode
                        try:
                            receipt["application"] = json.loads(child.stdout.splitlines()[-1])
                        except (ValueError, IndexError):
                            receipt["application"] = {"result": "FAIL", "phase": "sandbox_exec"}
                        verify_s06.require(
                            child.returncode == 0 and receipt["application"]["result"] == "PASS",
                            "h_application_failed",
                        )
                receipt["custody_media_cleared"] = not custody_dir.exists()
        finally:
            remaining = verify_s06.docker(
                "ps", "-aq", "--filter", f"label={verify_s06.LABEL}={run}"
            )
            verify_s06.require(not remaining, "owned_postgres_remaining")
            receipt["remaining_containers"] = 0
            verify_s06.require(owned_root(run) == root, "owned_runtime_root")
            shutil.rmtree(root)
            receipt["runtime_root_cleared"] = not root.exists()
    receipt["phase"] = "complete"
    receipt["result"] = "PASS"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--uci-metadata", type=Path, required=True)
    parser.add_argument("--license-metadata", type=Path, required=True)
    args = parser.parse_args()
    run = f"s06-{secrets.token_hex(6)}"
    evidence = ROOT / "tmp" / "s07-h-supplement" / f"R-{run}"
    evidence.mkdir(mode=0o700, parents=True)
    receipt = {
        "result": "FAIL",
        "guard": "NOT RUN",
        "run": run,
        "origin": "H",
        "coverage": ["V11", "T10 segment recovery"],
        "rejected": [],
    }
    try:
        verify(args, evidence, receipt)
    except Exception as exc:
        text = str(exc)
        receipt["failure"] = text if re.fullmatch(r"[a-z0-9_]{1,64}", text) else type(exc).__name__
    finally:
        receipt_path = evidence / "restore.json"
        receipt_path.write_text(json.dumps(receipt, ensure_ascii=True, indent=2) + "\n")
        print(f"{receipt['result']} {receipt_path.relative_to(ROOT)}", flush=True)
    return int(receipt["result"] != "PASS")


if __name__ == "__main__":
    sys.exit(main())
