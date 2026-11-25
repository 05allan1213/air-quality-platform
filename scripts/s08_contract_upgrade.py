#!/usr/bin/env python3
"""Verify the frozen S07 backend's F data and sessions across the S08 forward migration."""

import argparse
import hashlib
import json
import os
import secrets
import subprocess
import sys
import tarfile
import tempfile
from contextlib import contextmanager
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path

import verify_s06
from s01_runtime import ROOT
from s06_guard import check_database, check_environment, require
from s06_sandbox import BASELINE_MOUNT, check_network
from s07_contract_upgrade import (
    DETAILS,
    EXPORTS,
    SAVED,
    SCOPE,
    STATISTICS,
    csrf,
    csv_download,
    diagnostic,
    parse,
    session,
)
from s07_regression_upgrade import database_snapshot, migration_state

BASELINE = "a3fde5b2239fbd55e83e54ad0006e034b26f57a1"  # s07-backend-v1
SCRIPT = ROOT / "scripts/s08_contract_upgrade.py"
ROLES = {
    "manager": ("manage_collection",),
    "reviewer": ("review_observation",),
    "runner": ("run_collection",),
    "reader": (),
}
NEW_PERMISSIONS = {
    ("accounts", "accountstate", "maintain_city_search_alias"),
    ("observations", "product", "configure_collection_schedule"),
}
ME = "/api/v1/me/"
NATIONAL = "/api/v1/overview/national/"
CATALOG = "/api/v1/catalog/objects/"
JOBS = "/api/v1/admin/collection-jobs/"
RUNS = "/api/v1/admin/collection-runs/"


@contextmanager
def baseline(root, receipt):
    archive = subprocess.check_output(
        ["git", "archive", "--format=tar", BASELINE, "--", "backend"], cwd=ROOT, timeout=20
    )
    with tempfile.TemporaryDirectory(prefix="s08-baseline-", dir=root) as directory:
        tree = Path(directory)
        with tarfile.open(fileobj=BytesIO(archive), mode="r:") as source:
            source.extractall(tree, filter="data")
        receipt["baseline"] = {
            "commit": BASELINE,
            "backend_archive_sha256": hashlib.sha256(archive).hexdigest(),
            "mounted_subtree": "backend",
        }
        yield tree / "backend"


def permission_rows():
    from django.contrib.auth.models import Permission

    return list(
        Permission.objects.order_by("pk").values_list(
            "pk", "content_type__app_label", "content_type__model", "codename", "name"
        )
    )


def job_columns():
    from collection.models import Job

    return [field.attname for field in Job._meta.concrete_fields]


def legacy_jobs(columns):
    from collection.models import Job

    rows = list(Job.objects.order_by("pk").values(*columns))
    return json.loads(json.dumps(rows, default=datetime.isoformat))


def legacy_http(client, role, job, run):
    expected = {"job": 200 if role == "manager" else 403, "run": 200 if role == "reviewer" else 403}
    result = {"profile": parse(client.get(ME), 200, f"{role}_profile")}
    require(result["profile"]["username"] == f"s08-upgrade-{role}", f"{role}_session_owner")
    responses = {
        "job": client.get(f"{JOBS}{job}/"),
        "run": client.get(f"{RUNS}{run}/"),
    }
    for kind, response in responses.items():
        value = parse(response, expected[kind], f"{role}_{kind}")
        if expected[kind] == 403:
            require(value == {"code": "permission_denied"}, f"{role}_{kind}_denial")
        result[kind] = {"status": response.status_code, "body": value}
    if role == "manager":
        require(result["job"]["body"]["job"] == job, "legacy_job_projection")
    if role == "reviewer":
        entries = result["run"]["body"]["runs"]
        require([entry["id"] for entry in entries] == [run], "legacy_run_projection")
    return result


def research_http(client, contract):
    csrf(client)
    page = parse(client.post(DETAILS, json=contract["page"]), 200, "legacy_detail_cursor")
    saved = parse(
        client.post(f"{SAVED}{contract['saved_id']}/run/{SCOPE}"), 200, "legacy_saved_run"
    )
    require(
        page["input_version"]
        == saved["result"]["input_version"]
        == contract["page"]["input_version"],
        "legacy_research_version",
    )
    return {"page": page, "saved": saved, "csv": csv_download(client, contract["ticket"])}


