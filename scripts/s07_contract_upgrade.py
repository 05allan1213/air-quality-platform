#!/usr/bin/env python3
"""C02/V12: real S05 Session, cursor, saved query and ticket across forward upgrade."""

import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from threading import Thread
from traceback import extract_tb
from wsgiref.simple_server import make_server

import httpx
import verify_s06
from s01_runtime import ROOT
from s06_guard import IsolationError, check_database, check_environment, owned_root, require
from s06_sandbox import BASELINE_MOUNT, check_network

PREFIX = "/api/v1/"
STATISTICS = PREFIX + "analysis/statistics/"
DETAILS = PREFIX + "analysis/details/"
EXACT = PREFIX + "observations/current/"
EXPORTS = PREFIX + "me/exports/"
SAVED = PREFIX + "me/saved-queries/"
SCOPE = "?scope=synthetic"
MARK = re.compile(r"[0-9a-f]{64}")


@contextmanager
def session(*, witness=None):
    from django.core.wsgi import get_wsgi_application
    from s05_http import QuietRequestHandler

    with make_server(
        "127.0.0.1", 0, get_wsgi_application(), handler_class=QuietRequestHandler
    ) as server:
        thread = Thread(target=server.serve_forever)
        thread.start()
        try:
            with httpx.Client(
                base_url=f"http://127.0.0.1:{server.server_port}",
                trust_env=False,
                timeout=8,
                event_hooks={"response": [witness.inspect]} if witness else None,
            ) as client:
                yield client
        finally:
            server.shutdown()
            thread.join()


def parse(response, status, label):
    require(response.status_code == status, f"{label}_unexpected_status_{response.status_code}")
    require(response.headers.get("Content-Type") == "application/json", f"{label}_not_json")
    return response.json()


def csrf(client):
    result = parse(client.get(PREFIX + "auth/csrf/"), 200, "csrf")
    client.headers["X-CSRFToken"] = result["csrf_token"]


def csv_download(client, ticket):
    response = client.get(f"{EXPORTS}{ticket['id']}/download/{SCOPE}")
    require(response.status_code == 200, f"old_ticket_download_status_{response.status_code}")
    require(response.headers.get("Content-Type") == "text/csv; charset=utf-8", "csv_media")
    require("attachment;" in response.headers.get("Content-Disposition", ""), "csv_attachment")
    require("no-store" in response.headers.get("Cache-Control", ""), "csv_no_store")
    digest = hashlib.sha256(response.content).hexdigest()
    return {"bytes": len(response.content), "sha256": digest, "status": response.status_code}


def rejected_v1_download(client, ticket):
    response = client.get(f"{EXPORTS}{ticket['id']}/download/{SCOPE}")
    require(
        parse(response, 409, "legacy_csv_format") == {"code": "method_changed"}, "legacy_csv_code"
    )
    require("Content-Disposition" not in response.headers, "legacy_csv_attachment")
    return {"status": 409, "code": "method_changed"}


def diagnostic(exc):
    """Keep exception types and code locations; never retain credential-bearing output."""
    frames = []
    sqlstates = []
    types = []
    for _ in range(5):
        if exc is None:
            break
        types.append(type(exc).__name__)
        state = getattr(exc, "sqlstate", "")
        if isinstance(state, str) and re.fullmatch(r"[A-Z0-9]{5}", state):
            sqlstates.append(state)
        for frame in extract_tb(exc.__traceback__):
            path = Path(frame.filename)
            for root in (BASELINE_MOUNT, ROOT):
                if path.is_relative_to(root) and ".venv" not in path.parts:
                    frames.append(f"{path.relative_to(root)}:{frame.lineno}")
                    break
        if isinstance(exc, IsolationError) and re.fullmatch(r"[a-z0-9_]+", str(exc)):
            types.append(str(exc))
        exc = exc.__cause__ or exc.__context__
    return {"types": types, "frames": frames[-10:], "sqlstates": sqlstates}


