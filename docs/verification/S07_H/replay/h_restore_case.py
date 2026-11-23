"""Exercise the guarded restore command and revoke scoped uses of real UCI H."""

import errno
import json
import os
import re
import stat
import sys
from collections import Counter
from http.client import HTTPConnection
from io import StringIO
from pathlib import Path
from secrets import token_urlsafe
from unittest.mock import patch
from uuid import UUID, uuid4

from h_case import OPERATOR, STARTS, STATIONS, grant, grants, guard, load_inner, register, stage
from h_restore_custody import FIELDS, ISSUER, PERIOD, WINDOWS, request
from s04_checks import Probe, call, check, django_check, sql
from s05_v16_oracles import csv_oracle, sql_rows
from s06_guard import check_database, check_environment
from s06_restore_target import ALIAS, bootstrap_target, install_target
from s06_runtime import Runtime
from s06_sandbox import check_network

API = "/api/v1/admin/"
SCOPES = ((STATIONS[0], *WINDOWS[0]), (STATIONS[0], *WINDOWS[1]), (STATIONS[1], *WINDOWS[0]))
TARGET_COUNTS = """SELECT (SELECT count(*) FROM observations_source),
    (SELECT count(*) FROM observations_product), (SELECT count(*) FROM observations_stationobservation),
    (SELECT count(*) FROM observations_stationrevision), (SELECT count(*) FROM auth_user),
    (SELECT count(*) FROM django_session)"""


def volatile_stage(root):
    from django.conf import settings

    path = Path(settings.S02_STAGE_DIR)
    check(path == root / "stage" and path.is_mount(), "owned tmpfs stage")
    info = path.stat()
    mounts = [line.split() for line in Path("/proc/self/mountinfo").read_text().splitlines()]
    (mount,) = [row for row in mounts if row[4] == str(path)]
    storage = os.statvfs(path)
    check(
        info.st_uid == os.geteuid()
        and stat.S_IMODE(info.st_mode) == 0o700
        and {"nosuid", "nodev", "rw"} <= set(mount[5].split(","))
        and mount[mount.index("-") + 1] == "tmpfs"
        and storage.f_blocks * storage.f_frsize == 32 * 1024 * 1024,
        "owned bounded stage medium",
    )
    return {
        "medium": "tmpfs",
        "capacity_bytes": storage.f_blocks * storage.f_frsize,
        "mode": "0700",
    }


def exact_rights(product, operator, until):
    from datetime import date

    from h_case import CITY_CODE

    def basis(op, code, indicator, fields, period):
        start, end = map(date.fromisoformat, period)
        return grant(
            operator,
            product,
            op,
            "platform",
            fields,
            start,
            end,
            code=code,
            indicator=indicator,
            until=until,
        )

    for op in ("backup", "restore"):
        for code in (*STATIONS, CITY_CODE):
            basis(op, code, "*", {"catalog"}, PERIOD)
    uses = {}
    for index, (code, start, end) in enumerate(SCOPES):
        for op in ("backup", "restore", "retain_normalized"):
            uses[index, op] = basis(op, code, "CO", FIELDS, (start, end))
    return uses, basis


def target_counts(alias, *, independent):
    import psycopg
    from django.db import connections

    connection = connections[alias]
    if independent:
        options = connection.get_connection_params()
        options["options"] = "-c default_transaction_read_only=on -c statement_timeout=3000"
        with psycopg.connect(**options) as conn, conn.cursor() as cursor:
            cursor.execute(TARGET_COUNTS)
            return list(cursor.fetchone())
    with connection.cursor() as cursor:
        cursor.execute(TARGET_COUNTS)
        return list(cursor.fetchone())


def ensure_empty():
    counts = target_counts(ALIAS, independent=True)
    check(
        counts == [0] * 6,
        "target rollback leaves no source product keys revisions users or sessions",
    )
    return counts


def switch_target(slot):
    from django.db import connections

    if ALIAS in connections.databases:
        connections[ALIAS].close()
        del connections[ALIAS]
        del connections.databases[ALIAS]
    return install_target(slot)


def capture_responses():
    """Record only status and byte count of the real private loopback responses."""
    observed = []

    class ObservedConnection(HTTPConnection):
        def getresponse(self):
            response = super().getresponse()
            receive = response.read

            def measure(*args, **kwargs):
                body = receive(*args, **kwargs)
                observed.append({"http": response.status, "bytes": len(body)})
                return body

            response.read = measure
            return response

    return observed, patch("s06_restore_http.HTTPConnection", ObservedConnection)