def national_http(client, params):
    response = client.get(NATIONAL, params=params)
    page = parse(response, 200, "legacy_national_page")
    require(
        {"private", "no-store"} <= set(response.headers["Cache-Control"].split(", ")),
        "legacy_national_cache_control",
    )
    page.pop("evaluated_at")
    for row in page["results"]:
        for series in row["series"] + row["levels"]:
            series.pop("evaluated_at")
            series.pop("age_seconds")
    return page


def catalog_pages(client, query):
    first = parse(client.get(CATALOG, params=query), 200, "legacy_catalog_first_page")
    cursor = first["page"]["next_cursor"]
    require(cursor, "legacy_catalog_cursor_missing")
    params = query | {"cursor": cursor}
    second = parse(client.get(CATALOG, params=params), 200, "legacy_catalog_continuation")
    require(
        len(first["results"]) == len(second["results"]) == 1
        and first["results"][0]["shape"] == second["results"][0]["shape"] == "L"
        and first["results"][0]["city"] < second["results"][0]["city"]
        and second["page"] == {"page_size": 1, "next_cursor": None},
        "legacy_catalog_incorrect_continuation",
    )
    return {"cursor": cursor, "rows": first["results"] + second["results"]}


def seed_national(client, curator):
    from observations.normalization import STANDARD_2012
    from observations.policy import REQUIRED
    from s05_support import START, candidate, city, grant, ingest, product

    item = product("s08-upgrade-national-L", "L")
    first_city = city(item, "001", name="Upgrade city one")
    second_city = city(item, "002", name="Upgrade city two")
    for operation, fields in {
        "process_local": REQUIRED | {"overview_summary"},
        "retain_normalized": REQUIRED | {"catalog"},
        "display": REQUIRED | {"catalog", "overview_summary"},
    }.items():
        grant(curator, item, operation, fields=fields)
    ingest(
        curator,
        item,
        [
            candidate(
                item,
                code=code,
                indicator="AQI",
                value=value,
                token=1,
                standard_series=STANDARD_2012,
                standard_basis="source_stated",
                standard_evidence="Authored F upgrade witness",
            )
            for code, value in (("001", "30"), ("002", "60"))
        ],
    )
    query = {
        "product": item.pk,
        "scope": "synthetic",
        "at": START.isoformat(),
        "standard_series": STANDARD_2012,
        "page_size": 1,
    }
    witnesses = {}
    for order, cities in {
        "city": (first_city.pk, second_city.pk),
        "aqi_desc": (second_city.pk, first_city.pk),
    }.items():
        query_for_order = query if order == "city" else query | {"order": order}
        initial = national_http(client, query_for_order)
        cursor = initial["page"]["next_cursor"]
        require(cursor, f"s07_national_{order}_cursor_missing")
        continuation = query_for_order | {"cursor": cursor}
        page = national_http(client, continuation)
        require(
            [row["city"] for row in initial["results"]] == [cities[0]]
            and [row["city"] for row in page["results"]] == [cities[1]]
            and page["page"] == {"page_size": 1, "next_cursor": None}
            and initial["snapshot_at"] == page["snapshot_at"],
            f"s07_national_{order}_incorrect_continuation",
        )
        witnesses[order] = {"query": continuation, "page": page}
    catalog_query = {key: query[key] for key in ("product", "scope", "at", "page_size")}
    catalog = catalog_pages(client, catalog_query)
    return {"national": witnesses, "catalog": catalog | {"query": catalog_query}}


def seed_research(client, curator):
    from accounts.research_exports import export_fields
    from s05_support import HOUR, START, authorize, candidate, city, ingest, product

    item = product("s08-upgrade-research-C", "C")
    target = city(item)
    authorize(curator, item, detail_fields=export_fields("detail"))
    ingest(curator, item, [candidate(item, at=START + i * HOUR, token=1) for i in range(2)])
    query = {
        "selection": {
            "product": item.pk,
            "source": item.source.namespace,
            "scope": "synthetic",
            "cities": [target.pk],
            "indicators": ["CO"],
            "start": START.isoformat(),
            "end": (START + 2 * HOUR).isoformat(),
        },
        "method": "statistics",
        "method_version": "s05.v1",
        "grain": "day",
    }
    csrf(client)
    summary = parse(client.post(STATISTICS, json=query), 200, "s07_statistics")
    pinned = query | {"input_version": summary["input_version"]}
    first = parse(client.post(DETAILS, json=pinned | {"page_size": 1}), 200, "s07_first_page")
    cursor = first["page"]["next_cursor"]
    require(cursor, "s07_detail_cursor_missing")
    saved = parse(client.post(SAVED, json=query | {"title": "Upgrade witness"}), 201, "s07_saved")
    ticket = parse(
        client.post(EXPORTS, json={"kind": "detail", "groups": [pinned], "fields": []}),
        201,
        "s07_export_ticket",
    )
    contract = {
        "page": pinned | {"page_size": 1, "cursor": cursor},
        "saved_id": saved["id"],
        "ticket": ticket,
    }
    witnessed = research_http(client, contract)
    require(
        witnessed["page"]["results"][0]["key"] != first["results"][0]["key"],
        "s07_detail_cursor_stuck",
    )
    return {"contract": contract, "http": witnessed}


