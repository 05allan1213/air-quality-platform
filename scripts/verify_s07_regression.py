#!/usr/bin/env python3
"""V15 static checks, guarded A suite and R restoration-node reconciliation."""

import argparse
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
from pathlib import Path
from xml.etree import ElementTree

from s01_runtime import ROOT
from s06_guard import IsolationError, require
from s06_sandbox import summarize_pytest
from s07_compose_runtime import applications
from verify_s07 import ComposeRun, source_digest
from verify_s07_admin import prepare_test_database, verify_test_database

RECOVERY_FILES = {"tests/test_s06_backup.py", "tests/test_s06_backup_preparation.py"}
A_SKIP_REASONS = {
    "use s06_restore_verify.py": "requires the independently owned R restoration targets",
    "requires an owned S05 or S06 verification cluster": "volume fixtures require the R socket guard",
}


def invoke(command, *, timeout=90):
    result = subprocess.run(
        command, cwd=ROOT, capture_output=True, text=True, check=False, timeout=timeout
    )
    return {
        "command": command,
        "exit_code": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def write_result(root, result):
    path = root / "regression.json"
    path.write_text(json.dumps(result, indent=2) + "\n")
    print(path.relative_to(ROOT), result["status"], flush=True)


def static_checks():
    root = ROOT / "tmp/s07-evidence" / f"s07-static-{secrets.token_hex(6)}"
    for parent in (root.parent.parent, root.parent):
        parent.mkdir(mode=0o700, exist_ok=True)
        require(
            parent.resolve(strict=True) == parent
            and parent.stat().st_uid == os.getuid()
            and not parent.stat().st_mode & 0o022,
            "static_evidence_root",
        )
    root.mkdir(mode=0o700)
    tools = [
        [".venv/bin/ruff", "--version"],
        [".venv/bin/mypy", "--version"],
        ["uv", "--version"],
        [sys.executable, "--version"],
    ]
    checks = [
        [".venv/bin/ruff", "check", "--no-cache", "backend", "tests", "scripts", "main.py"],
        [
            ".venv/bin/ruff",
            "format",
            "--check",
            "--no-cache",
            "backend",
            "tests",
            "scripts",
            "main.py",
        ],
        [".venv/bin/mypy", "--cache-dir=/dev/null"],
        ["uv", "--no-cache", "lock", "--check", "--offline"],
        ["git", "diff", "--check"],
        ["git", "diff", "--cached", "--check"],
    ]
    result = {"status": "FAIL", "scope": "V15-static", "source": source_digest(), "checks": []}
    try:
        for command in [*tools, *checks]:
            result["checks"].append(invoke(command))
        require(all(item["exit_code"] == 0 for item in result["checks"]), "static_check_failed")
        require(result["source"] == source_digest(), "static_source_drift")
        result["status"] = "PASS"
    except (IsolationError, OSError, subprocess.SubprocessError) as error:
        result["error"] = str(error) if isinstance(error, IsolationError) else type(error).__name__
    finally:
        write_result(root, result)
    return int(result["status"] != "PASS")


def compose_inside(instance):
    verify_test_database(instance)
    commands = (
        [sys.executable, "-B", "backend/manage.py", "check"],
        [sys.executable, "-B", "backend/manage.py", "makemigrations", "--check", "--dry-run"],
        [sys.executable, "-B", "backend/manage.py", "migrate", "--plan"],
    )
    checks = [invoke(command) for command in commands]
    result = {"checks": checks, "exit_code": 1}
    with tempfile.TemporaryDirectory(prefix="s07-regression-") as directory:
        xml = Path(directory) / "pytest.xml"
        process = subprocess.run(
            [
                sys.executable,
                "-B",
                "-m",
                "pytest",
                "-p",
                "s06_sandbox",
                "--reuse-db",
                "-o",
                "junit_family=xunit1",
                "-o",
                "junit_logging=no",
                f"--junitxml={xml}",
                "-q",
            ],
            cwd=ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=1200,
        )
        result["exit_code"] = process.returncode
        if xml.is_file():
            result["counts"], result["cases"] = summarize_pytest(xml)
            nodes = ElementTree.parse(xml).findall("testsuite/testcase")
            for node, case in zip(nodes, result["cases"], strict=True):
                if case["status"] != "NOT RUN":
                    continue
                reason = node.find("skipped").get("message", "").removeprefix("Skipped: ")
                case["skip_reason"] = A_SKIP_REASONS.get(reason, "unclassified")
    print(json.dumps(result), flush=True)


def compose():
    with ComposeRun().guarded() as project:
        receipt = {"scope": "V15-A", "origin": "F/I", "status": "FAIL"}
        try:
            with applications(project) as apps:
                prepare_test_database(project)
                apps.start("migrate")
                web = apps.start("web")
                apps.ready()
                apps.check("web", web)
                receipt["image"] = apps.image
                output = project.docker(
                    "exec",
                    "--user",
                    "10001:10001",
                    "--env",
                    "PYTHONPATH=/app/backend:/app/tests:/app/scripts",
                    web,
                    "python",
                    "-B",
                    "scripts/verify_s07_regression.py",
                    "--inside",
                    project.name,
                    timeout=1250,
                )
                result = json.loads(output)
                receipt["suite"] = result
                require(
                    result["exit_code"] == 0 and result.get("counts", {}).get("PASS"),
                    "a_suite_failed",
                )
                require(
                    all(check["exit_code"] == 0 for check in result["checks"]), "a_django_check"
                )
                require(
                    "No planned migration operations" in result["checks"][-1]["stdout"],
                    "a_pending_migrations",
                )
                require(
                    all(case.get("skip_reason") != "unclassified" for case in result["cases"]),
                    "a_unclassified_skip",
                )
            require(
                project.receipt["source_sha256"] == source_digest(), "a_regression_source_drift"
            )
            receipt["status"] = "PASS"
        except Exception as error:
            receipt["error"] = (
                str(error) if isinstance(error, IsolationError) else type(error).__name__
            )
            raise
        finally:
            write_result(project.root, receipt)
    return 0


def r_suite(run):
    require(re.fullmatch(r"s06-[0-9a-f]{12}", run), "invalid_regression_run")
    root = ROOT / "tmp/s06-evidence" / run
    require(root.resolve(strict=True) == root, "regression_run_path")
    guard = json.loads((root / "V01/result.json").read_text())
    outcome = json.loads((root / "pytest.json").read_text())
    require(
        guard["run"] == outcome["run"] == run
        and guard["guard"] == guard["result"] == "PASS"
        and guard["cleanup"]
        and not guard["remaining_containers"]
        and outcome["exit_code"] == 0
        and guard["source_before"] == outcome["source"],
        "unverified_r_suite",
    )
    nodes = {case["nodeid"]: case["status"] for case in outcome["cases"]}
    require(
        len(nodes) == len(outcome["cases"]) and "pytest_session" not in nodes, "r_node_identity"
    )
    require(not outcome["summary"]["FAIL"], "failed_r_tests")
    return guard, outcome, nodes


def reconcile(full_run, restore_run):
    general, full, full_nodes = r_suite(full_run)
    restore, exclusive, exclusive_nodes = r_suite(restore_run)
    require(general["source_before"] == restore["source_before"], "r_suite_baseline_mismatch")
    require(full["selectors"] == ["tests"] and general["action"] == "pytest", "not_full_suite")
    require(
        set(exclusive["selectors"]) == RECOVERY_FILES
        and restore["restore_target"]["target_count"] == 2,
        "not_restoration_suite",
    )
    recovered = {node for node, state in exclusive_nodes.items() if state == "PASS"}
    skipped = {node for node, state in full_nodes.items() if state == "NOT RUN"}
    require(
        len(recovered) == len(exclusive_nodes) == 33
        and skipped == recovered
        and all(node.split("::")[0] in RECOVERY_FILES for node in skipped),
        "r_restore_set_mismatch",
    )
    print(
        json.dumps(
            {
                "scope": "V15-R",
                "origin": "F/I",
                "status": "PASS",
                "source": full["source"],
                "full": {"run": full_run, "counts": full["summary"]},
                "restore": {"run": restore_run, "counts": exclusive["summary"]},
                "covered": len(full_nodes),
                "skip_resolution": [
                    {
                        "nodeid": node,
                        "reason": A_SKIP_REASONS["use s06_restore_verify.py"],
                        "restore_evidence": restore_run,
                    }
                    for node in sorted(skipped)
                ],
                "unmatched_nodes": [],
            },
            indent=2,
        ),
        flush=True,
    )
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--compose", action="store_true")
    action.add_argument("--static", action="store_true")
    action.add_argument("--reconcile", nargs=2, metavar=("FULL_RUN", "RESTORE_RUN"))
    action.add_argument("--inside", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.static:
        return static_checks()
    if args.compose:
        return compose()
    if args.reconcile:
        return reconcile(*args.reconcile)
    try:
        compose_inside(args.inside)
    except Exception as error:
        code = str(error) if isinstance(error, IsolationError) else type(error).__name__
        print(json.dumps({"exit_code": 1, "error": code}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