class Rehearsal:
    def __init__(self, product, operator, custody, backup_root, socket_path):
        self.product = product
        self.operator = operator
        self.custody = custody
        self.backup = backup_root
        self.socket = socket_path

    def sign(self, kind, operation):
        material = call(self.custody, f"{API}backup-authority/{kind}/{operation}/", uncached=True)
        request(self.socket, "sign", material)

    def build(self, index):
        code, start, end = SCOPES[index]
        path = f"{API}backup-products/{self.product.pk}/segments/"
        scope = {
            "geography": "beijing",
            "object_code": code,
            "indicator": "CO",
            "start": start,
            "end": end,
        }
        prepared = call(
            self.operator, path, method="POST", body={"scope": scope}, status=201, uncached=True
        )
        check(prepared["state"] == "preparing", "prepared state")
        segment = UUID(prepared["operation_id"])
        self.sign("publish", segment)
        body = call(
            self.operator,
            f"{API}backup-segments/{segment}/publish/",
            method="POST",
            body={},
            uncached=True,
        )
        check(body["state"] == "available", "published segment")
        return segment

    def intent(self, segment, target, *, status=201, code=None):
        body = call(
            self.operator,
            f"{API}backup-segments/{segment}/restore-intents/",
            method="POST",
            body={"target": str(target)},
            status=status,
            code=code,
            uncached=True,
        )
        return UUID(body["operation_id"]) if status == 201 else status

    def withdraw(self, basis):
        path = f"{API}grants/{basis.pk}/actions/"
        registered = call(self.custody, path, uncached=True)
        result = call(
            self.custody,
            path,
            method="POST",
            body={
                "action": "withdraw",
                "request_id": str(uuid4()),
                "reason_code": "rights_review",
                "expected_version": registered["version"],
            },
            uncached=True,
        )
        check(
            result["state"] == "withdrawn" and result["version"] == registered["version"] + 1,
            "scoped withdrawal committed",
        )
        return result["version"]

    def dispose(self):
        from django.core.management import call_command

        path = f"{API}disposition-products/{self.product.pk}/"
        observed = call(self.custody, path, uncached=True)
        check(bool(observed["jobs"]), "real disposition jobs")
        prepared = call(
            self.custody,
            path,
            method="POST",
            body={
                "action": "prepare_disposal",
                "request_id": str(uuid4()),
                "reason_code": "due_reconciliation",
                "expected_version": observed["version"],
                "basis": observed["basis"],
            },
            uncached=True,
        )
        call_command("s06_dispose", operation=UUID(prepared["operation_id"]), stdout=StringIO())
        completed = call(self.custody, path, uncached=True)
        check(not completed["jobs"], "all due dispositions cleared")
        return len(observed["jobs"]), completed["version"]

    def positive(self, segment, *, cli):
        from accounts.models import GovernanceAction
        from django.core.management import call_command
        from observations.backup_models import RestoreIntent
        from observations.backup_restore import run
        from observations.policy import Denied
        from s06_restore_http import release

        target, guard_target = switch_target("" if cli else "2")
        before = ensure_empty()
        operation = self.intent(segment, target)
        self.sign("restore", operation)
        observed, measure = capture_responses()
        with measure:
            if cli:
                output = StringIO()
                call_command("s06_restore", str(operation), stdout=output)
                result = json.loads(output.getvalue())
            else:
                result = run(operation, target, ALIAS, guard_target, release)
        check(
            result == {"keys": 48, "revisions": 48, "verified": True, "http": [403, 204, 200, 403]},
            "positive H restore projection",
        )
        check(
            [x["http"] for x in observed] == result["http"]
            and observed[2]["bytes"] > 0
            and all(x["bytes"] == 0 for x in (observed[0], observed[1], observed[3])),
            "one private result body",
        )
        check(
            RestoreIntent.objects.get(pk=operation).consumed_at is not None,
            "intent durably consumed",
        )
        check(
            GovernanceAction.objects.filter(
                request_id=operation, action="restore_read", outcome="applied"
            ).count()
            == 1,
            "one applied audit",
        )
        try:
            run(operation, target, ALIAS, guard_target, release)
        except Denied as exc:
            check(exc.code == "precondition_conflict", "intent replay rejection")
        else:
            raise AssertionError("intent replay accepted")
        return result | {
            "probe": observed,
            "before": before,
            "after": ensure_empty(),
            "replay_rejected": True,
        }

    def deny_staged(self, segment, basis, *, make_unavailable):
        from accounts.models import GovernanceAction
        from observations.backup_models import BackupSegment, RestoreIntent
        from observations.backup_restore import run
        from observations.policy import Denied
        from s06_restore_http import release

        target, guard_target = switch_target("2")
        operation = self.intent(segment, target)
        self.sign("restore", operation)
        review = {}

        def withdraw_after_load(alias, read):
            review["loaded_in_transaction"] = target_counts(alias, independent=False)
            check(
                review["loaded_in_transaction"] == [1, 1, 48, 48, 1, 0],
                "staged real H keys and inactive subject",
            )
            review["parallel_target_connection"] = ensure_empty()
            review["withdrawn_rights_version"] = self.withdraw(basis)
            return release(alias, read)

        observed, measure = capture_responses()
        with measure:
            try:
                run(operation, target, ALIAS, guard_target, withdraw_after_load)
            except Denied as exc:
                check(exc.code == "restore_read_denied", "withdrawn restore rejected before read")
            else:
                raise AssertionError("withdrawn restore read succeeded")
        check(
            observed == [{"http": status, "bytes": 0} for status in (403, 204, 403, 403)],
            "withdrawal delivers zero bytes",
        )
        check(
            RestoreIntent.objects.get(pk=operation).consumed_at is not None,
            "claim before withdrawal was durable",
        )
        check(
            not GovernanceAction.objects.filter(
                request_id=operation, action="restore_read", outcome="applied"
            ).exists(),
            "no applied restore after withdrawal",
        )
        state = "blocked" if make_unavailable else "available"
        check(
            BackupSegment.objects.get(pk=segment).state == state
            and (self.backup / f"{segment}.segment").is_file(),
            "retention pending original volume verification",
        )
        rejection = "precondition_conflict" if make_unavailable else "use_denied"
        status = self.intent(
            segment, target, status=(409 if make_unavailable else 403), code=rejection
        )
        return review | {
            "probe": observed,
            "after": ensure_empty(),
            "segment_state": state,
            "intent_http": status,
            "intent_code": rejection,
            "durable_claim": True,
        }