def seed():
    import django
    from django.core.management import call_command

    django.setup()
    import config.settings
    from accounts.models import AccountState
    from collection.admission import LEDGER
    from collection.models import Job, Run, WorkWindow
    from django.contrib.auth import get_user_model
    from django.contrib.auth.models import Group, Permission
    from observations.policy import clock
    from s05_support import HOUR, START, city, grant, product

    require(
        Path(config.settings.__file__).resolve().is_relative_to(BASELINE_MOUNT), "wrong_s07_code"
    )
    call_command("migrate", interactive=False, verbosity=0)
    curator = get_user_model().objects.create_superuser("s08-upgrade-curator")
    item = product("s08-upgrade-C", "C")
    due = clock().replace(minute=0, second=0, microsecond=0) + HOUR
    target = city(item, end=due + timedelta(days=1))
    for operation in ("review", "retain_normalized"):
        grant(curator, item, operation, fields=LEDGER, start=START, end=due + HOUR)
    job = Job.objects.create(
        product=item,
        executor=curator,
        city=target,
        profile="f-exact-v1",
        enabled=True,
        next_due_at=due,
        cadence_seconds=7200,
        control_version=3,
    )
    Job.objects.create(
        product=item,
        executor=curator,
        city=target,
        profile="f-latest-v1",
        enabled=False,
        pause_reason="operator_pause",
        control_version=4,
    )
    window = WorkWindow.objects.create(
        product=item,
        scope_key=f"city:{job.city_id}",
        profile=job.profile,
        target_start=START,
        target_end=START + HOUR,
        closed_at=START + HOUR,
        result="complete",
    )
    common = dict(window=window, job=job, actor=curator, executor=curator)
    first = Run.objects.create(
        **common,
        reason="scheduled",
        scheduled_for=START,
        status="failed",
        error_code="transient_http",
        started_at=START,
        finished_at=START + HOUR,
    )
    retry = Run.objects.create(
        **common,
        reason="retry",
        predecessor=first,
        attempt=2,
        status="finished",
        result="complete",
        started_at=START + HOUR,
        finished_at=START + 2 * HOUR,
    )
    witnesses = {}
    for role, codenames in ROLES.items():
        password = secrets.token_urlsafe(24)
        user = get_user_model().objects.create_user(f"s08-upgrade-{role}", password=password)
        AccountState.objects.create(user=user)
        perms = Permission.objects.filter(
            content_type__app_label="observations", codename__in=codenames
        )
        require(perms.count() == len(codenames), f"{role}_missing_s07_permission")
        if role == "reviewer":
            group = Group.objects.create(name="s08-upgrade-reviewers")
            group.permissions.set(perms)
            user.groups.add(group)
        else:
            user.user_permissions.set(perms)
        with session() as client:
            csrf(client)
            parse(
                client.post(
                    "/api/v1/auth/login/", json={"username": user.username, "password": password}
                ),
                200,
                f"{role}_login",
            )
            cookie = client.cookies.get("sessionid")
            require(cookie, f"{role}_old_session_cookie")
            witnesses[role] = {
                "session": cookie,
                "http": legacy_http(client, role, job.pk, str(retry.pk)),
            }
            if role == "reader":
                witnesses[role]["national"] = seed_national(client, curator)
                witnesses[role]["research"] = seed_research(client, curator)
    columns = job_columns()
    return {
        "migrations": migration_state(),
        "columns": columns,
        "jobs": legacy_jobs(columns),
        "database": database_snapshot(),
        "permissions": permission_rows(),
        "witnesses": witnesses,
        "active_job": job.pk,
        "retry_run": str(retry.pk),
    }


