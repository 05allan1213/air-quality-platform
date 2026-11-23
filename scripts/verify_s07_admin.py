#!/usr/bin/env python3
"""Run P05's F/I regression and V17 trends in the isolated A Compose environment."""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from xml.etree import ElementTree

import psycopg
from s06_guard import IsolationError, require
from s07_compose_runtime import applications
from verify_s07 import ComposeRun, identities

TESTS = "tests/test_s07_operations.py"


def verify_test_database(instance):
    db, role = identities(instance)
    require(
        os.environ.get("POSTGRES_DB") == db
        and os.environ.get("POSTGRES_USER") == role
        and os.environ.get("POSTGRES_HOST", "db") == "db"
        and os.environ.get("CNEMC_MODE", "off") == "off",
        "v17_fixture_environment",
    )
    with (
        psycopg.connect(
            dbname=f"test_{db}",
            user=role,
            password=os.environ["POSTGRES_PASSWORD"],
            host="db",
            options="-c default_transaction_read_only=on",
        ) as conn,
        conn.cursor() as sql,
    ):
        sql.execute("""
            SELECT current_database(), current_user, current_setting('airquality.s07_instance'),
                   (SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public')
            """)
        require(sql.fetchone() == (f"test_{db}", role, instance, 0), "v17_fixture_database")


def inside(instance):
    from s06_sandbox import summarize_pytest

    verify_test_database(instance)
    with tempfile.TemporaryDirectory(prefix="s07-admin-") as directory:
        xml = Path(directory) / "pytest.xml"
        proc = subprocess.run(
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
                f"--junitxml={xml}",
                "-q",
                TESTS,
            ],
            capture_output=True,
            timeout=100,
            check=False,
        )
        result = {"exit_code": proc.returncode}
        if xml.exists():
            counts, cases = summarize_pytest(xml)
            result.update(counts=counts, cases=cases)
            # This F/I-only test constructs its witness from preselected synthetic bucket
            # counters and emits no login, grant, source row, or stage material.
            node = ElementTree.parse(xml).find(
                ".//testcase[@name='test_v17_trends_match_independent_sql']/properties/"
                "property[@name='s06_observed']"
            )
            if node is not None and len(node.attrib["value"]) < 8192:
                result["trend_buckets"] = json.loads(node.attrib["value"])
        print(json.dumps(result), flush=True)


def prepare_test_database(project):
    require(
        re.fullmatch(r"[a-z0-9_]+", project.db) and re.fullmatch(r"[a-z0-9_]+", project.role),
        "v17_fixture_identity",
    )
    # The production settings point at the application's uniquely labelled DB.
    # pytest-django uses its separate test_ database in the same disposable cluster.
    project.owned_container("db", project.containers["db"])
    project.docker(
        "exec",
        project.containers["db"],
        "psql",
        "-Xq",
        "-v",
        "ON_ERROR_STOP=1",
        "-U",
        "postgres",
        "-d",
        "postgres",
        "-c",
        f"CREATE DATABASE test_{project.db} OWNER {project.role} TEMPLATE template0",
    )


def run(project):
    receipt = {"scope": "P05/V17-A", "origins": ["F", "I"], "result": "FAIL"}
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
                "scripts/verify_s07_admin.py",
                "--inside",
                project.name,
                timeout=120,
            )
            suite = json.loads(output)
            receipt["suite"] = suite
            require(
                suite["exit_code"] == 0 and suite["counts"] == {"PASS": 5, "FAIL": 0, "NOT RUN": 0},
                "v17_fixture_suite",
            )
            require("trend_buckets" in suite, "v17_fixture_witness")
        receipt["result"] = "PASS"
    except Exception as error:
        receipt["error"] = str(error) if isinstance(error, IsolationError) else type(error).__name__
        raise
    finally:
        (project.root / "admin-trends.json").write_text(json.dumps(receipt, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inside")
    args = parser.parse_args()
    if args.inside is not None:
        require(re.fullmatch(r"s07a-[a-f0-9]{12}", args.inside), "v17_fixture_instance")
        try:
            inside(args.inside)
        except Exception as error:
            action = str(error) if isinstance(error, IsolationError) else type(error).__name__
            print(json.dumps({"exit_code": 1, "error": action}), flush=True)
    else:
        with ComposeRun().guarded() as project:
            run(project)


if __name__ == "__main__":
    main()