def do_work(root, receipt):
    from django.conf import settings
    from django.contrib.auth.models import Permission, User
    from observations.backup_models import BackupSegment, BackupSubject
    from observations.models import SiteEpoch

    password = token_urlsafe(32)
    receipt["phase"] = "register"
    product, importer, until = register(password, restore=True)
    custodian_password = token_urlsafe(32)
    custodian = User.objects.create_user(ISSUER, password=custodian_password)
    custodian.user_permissions.add(
        Permission.objects.get(content_type__app_label="observations", codename="manage_grants")
    )
    BackupSubject.objects.create(user=importer)
    uses, make_basis = exact_rights(product, importer, until)
    rows = grants(product)
    receipt["initial_grants"] = {
        "operations": dict(sorted(Counter(r["operation"] for r in rows).items())),
        "audiences": sorted({r["audience"] for r in rows}),
        "first_valid_from": min(r["valid_from"] for r in rows).isoformat(),
        "last_valid_from": max(r["valid_from"] for r in rows).isoformat(),
        "valid_until": until.isoformat(),
        "terms_versions": sorted({r["terms_version"] for r in rows}),
    }
    backup = Path(settings.S06_BACKUP_DIR)
    backup.mkdir(mode=0o700)
    publication = Path(settings.S06_AUTHORITY_DIR)
    socket_path = root / "custody.sock"
    request(socket_path, "configure")
    check(not Path(os.environ["S07_H_KEY_PROBE"]).exists(), "custodian key not mounted")
    try:
        with (publication / "trust.json").open("r+b"):
            raise AssertionError("authority publication writable in app")
    except OSError as exc:
        check(exc.errno == errno.EROFS, "independent authority read-only mount")
    receipt["custody_mount"] = {
        "readonly": True,
        "key_visible": False,
        "separate_session_role": True,
    }

    receipt["phase"] = "target_prepare"
    target_a = bootstrap_target()
    switch_target("2")
    target_b = bootstrap_target("2")
    check(target_a != target_b, "separate restore targets")
    runtime = Runtime(root)
    try:
        with runtime.managed():
            receipt["phase"] = "import"
            runtime.redis()
            receipt["worker_queues"] = len(runtime.worker())
            raw = load_inner(sys.stdin.buffer)
            receipt["import"] = stage(raw, importer)
            values = csv_oracle(raw)
            del raw
            epochs = dict(
                SiteEpoch.objects.filter(station__source=product.source).values_list("name", "id")
            )
            imported = sql_rows(product.pk, epochs, values)
            receipt["input"] = {
                "class": "H",
                "keys": len(imported),
                "stations": len(epochs),
                "windows": len(STARTS),
                "csv_pg_reconciled": True,
            }
            web = runtime.web()
            operator = Probe(web.base).login(OPERATOR, password)
            issuer = Probe(web.base).login(ISSUER, custodian_password)
            test = Rehearsal(product, operator, issuer, backup, socket_path)
            receipt["phase"] = "publish"
            first, second, sibling = (test.build(index) for index in range(len(SCOPES)))
            receipt["published_segments"] = 3
            receipt["phase"] = "restore"
            receipt["restore_success"] = test.positive(first, cli=True)
            receipt["phase"] = "withdraw_restore"
            receipt["withdraw_restore"] = test.deny_staged(
                first, uses[0, "restore"], make_unavailable=False
            )
            receipt["phase"] = "withdraw_backup"
            receipt["withdraw_backup"] = test.deny_staged(
                second, uses[1, "backup"], make_unavailable=True
            )
            code, start, end = SCOPES[1]
            make_basis("backup", code, "CO", FIELDS, (start, end))
            target, _ = switch_target("2")
            receipt["regrant_old_segment"] = test.intent(
                second, target, status=409, code="precondition_conflict"
            )
            receipt["phase"] = "dispose"
            jobs, version = test.dispose()
            check(
                BackupSegment.objects.filter(
                    pk=second, state="removed", manifest__isnull=True
                ).exists(),
                "removed backup catalog projection",
            )
            check(
                all(not (backup / f"{second}.{ext}").exists() for ext in ("partial", "segment")),
                "revoked segment files cleared from original volume",
            )
            check(
                all((backup / f"{good}.segment").is_file() for good in (first, sibling)),
                "valid backup files retained",
            )
            after = sql_rows(product.pk, epochs, values)
            check(
                after == imported,
                "source observations match the lawful pre-disposal keys and revisions",
            )
            receipt["disposition"] = {
                "jobs_completed": jobs,
                "rights_version": version,
                "affected_files": 0,
                "lawful_segment_files": 2,
                "legal_source_keys": len(after),
            }
            receipt["phase"] = "legal_sibling_restore"
            receipt["legal_sibling_restore"] = test.positive(sibling, cli=False)
            check(sql("SELECT count(*) FROM collection_run") == [(0,)], "no online acquisitions")
            receipt["online_collection_runs"] = 0
    finally:
        receipt["child_processes_remaining"] = len(runtime.children)
        receipt["lifecycle"] = runtime.lifecycle


