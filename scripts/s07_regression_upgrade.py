#!/usr/bin/env python3
"""V15 forward migration of a frozen S06 fixture inside the guarded R instance."""

import argparse
import json
import os
import secrets
import subprocess
import sys
import tarfile
import tempfile
from contextlib import contextmanager
from io import BytesIO
from pathlib import Path

import verify_s06
from s01_runtime import ROOT
from s06_guard import IsolationError, check_database, check_environment, require
from s06_sandbox import BASELINE_MOUNT, check_network

BASELINE = "8de867853c470393617bc7ee14bb692a97fe8c5c"
BASELINE_SOURCE = "598e00c2db1d39918b2e5eecb64f35956de26c38c4ebed944401c34caa66c632"
USERNAME = "s07-upgrade-fixture"
SCRIPT = ROOT / "scripts/s07_regression_upgrade.py"


@contextmanager
def baseline(root, receipt):
    files = (*verify_s06.SOURCE_FILES, *verify_s06.SOURCE_DIRS)
    archive = subprocess.check_output(
        ["git", "archive", "--format=tar", BASELINE, "--", *files], cwd=ROOT, timeout=20
    )
    with tempfile.TemporaryDirectory(prefix="s07-baseline-", dir=root) as directory:
        tree = Path(directory)
        with tarfile.open(fileobj=BytesIO(archive), mode="r:") as source:
            source.extractall(tree, filter="data")
        digest = subprocess.check_output(
            [
                sys.executable,
                "-IBc",
                "import runpy, sys; print(runpy.run_path(sys.argv[1])['source_hash']())",
                str(tree / "scripts/s01_runtime.py"),
            ],
            cwd=tree,
            timeout=10,
            text=True,
        ).strip()
        require(digest == BASELINE_SOURCE, "s06_frozen_source_fingerprint")
        receipt["baseline"] = {"commit": BASELINE, "source": digest, "mounted_subtree": "backend"}
        yield tree / "backend"


def database_snapshot():
    from django.db import connection, transaction

    with transaction.atomic(), connection.cursor() as query:
        query.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        query.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY 1")
        snapshots = {}
        for (table,) in query.fetchall():
            name = connection.ops.quote_name(table)
            query.execute(
                f"""SELECT count(*), md5(coalesce(string_agg(row::text, E'\n' ORDER BY row::text), ''))
                    FROM (SELECT to_jsonb(t) AS row FROM {name} AS t) AS rows"""
            )
            snapshots[table] = list(query.fetchone())
        return snapshots


def migration_state():
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor

    executor = MigrationExecutor(connection)
    leaves = executor.loader.graph.leaf_nodes()
    return {
        "leaves": sorted(map(list, leaves)),
        "pending": [
            [migration.app_label, migration.name, backwards]
            for migration, backwards in executor.migration_plan(leaves)
        ],
    }


def seed():
    import django
    from django.core.management import call_command

    django.setup()
    import config.settings
    from accounts.models import AccountState
    from collection.models import Job, Run, WorkWindow
    from django.contrib.auth import get_user_model
    from observations.models import CityRevision, DispositionJob
    from observations.policy import revoke_grant
    from s05_support import HOUR, START, authorize, candidate, city, ingest, product
    from s07_contract_upgrade import csrf, parse, session

    require(Path(config.settings.__file__).is_relative_to(BASELINE_MOUNT), "wrong_s06_seed_code")
    call_command("migrate", interactive=False, verbosity=0)
    password = secrets.token_urlsafe(24)
    user = get_user_model().objects.create_user(USERNAME, password=password, is_superuser=True)
    AccountState.objects.create(user=user)
    item = product("s07-forward-C", "C")
    owner = city(item)
    rights = authorize(user, item)
    ingest(user, item, [candidate(item, token=1, value="10")])
    ingest(user, item, [candidate(item, token=2, value="11")])
    require(CityRevision.objects.filter(key__product=item).count() == 2, "seed_revisions")
    job = Job.objects.create(product=item, executor=user, city=owner, profile="f-exact-v1")
    window = WorkWindow.objects.create(
        product=item,
        scope_key="F-city",
        profile=job.profile,
        target_start=START,
        target_end=START + HOUR,
        closed_at=START + HOUR,
        result="complete",
    )
    Run.objects.create(
        window=window,
        job=job,
        actor=user,
        executor=user,
        reason="manual",
        status="finished",
        result="complete",
        started_at=START,
        finished_at=START + HOUR,
    )
    revoke_grant(user.pk, rights["retain_normalized"].pk, "F upgrade disposition witness")
    job = DispositionJob.objects.get(product=item, reason="revocation")
    require(
        job.completed_at is None and job.boundary_grant_id is not None, "seed_pending_disposition"
    )
    with session() as client:
        csrf(client)
        response = client.post(
            "/api/v1/auth/login/", json={"username": USERNAME, "password": password}
        )
        parse(response, 200, "s06_fixture_login")
        require(
            parse(client.get("/api/v1/me/"), 200, "s06_session")["username"] == USERNAME,
            "s06_session_owner",
        )
        cookie = client.cookies.get("sessionid")
    require(cookie, "s06_missing_session")
    return {"session": cookie, "database": database_snapshot(), "migrations": migration_state()}