def check_after(old, result):
    from collection.models import Job, Run, ScheduleTemplate, ScheduleTemplateRevision

    require(legacy_jobs(old["columns"]) == old["jobs"], "s07_job_columns_changed")
    new_fields = {
        "plan_state": "legacy",
        "effective_from": None,
        "effective_until": None,
        "predecessor_id": None,
        "pending_successor_id": None,
        "schedule_anchor_at": None,
        "schedule_template_id": None,
        "schedule_template_version": None,
        "handoff_checked": False,
    }
    require(set(job_columns()) - set(old["columns"]) == new_fields.keys(), "job_field_set_changed")
    jobs = list(Job.objects.order_by("pk").values(*new_fields))
    require(jobs == [new_fields] * len(old["jobs"]), "s07_job_schedule_default")
    require(
        not ScheduleTemplate.objects.exists() and not ScheduleTemplateRevision.objects.exists(),
        "migration_enrolled_legacy_jobs",
    )
    run = Run.objects.select_related("predecessor").get(pk=old["retry_run"])
    require(
        run.job_id == run.predecessor.job_id == old["active_job"]
        and run.window_id == run.predecessor.window_id
        and run.attempt == 2,
        "old_run_owner_changed",
    )
    result["legacy_jobs"] = {"unchanged": len(jobs), "plan_state": "legacy", "templates": 0}
    result["run_lineage"] = {"same_job": True, "same_window": True, "retry_attempt": run.attempt}
    actual_permissions = {tuple(row) for row in permission_rows()}
    previous_permissions = {tuple(row) for row in old["permissions"]}
    require(previous_permissions <= actual_permissions, "frozen_permissions_changed")
    require(
        {row[1:4] for row in actual_permissions - previous_permissions} == NEW_PERMISSIONS,
        "unexpected_permissions_post_migrate",
    )
    result["permission_additions"] = sorted(map(list, NEW_PERMISSIONS))
    result["old_sessions"] = {}
    for role, witness in old["witnesses"].items():
        with session() as client:
            client.cookies.set("sessionid", witness["session"])
            actual = legacy_http(client, role, old["active_job"], old["retry_run"])
            require(actual == witness["http"], f"{role}_s07_http_changed")
            capability = parse(client.get(f"{ME}capabilities/"), 200, f"{role}_capabilities")
            require(capability["actions"] == list(ROLES[role]), f"{role}_permissions_changed")
            schedule = parse(
                client.get(f"{JOBS}{old['active_job']}/schedule/"), 403, f"{role}_schedule"
            )
            require(schedule == {"code": "permission_denied"}, f"{role}_schedule_denial")
            result["old_sessions"][role] = {
                "profile": 200,
                "job": actual["job"]["status"],
                "raw_run": actual["run"]["status"],
                "schedule": 403,
                "actions": capability["actions"],
            }
            if role == "reader":
                old_catalog = witness["national"]["catalog"]
                query = old_catalog["query"]
                stale_page = parse(
                    client.get(CATALOG, params=query | {"cursor": old_catalog["cursor"]}),
                    409,
                    "legacy_catalog_changed_link",
                )
                require(stale_page == {"code": "page_changed"}, "legacy_catalog_page_conflict")
                restarted = catalog_pages(client, query)
                for before, after in zip(old_catalog["rows"], restarted["rows"], strict=True):
                    require(before["link"] != after["link"], "legacy_catalog_link_not_corrected")
                    require(before | {"link": after["link"]} == after, "legacy_catalog_row_changed")
                result["legacy_catalog"] = {
                    "s07_cursor_issued": True,
                    "continuation_http": 409,
                    "continuation_code": stale_page["code"],
                    "restart_http": 200,
                    "restarted_continuation_http": 200,
                    "only_link_changed": True,
                }
                result["legacy_national"] = {}
                for order, national in witness["national"]["national"].items():
                    query = national["query"]
                    page = national_http(client, query)
                    require(page == national["page"], f"legacy_national_{order}_page_changed")
                    mismatch = parse(
                        client.get(NATIONAL, params=query | {"home": 1}),
                        400,
                        f"legacy_national_{order}_home_cursor",
                    )
                    require(mismatch == {"code": "invalid_cursor"}, "home_cursor_namespace")
                    result["legacy_national"][order] = {
                        "s07_cursor_issued": True,
                        "continuation_http": 200,
                        "continuation_unchanged": True,
                        "home_cursor_http": 400,
                        "home_cursor_code": mismatch["code"],
                    }
                research = witness["research"]
                observed = research_http(client, research["contract"])
                for operation, expected in research["http"].items():
                    require(observed[operation] == expected, f"legacy_research_{operation}_changed")
                result["private_research"] = {
                    "detail_cursor_http": 200,
                    "saved_query_run_http": 200,
                    "export_download": observed["csv"],
                }