def main():
    import django

    receipt = {"result": "FAIL", "phase": "guards"}
    try:
        root = check_environment(os.environ)
        receipt["network"] = check_network()
        check_database(os.environ, expected_marker=os.environ["S06_RUN"], empty=True)
        django.setup()
        guard(os.environ["S06_RUN"])
        receipt["stage"] = volatile_stage(root)
        receipt["phase"] = "authority_migrations"
        django_check("migrate", interactive=False, verbosity=0)
        do_work(root, receipt)
        receipt.update(result="PASS", phase="complete")
    except Exception as exc:
        import traceback

        receipt["error_type"] = type(exc).__name__
        label = str(exc)
        if isinstance(exc, (AssertionError, ValueError)) and re.fullmatch(
            r"[\w ,/=?().'\-]{1,180}", label
        ):
            label = re.sub(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", "<id>", label)
            receipt["condition"] = label
        if (state := getattr(exc.__cause__, "sqlstate", None)) and re.fullmatch(
            r"[A-Z0-9]{5}", state
        ):
            receipt["sqlstate"] = state
        receipt["frames"] = [
            f"{Path(x.filename).name}:{x.lineno}"
            for x in traceback.extract_tb(exc.__traceback__)[-6:]
        ]
    print(json.dumps(receipt, ensure_ascii=True), flush=True)
    return int(receipt["result"] != "PASS")


if __name__ == "__main__":
    sys.exit(main())
