#!/usr/bin/env python3
"""Run S04 reconciliation in a disposable, offline PostgreSQL network namespace."""

import argparse
import json
import os
import secrets
import subprocess
import tempfile
from pathlib import Path

from s01_runtime import ROOT, source_hash, wait_for


def run(args, **kwargs):
    return subprocess.run(args, cwd=ROOT, timeout=600, **kwargs)


def verify(image, archive, phase):
    prefix = f"s04-verify-{secrets.token_hex(6)}"
    evidence = ROOT / "tmp" / "s04-evidence" / prefix
    evidence.mkdir(parents=True, mode=0o700)
    label = f"airquality.verification={prefix}"
    env = {
        "DJANGO_SECRET_KEY": secrets.token_hex(48),
        "S04_VERIFY_RUN": prefix,
        "POSTGRES_USER": "postgres",
        "POSTGRES_PASSWORD": secrets.token_hex(32),
        "POSTGRES_DB": "s04verify",
        "POSTGRES_HOST": "127.0.0.1",
        "POSTGRES_PORT": "5432",
        "DJANGO_SETTINGS_MODULE": "config.settings",
        "DJANGO_ALLOWED_HOSTS": "127.0.0.1",
        "CNEMC_MODE": "off",
        "CNEMC_FIXTURE_HTTP": "0",
        "S03_FIXTURE_HTTP": "0",
        "PYTHONPATH": "/workspace/backend:/workspace/tests",
    }
    digest = source_hash()
    result = {}
    db_id = None
    redact = [env["DJANGO_SECRET_KEY"], env["POSTGRES_PASSWORD"]]
    try:
        with tempfile.TemporaryDirectory(dir=evidence) as directory:
            env_file = Path(directory) / "env"
            env_file.write_text("".join(f"{key}={value}\n" for key, value in env.items()))
            env_file.chmod(0o600)
            boot = run(
                [
                    "docker",
                    "run",
                    "-d",
                    "--pull=never",
                    "--network=none",
                    "--name",
                    prefix,
                    "--label",
                    label,
                    "--env-file",
                    str(env_file),
                    "--tmpfs",
                    "/var/lib/postgresql/data:rw,size=512m",
                    "postgres:16-alpine",
                    "-c",
                    "listen_addresses=127.0.0.1",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            db_id = boot.stdout.strip()
            wait_for(
                "owned PostgreSQL listener",
                lambda: (
                    run(
                        [
                            "docker",
                            "exec",
                            db_id,
                            "pg_isready",
                            "-h",
                            "127.0.0.1",
                            "-d",
                            env["POSTGRES_DB"],
                        ],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    ).returncode
                    == 0
                ),
            )
            # Install the extension as owner, then the Web and SQL probe use its ordinary role.
            run(
                [
                    "docker",
                    "exec",
                    "-i",
                    db_id,
                    "psql",
                    "-Xq",
                    "-v",
                    "ON_ERROR_STOP=1",
                    "-U",
                    "postgres",
                    "-d",
                    env["POSTGRES_DB"],
                ],
                input=(
                    "CREATE EXTENSION btree_gist;\n"
                    f"ALTER DATABASE s04verify SET airquality.s04_verify_run = '{prefix}';\n"
                    f"CREATE ROLE s04_app LOGIN PASSWORD '{env['POSTGRES_PASSWORD']}';\n"
                    f"ALTER DATABASE {env['POSTGRES_DB']} OWNER TO s04_app;\n"
                ),
                text=True,
                check=True,
                capture_output=True,
            )
            env_file.write_text(
                env_file.read_text().replace("POSTGRES_USER=postgres", "POSTGRES_USER=s04_app")
            )
            mounts = [
                "-v",
                f"{ROOT}/backend:/workspace/backend:ro",
                "-v",
                f"{ROOT}/tests:/workspace/tests:ro",
                "-v",
                f"{ROOT}/scripts:/workspace/scripts:ro",
            ]
            command = [
                "docker",
                "run",
                "--rm",
                "--pull=never",
                "--label",
                label,
                "--network",
                f"container:{db_id}",
                "--env-file",
                str(env_file),
                "-w",
                "/workspace",
                *mounts,
            ]
            executable = [image, "python", "scripts/s04_checks.py", "--phase"]
            manifest = "null"
            if archive:
                with archive.open("rb") as dataset:
                    imported = run(
                        [
                            *command,
                            "-i",
                            "--name",
                            prefix + "-import",
                            "--tmpfs",
                            "/run/s02-history:rw,noexec,nosuid,nodev,size=32m,mode=0700,uid=10001,gid=10001",
                            *executable,
                            "h-import",
                        ],
                        stdin=dataset,
                        capture_output=True,
                    )
                if imported.returncode:
                    result["runner_exit_code"] = imported.returncode
                    result["cases"] = [{"check": "V16/H importer", "result": "FAIL"}]
                    diagnostic = imported.stderr.decode(errors="replace")
                    for secret in redact:
                        diagnostic = diagnostic.replace(secret, "[redacted]")
                    error_path = evidence / "private-error.log"
                    error_path.touch(mode=0o600)
                    error_path.write_text(diagnostic[-16000:])
                    return 1
                manifest = json.dumps(json.loads(imported.stdout))
            verified = run(
                [*command, "-i", "--name", prefix + "-checks", *executable, phase],
                input=manifest,
                text=True,
                capture_output=True,
            )
            # The inner runner emits only its structured public test receipt to stdout.
            # Skip arbitrary command/client output on failure.
            try:
                result.update(json.loads(verified.stdout))
            except (ValueError, TypeError):
                result["runner_error"] = "inner checks produced no structured receipt"
            result["runner_exit_code"] = verified.returncode
            if verified.returncode:
                # Private diagnostic excludes credentials; may contain database details.
                diagnostic = verified.stderr
                for secret in redact:
                    diagnostic = diagnostic.replace(secret, "[redacted]")
                error_path = evidence / "private-error.log"
                error_path.touch(mode=0o600)
                error_path.write_text(diagnostic[-16000:])
    finally:
        if db_id:
            run(["docker", "rm", "-f", db_id], check=True, capture_output=True)
        remaining = run(
            ["docker", "ps", "-aq", "--filter", f"label={label}"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.split()
        result.update(
            source_sha256=digest,
            source_unchanged=source_hash() == digest,
            cleanup_remaining_containers=len(remaining),
            phase=phase,
            network="isolated loopback namespace, no worker or beat",
        )
        receipt = evidence / "checks.json"
        receipt.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
        print(receipt.relative_to(ROOT), flush=True)
    cases = result.get("cases", [])
    if result.get("runner_exit_code") or not result["source_unchanged"] or remaining:
        return 1
    return 2 if any(case["result"] == "NOT RUN" for case in cases) else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-image", default="air-quality-platform-app:local")
    parser.add_argument("--uci-archive", type=Path, help="approved local UCI inner ZIP")
    parser.add_argument("--phase", choices=("baseline", "h", "s04"), default="s04")
    args = parser.parse_args()
    archive = args.uci_archive
    if archive:
        archive = archive.resolve(strict=True)
        if not archive.is_file() or not os.access(archive, os.R_OK):
            parser.error("UCI archive must be an accessible local regular file")
    if args.phase == "h" and archive is None:
        parser.error("--phase h requires --uci-archive")
    return verify(args.app_image, archive, args.phase)


if __name__ == "__main__":
    raise SystemExit(main())
