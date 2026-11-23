"""Current-code F-only HTTP, migration and asynchronous regression checks for S05."""

import argparse
import json
import os
import secrets
import selectors
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from datetime import timedelta
from hashlib import file_digest
from pathlib import Path
from uuid import uuid4
from zipfile import ZipFile

import django
import redis
from s01_runtime import FIXTURE_POST, OBS, ROOT, RUNS, wait_for
from s04_checks import (
    BASE,
    FAVORITES,
    SYNTHETIC,
    Probe,
    account_http,
    call,
    check,
    check_exact_upgrade,
    db_failure,
    detail_http,
    django_check,
    exact,
    journey,
    plans,
    private_http,
    record,
    seed,
    snapshot,
    sql,
    url,
    web,
)


def owned_database():
    from django.conf import settings

    run = os.environ.get("S05_VERIFY_RUN")
    if not run or settings.CNEMC_MODE != "off" or settings.CNEMC_FIXTURE_HTTP:
        raise RuntimeError("Use scripts/verify_s05.py with CNEMC off")
    ownership = sql("""
        SELECT current_user, current_database(), inet_server_addr() IS NULL,
               current_setting('airquality.s05_verify_run', true)
    """)
    check(ownership == [("s05_app", "s05verify", True, run)], "owned offline database")
    evidence = Path(os.environ["S05_EVIDENCE_DIR"])
    check(evidence == ROOT / "tmp" / "s05-evidence" / run, "owned evidence directory")
    return evidence


S04_GIT_COMMIT = "98548111e364654253c7957aaa68c5388d0914d0"
S04_ARCHIVE_SHA = "8ba60ddae2ef627ae9f4c661f18a722b112abb075afbc14c6724a39a9a0668e4"
S04_SOURCE_SHA = "f28cd1a7273f1fbf43321970aeba9cb1310e76369e32ffd6b1df4d859fff5b17"


@contextmanager
def s04_server(archive, evidence, password):
    # Seed using the S04 writes and serve the S04 URLConf against its actual
    # schema. The S05 policy already requires its own private tables on writes.
    with tempfile.TemporaryDirectory(dir=evidence) as directory:
        root = Path(directory)
        if archive is None:
            archive = root / "s04-source.zip"
            subprocess.run(
                [
                    "git",
                    "archive",
                    "--format=zip",
                    "--prefix=snapshot/",
                    "-o",
                    str(archive),
                    S04_GIT_COMMIT,
                    "--",
                    "Dockerfile",
                    ".dockerignore",
                    ".env.example",
                    ".python-version",
                    "compose.yaml",
                    "pyproject.toml",
                    "uv.lock",
                    "backend",
                    "tests",
                    "docker",
                    "scripts",
                ],
                cwd=ROOT,
                check=True,
                timeout=30,
            )
        else:
            with archive.open("rb") as raw:
                check(
                    file_digest(raw, "sha256").hexdigest() == S04_ARCHIVE_SHA, "frozen S04 archive"
                )
        with ZipFile(archive) as package:
            members = [name for name in package.namelist() if name.startswith("snapshot/")]
            package.extractall(root, members)
        source = root / "snapshot"
        env = os.environ | {
            "PYTHONPATH": os.pathsep.join(
                str(source / name) for name in ("backend", "tests", "scripts")
            ),
            "S05_S04_SOURCE_SHA": S04_SOURCE_SHA,
        }
        code = """
import json
import os
import sys
import django
from s01_runtime import source_hash
from s04_checks import django_check, seed, web

assert source_hash() == os.environ['S05_S04_SOURCE_SHA']
django.setup()
password = sys.stdin.readline().rstrip('\\n')
django_check('migrate', interactive=False)
ids = seed(password)
with web() as (base, _):
    print(json.dumps((base, ids)), flush=True)
    sys.stdin.read()
"""
        with subprocess.Popen(
            [sys.executable, "-c", code],
            cwd=source,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        ) as old:
            try:
                old.stdin.write(password + "\n")
                old.stdin.flush()
                with selectors.DefaultSelector() as selector:
                    selector.register(old.stdout, selectors.EVENT_READ)
                    check(selector.select(35), "frozen S04 seeded and listening")
                    result = old.stdout.readline()
                check(result, "frozen S04 source completed its seed")
                yield json.loads(result)
            finally:
                old.stdin.close()
                try:
                    old.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    old.kill()
                    old.wait(timeout=5)
                check(old.returncode == 0, "frozen S04 source exited normally")