def prepare_old(password):
    import django
    from django.core.management import call_command

    django.setup()
    import config.settings

    require(
        Path(config.settings.__file__).resolve().is_relative_to(BASELINE_MOUNT),
        "wrong_s05_settings",
    )
    call_command("migrate", interactive=False, verbosity=0)
    from accounts.models import ExportTicket
    from accounts.research_exports import export_fields
    from django.contrib.auth import get_user_model
    from s05_support import HOUR, START, authorize, candidate, city, ingest, product

    operator = get_user_model().objects.create_superuser("compat-curator")
    reader = get_user_model().objects.create_user("compat-reader", password=password)
    old = {"products": []}
    with session() as client:
        csrf(client)
        parse(
            client.post(
                PREFIX + "auth/login/", json={"username": reader.username, "password": password}
            ),
            200,
            "s05_login",
        )
        csrf(client)
        old["session"] = client.cookies.get("sessionid")
        require(old["session"], "old_session_cookie")
        for role in ("stable", "changing"):
            item = product(f"s07-compat-{role}", "C")
            target = city(item)
            authorize(operator, item, detail_fields=export_fields("detail"))
            ingest(
                operator,
                item,
                [
                    candidate(item, at=START + i * HOUR, token=1, value=str(i + 10))
                    for i in range(3)
                ],
            )
            query = {
                "selection": {
                    "product": item.pk,
                    "source": item.source.namespace,
                    "scope": "synthetic",
                    "cities": [target.pk],
                    "indicators": ["CO"],
                    "start": START.isoformat(),
                    "end": (START + 3 * HOUR).isoformat(),
                },
                "method": "statistics",
                "method_version": "s05.v1",
                "grain": "day",
            }
            exact = {
                "product": item.pk,
                "scope": "synthetic",
                "city": target.pk,
                "indicator": "CO",
                "report_kind": "hourly",
                "standard_series": "not_applicable",
                "observed_at": START.isoformat(),
                "window_definition": "hourly_label_unspecified",
            }
            exact_rows = parse(client.get(EXACT, params=exact), 200, "s05_exact")["results"]
            require(len(exact_rows) == 1, "s05_exact_row")
            require(isinstance(exact_rows[0].pop("input_version"), dict), "s05_exact_old_version")
            summary = parse(client.post(STATISTICS, json=query), 200, "s05_statistics")
            research = query | {"input_version": summary["input_version"]}
            first = parse(client.post(DETAILS, json=research | {"page_size": 1}), 200, "s05_page")
            require(first["page"]["next_cursor"], "s05_cursor")
            ticket = parse(
                client.post(EXPORTS, json={"kind": "detail", "groups": [research], "fields": []}),
                201,
                "s05_ticket",
            )
            persisted = ExportTicket.objects.get(pk=ticket["id"])
            require(persisted.format_version == "s05.csv.v1", "s05_csv_version")
            file_receipt = csv_download(client, ticket)
            require(file_receipt["sha256"] == persisted.data_sha256, "csv_digest")
            saved = parse(client.post(SAVED, json=query | {"title": role}), 201, "s05_saved")
            old["products"].append(
                {
                    "role": role,
                    "query": research,
                    "exact": exact,
                    "row": exact_rows[0],
                    "cursor": first["page"]["next_cursor"],
                    "first_key": first["results"][0]["key"],
                    "ticket": ticket,
                    "download": file_receipt,
                    "saved_id": saved["id"],
                    "operator": operator.pk,
                }
            )
    return old


def accepted_upgrade(old):
    import django
    from django.core.management import call_command

    django.setup()
    call_command("migrate", interactive=False, verbosity=0)
    from django.contrib.auth import get_user_model
    from observations.models import Product
    from s05_support import candidate, ingest

    evidence = []
    with session() as client:
        client.cookies.set("sessionid", old["session"])
        user = parse(client.get(PREFIX + "me/"), 200, "s07_existing_session")
        require(user["username"] == "compat-reader", "old_session_identity")
        csrf(client)
        for data in old["products"]:
            query, exact = data["query"], data["exact"]
            rows = parse(client.get(EXACT, params=exact), 200, "s07_exact")["results"]
            require(len(rows) == 1, "s07_exact_row")
            row = rows[0]
            mark = row.pop("input_version")
            require(isinstance(mark, str) and MARK.fullmatch(mark), "s07_exact_mark")
            require(row == data["row"], "s05_exact_projection_changed")
            page = query | {"page_size": 1, "cursor": data["cursor"]}
            second = parse(client.post(DETAILS, json=page), 200, "s05_cursor_continued")
            require(
                second["input_version"] == query["input_version"]
                and second["results"][0]["key"] != data["first_key"],
                "s05_cursor_result",
            )
            saved = parse(
                client.post(f"{SAVED}{data['saved_id']}/run/{SCOPE}"), 200, "s05_saved_run"
            )
            require(saved["state"] in {"current", "baseline_unavailable"}, "s05_saved_state")
            require(saved["result"]["input_version"] == query["input_version"], "s05_saved_version")
            file_receipt = rejected_v1_download(client, data["ticket"])
            mixing = parse(
                client.post(DETAILS, json=query | {"input_version": mark}), 409, "c02_mixed"
            )
            require(mixing == {"code": "input_changed"}, "c02_mixed_diagnostic")
            if data["role"] == "changing":
                p = Product.objects.get(pk=query["selection"]["product"])
                operator = get_user_model().objects.get(pk=data["operator"])
                ingest(operator, p, [candidate(p, token=2, value="99")])
                stale = parse(client.post(DETAILS, json=query), 409, "s05_changed_version")
                require(stale == {"code": "input_changed"}, "s05_version_diagnostic")
                response = client.get(f"{EXPORTS}{data['ticket']['id']}/download/{SCOPE}")
                denial = parse(response, 409, "s05_changed_ticket")
                require(denial == {"code": "method_changed"}, "s05_ticket_diagnostic")
                require("Content-Disposition" not in response.headers, "s05_denial_csv_header")
            evidence.append(
                {
                    "role": data["role"],
                    "exact_projection_unchanged": True,
                    "new_exact_version": "opaque_sha256",
                    "old_cursor_continued": True,
                    "old_saved_query_state": saved["state"],
                    "csv": file_receipt,
                    "exact_mark_in_research_status": 409,
                    "changed_ticket_reject_status": 409 if data["role"] == "changing" else None,
                }
            )
        rejected_v1_download(client, old["products"][0]["ticket"])
    return evidence