def upgrade(env, result):
    import django
    from django.core.management import call_command

    old = subprocess.run(
        [sys.executable, "-B", str(SCRIPT), "--old"],
        cwd=BASELINE_MOUNT,
        env=env
        | {
            "PYTHONPATH": os.pathsep.join(
                map(str, (BASELINE_MOUNT, ROOT / "scripts", ROOT / "tests"))
            )
        },
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    if old.returncode:
        result["old_seed"] = json.loads(old.stdout) if old.stdout else {"exit": old.returncode}
    require(old.returncode == 0, "s07_seed_process_failed")
    original = json.loads(old.stdout)
    result["old_writers_exited"] = True
    result["s07"] = original["migrations"]
    require(not result["s07"]["pending"], "s07_pending_migrations")
    check_database(env, expected_marker=env["S06_RUN"], empty=False)
    django.setup()
    import config.settings

    require(
        Path(config.settings.__file__).resolve().is_relative_to(ROOT / "backend"), "wrong_s08_code"
    )
    before = result["s08_before"] = migration_state()
    require(
        before["pending"] and all(not step[-1] for step in before["pending"]),
        "expected_forward_steps",
    )
    call_command("migrate", interactive=False, verbosity=0)
    first = result["s08_first"] = migration_state()
    require(not first["pending"], "s08_pending_migrations")
    snapshot = database_snapshot()
    call_command("migrate", interactive=False, verbosity=0)
    result["s08_second"] = migration_state()
    require(
        result["s08_second"] == first and database_snapshot() == snapshot, "second_migration_drift"
    )
    sections = {"auth_permission", "collection_job", "django_content_type", "django_migrations"}
    preserved = original["database"].keys() - sections
    result["preserved_tables"] = sorted(preserved)
    require(
        all(original["database"][name] == snapshot.get(name) for name in preserved),
        "s07_table_contents_changed",
    )
    result["legacy_ledger_rows"] = {
        name: snapshot[name][0] for name in ("collection_run", "collection_workwindow")
    }
    check_after(original, result)


def inside(old):
    env = os.environ.copy()
    root = check_environment(env)
    network = check_network()
    check_database(env, expected_marker=env["S06_RUN"], empty=True)
    require(os.statvfs(BASELINE_MOUNT).f_flag & os.ST_RDONLY, "s07_baseline_not_read_only")
    if old:
        require(Path.cwd() == BASELINE_MOUNT, "s07_seed_cwd")
        try:
            state = seed()
        except Exception as exc:
            print(json.dumps(diagnostic(exc)))
            return 1
        print(
            json.dumps(state)
        )  # Private pipe only. Old session cookies are never written to receipts.
        return 0
    result = {
        "scope": "S07-to-S08-forward",
        "commit": BASELINE,
        "origin": "F",
        "network": network,
        "status": "FAIL",
    }
    try:
        upgrade(env, result)
        result["status"] = "PASS"
    except Exception as exc:
        result["diagnostic"] = diagnostic(exc)
    finally:
        (root / "upgrade.json").write_text(json.dumps(result, indent=2) + "\n")
    return int(result["status"] != "PASS")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    child = parser.add_mutually_exclusive_group()
    child.add_argument("--inside", action="store_true", help=argparse.SUPPRESS)
    child.add_argument("--old", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.inside or args.old:
        return inside(args.old)
    sandbox = verify_s06.sandbox_command

    def command(env, snapshot, action, *selectors, old=None):
        argv = sandbox(env, snapshot, action, *selectors, old=old)
        if action != "upgrade":
            return argv
        require(argv[-2:] == [str(ROOT / "scripts/s06_sandbox.py"), "upgrade"], "sandbox_entry")
        return [*argv[:-2], str(SCRIPT), "--inside"]

    verify_s06.baseline_code = baseline
    verify_s06.sandbox_command = command
    sys.argv[1:] = ["upgrade"]
    return verify_s06.main()


if __name__ == "__main__":
    sys.exit(main())