def stable_favorite(payload):
    # S04 age_seconds is relative to request time. All other fields must match.
    if isinstance(payload, dict):
        return {
            key: stable_favorite(value) for key, value in payload.items() if key != "age_seconds"
        }
    if isinstance(payload, list):
        return [stable_favorite(value) for value in payload]
    return payload


def migration_graph(archive, evidence):
    from django.core.management import call_command
    from django.db import connection
    from django.db.migrations.loader import MigrationLoader

    password = secrets.token_urlsafe(24)
    loader = MigrationLoader(connection)
    leaves = loader.graph.leaf_nodes()
    s04 = ("accounts", "0001_initial")
    s05 = [node for node in leaves if node[0] == "accounts" and node != s04]
    check(len(s05) == 1, "one latest successor to S04 accounts")
    check(not loader.applied_migrations, "empty verification database")
    with s04_server(archive, evidence, password) as (base, ids):
        check(to_regclass("accounts_favorite"), "S04 favorite table")
        check(not to_regclass("accounts_savedquery"), "S04 schema predates saved queries")
        check(not to_regclass("accounts_exportticket"), "S04 schema predates export tickets")
        reader = Probe(base).login("s04-old-reader", password)
        operator = Probe(base).login("s04-operator", password)
        favorite = call(
            reader,
            FAVORITES,
            method="POST",
            status=201,
            body={
                "kind": "city",
                "target_id": ids["city"],
                "product": ids["products"]["C"],
                "scope": SYNTHETIC,
            },
        )
        item = url(f"{FAVORITES}{favorite['id']}/", scope=SYNTHETIC)
        window = BASE + f"admin/collection-windows/{ids['window']}/"
        observation = exact(ids["products"]["S"], ids["epoch"])
        before = snapshot()
        old_favorite = stable_favorite(call(reader, item))
        old_observation = call(reader, observation)
        old_window = call(operator, window)
    with web() as (base, _):
        reader.base = operator.base = base
        call_command("migrate", interactive=False, verbosity=0)
        django_check("check")
        django_check("makemigrations", check=True, dry_run=True, interactive=False)
        django_check("migrate", check_unapplied=True, interactive=False)
        check(to_regclass("accounts_savedquery"), "S05 saved-query table")
        check(to_regclass("accounts_exportticket"), "S05 export-ticket table")
        check(snapshot() == before, "S04 data and sessions survived upgrade")
        check(
            stable_favorite(call(reader, item)) == old_favorite,
            "old Session and Favorite remained usable",
        )
        check_exact_upgrade(old_observation, call(reader, observation))
        check(call(operator, window) == old_window, "old collection window remained usable")
        applied = MigrationLoader(connection).applied_migrations
        check(all(node in applied for node in leaves), "all migration leaves applied")
        call_command("migrate", *s04, interactive=False, verbosity=0)
        check(not to_regclass("accounts_savedquery"), "reverse migration drops saved queries")
        check(not to_regclass("accounts_exportticket"), "reverse migration drops export tickets")
        check(snapshot() == before, "S04 data survived reverse migration")
        check(
            stable_favorite(call(reader, item)) == old_favorite,
            "S04 Favorite survived reverse migration",
        )
        call_command("migrate", interactive=False, verbosity=0)
        django_check("migrate", check_unapplied=True, interactive=False)
    return {
        "result": "PASS",
        "accounts_before": s04[1],
        "accounts_after": s05[0][1],
        "leaves": leaves,
        "scope": "S04 frozen source, F rows, old Session, forward/reverse/reapply",
        "s04_source_sha256": S04_SOURCE_SHA,
        "s04_origin": S04_GIT_COMMIT if archive is None else "verified S04 review archive",
    }