def upgrade(result, env):
    import django
    from django.core.management import call_command
    from s07_contract_upgrade import session

    require(os.statvfs(BASELINE_MOUNT).f_flag & os.ST_RDONLY, "s06_baseline_mount_not_read_only")
    old = subprocess.run(
        [sys.executable, "-B", str(SCRIPT), "--old"],
        cwd=BASELINE_MOUNT,
        env=env
        | {
            "PYTHONPATH": os.pathsep.join(
                map(str, (BASELINE_MOUNT, ROOT / "scripts", ROOT / "tests"))
            )
        },
        check=False,
        capture_output=True,
        text=True,
        timeout=75,
    )
    require(old.returncode == 0, f"s06_writers_fixture_exit_{old.returncode}")
    original = json.loads(old.stdout)
    result["old_writers_exited"] = True
    result["s06"] = original["migrations"]
    require(not result["s06"]["pending"], "s06_pending_migrations")
    django.setup()
    result["s07_before"] = migration_state()
    require(all(not step[-1] for step in result["s07_before"]["pending"]), "backward_migration")
    call_command("migrate", interactive=False, verbosity=0)
    result["s07_first"] = migration_state()
    require(not result["s07_first"]["pending"], "pending_s07_migrations")
    first = database_snapshot()
    call_command("migrate", interactive=False, verbosity=0)
    result["s07_second"] = migration_state()
    require(result["s07_first"] == result["s07_second"], "second_migration_drift")
    after = database_snapshot()
    require(after == first, "second_migration_row_drift")
    before = original["database"]
    result["tables"] = {
        table: {
            "s06_rows": before[table][0] if table in before else None,
            "s07_rows": after[table][0] if table in after else None,
            "contents_preserved": before.get(table) == after.get(table),
        }
        for table in sorted(before.keys() | after.keys())
    }
    require(
        all(table["contents_preserved"] for table in result["tables"].values()), "s07_row_drift"
    )
    with session() as client:
        client.cookies.set("sessionid", original["session"])
        response = client.get("/api/v1/me/")
        result["old_session_http"] = response.status_code
        require(
            response.status_code == 200 and response.json()["username"] == USERNAME,
            "s07_session_preservation",
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inside", choices=("upgrade",), help=argparse.SUPPRESS)
    parser.add_argument("--old", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.old or args.inside == "upgrade":
        env = os.environ.copy()
        root = check_environment(env)
        check_network()
        check_database(env, expected_marker=env["S06_RUN"], empty=True)
        if args.old:
            require(Path.cwd() == BASELINE_MOUNT, "s06_seed_cwd")
            print(json.dumps(seed()), flush=True)
            return 0
        result = {"scope": "V15-R-forward", "origin": "F", "status": "FAIL"}
        try:
            upgrade(result, env)
            result["status"] = "PASS"
        except Exception as error:
            result["error"] = (
                str(error) if isinstance(error, IsolationError) else type(error).__name__
            )
        finally:
            (root / "upgrade.json").write_text(json.dumps(result, indent=2) + "\n")
        return int(result["status"] != "PASS")
    require(args.inside is None, "invalid_regression_entry")
    sandbox = verify_s06.sandbox_command

    def command(env, snapshot, action, *selectors, old=None):
        argv = sandbox(env, snapshot, action, *selectors, old=old)
        if action != "upgrade":
            return argv
        require(argv[-2:] == [str(ROOT / "scripts/s06_sandbox.py"), "upgrade"], "sandbox_entry")
        return [*argv[:-2], str(SCRIPT), "--inside", "upgrade"]

    verify_s06.baseline_code = baseline
    verify_s06.sandbox_command = command
    sys.argv[1:] = ["upgrade"]
    return verify_s06.main()


if __name__ == "__main__":
    sys.exit(main())