def sandbox():
    env = os.environ.copy()
    root = check_environment(env)
    check_network()
    check_database(env, expected_marker=env["S06_RUN"], empty=True)
    old_dir = Path(env["S06_BASELINE_CODE"])
    require(old_dir == BASELINE_MOUNT and old_dir.is_dir(), "baseline_mount")
    require(
        any(
            line.split()[4] == str(old_dir) and "ro" in line.split()[5].split(",")
            for line in Path("/proc/self/mountinfo").read_text().splitlines()
        ),
        "baseline_not_read_only",
    )
    result = {"baseline": verify_s06.BASELINE, "origin": "F", "status": "FAIL"}
    try:
        child = subprocess.run(
            [sys.executable, "-B", __file__, "--old"],
            cwd=old_dir,
            env=env
            | {
                "DJANGO_SETTINGS_MODULE": "config.settings",
                "PYTHONPATH": os.pathsep.join(
                    map(str, (old_dir, ROOT / "scripts", ROOT / "tests"))
                ),
                "S07_COMPAT_PASSWORD": secrets.token_urlsafe(30),
            },
            check=False,
            capture_output=True,
            text=True,
            timeout=65,
        )
        if child.returncode:
            old_failure = root / "old_failure.json"
            if old_failure.is_file():
                result["old_failure"] = json.loads(old_failure.read_text())
            require(False, "s05_compat_seed_failed")
        result["cases"] = accepted_upgrade(json.loads(child.stdout))
        result["status"] = "PASS"
    except Exception as exc:
        result["error"] = diagnostic(exc)
        raise SystemExit(1) from None
    finally:
        (root / "upgrade.json").write_text(json.dumps(result, indent=2) + "\n")


def run():
    run_id = f"s06-{secrets.token_hex(6)}"
    root = verify_s06.make_root(run_id)
    env = verify_s06.environment(run_id, root, secrets.token_hex(32))
    receipt = {"run": run_id, "rejected": [], "origin": "F/I", "guard": "NOT RUN", "status": "FAIL"}
    print(root.relative_to(ROOT), flush=True)
    try:
        with (
            verify_s06.freeze_source(root) as (snapshot, digest),
            verify_s06.baseline_code(root, receipt) as old,
            verify_s06.cluster(env, receipt) as (container, image),
        ):
            receipt["source_before"] = digest
            verify_s06.probe(env, receipt, snapshot)
            verify_s06.isolation(verify_s06.inspect(container), run_id, container, image)
            receipt["guard"] = "PASS"
            command = verify_s06.sandbox_command(env, snapshot, "upgrade", old=old)
            require(
                command[-2:] == [str(ROOT / "scripts/s06_sandbox.py"), "upgrade"], "sandbox_command"
            )
            child = subprocess.run(
                [*command[:-2], str(ROOT / "scripts/s07_contract_upgrade.py"), "--sandbox"],
                cwd=ROOT,
                env=env,
                check=False,
                timeout=120,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            receipt["exit"] = child.returncode
            require(child.returncode == 0, "contract_upgrade_failed")
    except (IsolationError, OSError, subprocess.SubprocessError) as exc:
        receipt["error"] = str(exc) if isinstance(exc, IsolationError) else type(exc).__name__
    finally:
        require(
            not verify_s06.docker("ps", "-aq", "--filter", f"label={verify_s06.LABEL}={run_id}"),
            "container_remaining",
        )
        owned_root(run_id)
        for name in ("pg", "stage", "broker"):
            path = root / name
            require(path.resolve(strict=True) == path and path.is_dir(), "cleanup_path")
            shutil.rmtree(path)
        receipt["cleanup"] = verify_s06.evidence_only(root)
        if receipt["guard"] == "PASS" and receipt.get("exit") == 0 and receipt["cleanup"]:
            receipt["status"] = "PASS"
        (root / "V01" / "result.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(receipt["status"], flush=True)
    return int(receipt["status"] != "PASS")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--sandbox", action="store_true", help=argparse.SUPPRESS)
    action.add_argument("--old", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.sandbox:
        return sandbox()
    if args.old:
        env = os.environ.copy() | {"DJANGO_SETTINGS_MODULE": "s06_settings"}
        root = check_environment(env)
        check_network()
        check_database(env, expected_marker=env["S06_RUN"], empty=True)
        require(Path.cwd() == BASELINE_MOUNT, "s05_old_cwd")
        require(os.statvfs(BASELINE_MOUNT).f_flag & os.ST_RDONLY, "s05_baseline_mount")
        try:
            old = prepare_old(os.environ["S07_COMPAT_PASSWORD"])
        except Exception as exc:
            (root / "old_failure.json").write_text(json.dumps(diagnostic(exc)) + "\n")
            return 1
        print(json.dumps(old))
        return 0
    return run()


if __name__ == "__main__":
    sys.exit(main())