def to_regclass(table):
    return sql("SELECT to_regclass(%s)", table)[0][0]


@contextmanager
def process(args, cwd=ROOT):
    with subprocess.Popen(args, cwd=cwd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) as p:
        try:
            yield p
        finally:
            if p.poll() is None:
                p.terminate()
                try:
                    p.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    p.kill()
                    p.wait(timeout=5)


def async_regression(evidence):
    from collection.models import Run, WorkWindow
    from config.celery import app
    from django.contrib.auth.models import Permission, User
    from django.utils import timezone
    from django_celery_beat.models import IntervalSchedule, PeriodicTask
    from samples.models import FixtureGrant, FixtureRun
    from samples.normalization import SOURCE

    django_check("migrate", interactive=False)
    password = secrets.token_urlsafe(24)
    ids = seed(password)
    operator = User.objects.get(pk=ids["operator"])
    operator.user_permissions.add(Permission.objects.get(codename="run_fixture"))
    for op in ("process_local", "retain"):
        FixtureGrant.objects.get_or_create(
            source=SOURCE,
            product="F",
            operation=op,
            geography="synthetic",
            audience="local_authenticated",
            defaults={"valid_from": timezone.now(), "basis": "S05 F-only regression"},
        )
    now = timezone.now()
    window = WorkWindow.objects.get(pk=ids["window"])
    run = window.runs.get()
    owner = uuid4()
    window.scope_key = f"city:{run.job.city_id}"
    window.owner = owner
    window.generation = 1
    window.lease_until = now - timedelta(seconds=2)
    window.save(update_fields=("scope_key", "owner", "generation", "lease_until"))
    run.status = "running"
    run.owner = owner
    run.generation = 1
    run.attempt = 3
    run.started_at = now - timedelta(seconds=60)
    run.save(update_fields=("status", "owner", "generation", "attempt", "started_at"))
    interval = IntervalSchedule.objects.create(every=1, period=IntervalSchedule.SECONDS)
    beat_task = PeriodicTask.objects.create(
        name="s05-recover-f-lease",
        interval=interval,
        task="collection.reconcile_due",
        queue="s03.collection",
    )
    socket = evidence / "redis.sock"
    check(len(os.fsencode(socket)) < 108, "Redis socket OS path limit")
    broker = f"redis+socket://{socket}"
    os.environ["CELERY_BROKER_URL"] = broker
    client = redis.Redis(unix_socket_path=str(socket), socket_connect_timeout=1)

    def ping():
        try:
            return client.ping()
        except redis.ConnectionError:
            return False

    celery = [sys.executable, "-m", "celery", "-A", "config", "--broker", broker]
    with process(
        [
            "redis-server",
            "--port",
            "0",
            "--unixsocket",
            str(socket),
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
        wait_for("isolated Redis socket", ping)
        check(server.poll() is None, "owned Redis process running")
        with (
            process(
                [
                    *celery,
                    "worker",
                    "--pool",
                    "solo",
                    "--concurrency",
                    "1",
                    "-Q",
                    "s01.fixture,s03.collection,s02.history",
                    "-n",
                    f"{evidence.name}@localhost",
                ]
            ) as worker,
            web() as (base, _),
        ):
            worker_name = f"{evidence.name}@localhost"
            wait_for(
                "isolated Worker", lambda: app.control.ping(destination=[worker_name], timeout=1)
            )
            http = Probe(base).login("s04-operator", password)
            baseline = Run.objects.count()
            call(
                http,
                RUNS,
                method="POST",
                body=FIXTURE_POST,
                csrf=False,
                status=403,
                code="csrf_failed",
            )
            submitted = call(http, RUNS, method="POST", body=FIXTURE_POST, status=202)
            wait_for(
                "Worker committed F fixture",
                lambda: FixtureRun.objects.filter(
                    pk=submitted["run_id"],
                    state="succeeded",
                    observation__isnull=False,
                    dispatch="sent",
                ).exists(),
            )
            check(call(http, OBS)["count"] == 1, "F fixture available over authenticated HTTP")
            check(worker.poll() is None, "owned Worker process running")
            with process(
                [*celery, "beat", "--pidfile", str(evidence / "beat.pid"), "--max-interval", "1"]
            ):
                wait_for(
                    "Beat scheduled S03 recovery and Worker committed",
                    lambda: Run.objects.filter(
                        pk=run.pk, status="expired", error_code="lease_expired"
                    ).exists(),
                )
                beat_task.refresh_from_db()
                check(beat_task.total_run_count >= 1, "Beat persisted dispatch count")
                window.refresh_from_db()
                check(
                    window.owner is None and window.lease_until is None,
                    "Worker released stale collection lease",
                )
                check(Run.objects.count() == baseline, "no F recovery Run or real collection added")
    client.close()
    return {
        "result": "PASS",
        "redis": "Unix socket, TCP disabled",
        "worker": "S01 CSRF-protected HTTP to fixture committed",
        "beat": "S03 stale lease expired",
        "collection_run_delta": 0,
    }


@contextmanager
def restarted_web():
    code = """
import sys
import django
from s04_checks import web

django.setup()
with web() as (base, _):
    print(base, flush=True)
    sys.stdin.read()
"""
    with subprocess.Popen(
        [sys.executable, "-c", code], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True
    ) as child:
        try:
            with selectors.DefaultSelector() as ready:
                ready.register(child.stdout, selectors.EVENT_READ)
                check(bool(ready.select(timeout=10)), "isolated Web process ready")
            base = child.stdout.readline().strip()
            check(base.startswith("http://127.0.0.1:"), "loopback Web endpoint")
            yield base
        finally:
            child.stdin.close()
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)


def private_restart():
    from accounts.models import ExportTicket, SavedQuery
    from accounts.research_exports import export_fields
    from django.contrib.auth.models import User
    from observations.policy import clock
    from observations.tasks import expire_research
    from s05_support import HOUR, START, authorize, candidate, city, ingest, product

    django_check("migrate", interactive=False)
    password = secrets.token_urlsafe(24)
    operator = User.objects.create_superuser("s05-restart-op")
    User.objects.create_user("s05-restart-reader", password=password)
    source_product = product("s05-restart", "C")
    target = city(source_product)
    authorize(operator, source_product, detail_fields=export_fields("detail"))
    ingest(operator, source_product, [candidate(source_product, "001")])
    query = {
        "selection": {
            "product": source_product.pk,
            "source": source_product.source.namespace,
            "scope": "synthetic",
            "cities": [target.pk],
            "indicators": ["CO"],
            "start": START.isoformat(),
            "end": (START + HOUR).isoformat(),
        },
        "method": "detail",
        "method_version": "s05.v1",
        "grain": "day",
    }
    saved = BASE + "me/saved-queries/"
    exports = BASE + "me/exports/"
    with restarted_web() as base:
        reader = Probe(base).login("s05-restart-reader", password)
        stamp = call(reader, BASE + "analysis/details/", method="POST", body=query)["input_version"]
        item = call(
            reader,
            saved,
            method="POST",
            status=201,
            body=query | {"method": "statistics", "title": "S05 F restart"},
        )
        ticket = call(
            reader,
            exports,
            method="POST",
            status=201,
            body={"kind": "detail", "groups": [query | {"input_version": stamp}]},
        )
    download = url(exports + f"{ticket['id']}/download/", scope=SYNTHETIC)
    run = url(saved + f"{item['id']}/run/", scope=SYNTHETIC)
    with restarted_web() as base:
        reader.base = base
        status, _, headers = reader.request(download)
        check(
            status == 200 and headers.get_content_type() == "text/csv",
            "frozen ticket survives restart",
        )
        check(
            call(reader, run, body={}, method="POST")["state"] == "current",
            "saved intent survives restart",
        )
        now = clock()
        ExportTicket.objects.filter(pk=ticket["id"]).update(
            created_at=now - 2 * HOUR, expires_at=now - HOUR
        )
        SavedQuery.objects.filter(pk=item["id"]).update(baseline_until=now - HOUR)
        call(reader, download, status=410, code="expired")
        expire_research()
        call(reader, download, status=404, code="not_found")
        retained = SavedQuery.objects.get(pk=item["id"])
        check(
            not retained.baseline
            and not retained.baseline_basis
            and retained.baseline_until is None,
            "expired baseline and supporting metadata cleared, intent retained",
        )
    return {"result": "PASS", "old_session_after_restart": 200, "expired": 410, "swept": 404}


def s04_http_regression(result):
    from django.contrib.auth.models import Permission, User

    result["runtime"] = {"django": django.get_version(), "pg": sql("SHOW server_version")[0][0]}
    result["cases"] = []
    django_check("migrate", interactive=False)
    password = secrets.token_urlsafe(24)
    ids = seed(password)
    with web() as (base, queries):
        reader = account_http(base, ids, password)
        result["cases"].append({"check": "registration, Session and CSRF", "result": "PASS"})
        record(
            result,
            "precise and historical detail",
            "independent CO SQL match, explicit gaps and pagination",
            lambda: {"co_rows_match_independent_sql": detail_http(reader, ids)},
        )
        new_password = private_http(base, reader, ids, password)
        result["cases"].append(
            {
                "check": "profile, password change, permission withdrawal and old Session rejection",
                "result": "PASS",
            }
        )
        User.objects.get(pk=ids["operator"]).user_permissions.add(
            Permission.objects.get(
                content_type__app_label="observations",
                codename="review_observation",
            )
        )
        record(
            result,
            "F read journey",
            "L → C/T → directory → station → window → Favorite → new Session",
            lambda: journey(reader, base, ids, new_password, password),
        )
        record(
            result,
            "S04 SQL plans",
            "bounded S04 queries with small and expanded F datasets",
            lambda: plans(reader, ids, queries),
        )
        record(
            result,
            "PG outage",
            "unavailable connection yields 503 and recovers",
            lambda: db_failure(reader, ids),
        )
    result["result"] = "PASS"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("s04-http", "migration", "async", "fresh", "restart"))
    parser.add_argument(
        "--s04-archive",
        type=Path,
        help="Use a fingerprint-verified S04 review ZIP instead of the local frozen Git commit",
    )
    args = parser.parse_args()
    if args.s04_archive is not None and not args.s04_archive.is_file():
        parser.error("the requested S04 review archive is missing")
    django.setup()
    evidence = owned_database()
    case_dir = evidence / ("V18" if args.phase in {"migration", "fresh", "restart"} else "V15")
    case_dir.mkdir(exist_ok=True)
    result = {}
    try:
        if args.phase == "migration":
            result = migration_graph(args.s04_archive, evidence)
        elif args.phase == "async":
            result = async_regression(evidence)
        elif args.phase == "restart":
            result = private_restart()
        elif args.phase == "fresh":
            django_check("migrate", interactive=False)
            django_check("check")
            django_check("makemigrations", check=True, dry_run=True, interactive=False)
            django_check("migrate", check_unapplied=True, interactive=False)
            from django.db import connection
            from django.db.migrations.loader import MigrationLoader

            result = {"result": "PASS", "leaves": MigrationLoader(connection).graph.leaf_nodes()}
        else:
            s04_http_regression(result)
    except Exception as exc:
        result.update(result="FAIL", error=type(exc).__name__)
        raise
    finally:
        (case_dir / f"{args.phase}.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2)
        )
    print(f"{args.phase}: {result['result']}", flush=True)


if __name__ == "__main__":
    main()
