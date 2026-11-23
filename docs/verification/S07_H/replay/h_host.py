"""Exercise V05 and V14 with real H through the isolated S07 B host runner."""

import argparse
import hashlib
import json
import os
import secrets
import shutil
import stat
import sys
import tempfile
import traceback
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

REPLAY = Path(__file__).resolve().parent
ROOT = REPLAY.parents[3]
sys.path.insert(0, str(ROOT / "scripts"))

import h_case  # noqa: E402
import verify_s07_host as base  # noqa: E402
from s04_checks import Probe, call, sql  # noqa: E402
from s06_guard import IsolationError, require  # noqa: E402
from s06_runtime import Runtime  # noqa: E402
from s07_host_guard import broker_client, check_broker, check_database, check_environment  # noqa: E402
from s07_offline import OfflineChecks  # noqa: E402
from s07_offline_host import HostOffline, owned_volume, postgres_bridge  # noqa: E402
from s07_startup_checks import http, prepare, verify_lock  # noqa: E402

REPORT = Path("V01/h.json")
ARCHIVE = "h-inner.zip"
SCOPES = ("V05-H", "V14-H", "T10-H-B")
SETUP_STAGE = """
/usr/bin/mount --internal-only -t tmpfs -o rw,nosuid,nodev,noexec,size=32m,mode=0700 tmpfs "$1"
shift
exec "$@"
"""


def now():
    return datetime.now(UTC).isoformat()


def record(path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str) + "\n")


def record_failure(report, error):
    report["error"] = type(error).__name__
    if isinstance(error, (AssertionError, IsolationError)):
        report["check"] = str(error)
    report["frames"] = [
        [Path(frame.filename).name, frame.lineno, frame.name]
        for frame in traceback.extract_tb(error.__traceback__)[-10:]
    ]


def command(env, snapshot, root):
    argv = base.sandbox_command(env, snapshot, "offline")
    mount = argv.index("--")
    stage = str(root / "stage")
    archive = str(root / ARCHIVE)
    # A private outer mount namespace provides the exact tmpfs required by main.py.
    # The inner sandbox keeps the usual unsharing and --disable-userns.
    guarded = [
        argv[0],
        "--uid",
        str(os.geteuid()),
        "--gid",
        str(os.getegid()),
        *argv[1:mount],
        "--bind",
        stage,
        stage,
        "--ro-bind",
        archive,
        archive,
        "--",
        argv[mount + 1],
        "-B",
        str(REPLAY / "h_host.py"),
        "_sandbox",
    ]
    return [
        "/usr/bin/unshare",
        "--user",
        "--map-root-user",
        "--mount",
        "--propagation",
        "private",
        "--net",
        "--",
        "/usr/bin/sh",
        "-euc",
        SETUP_STAGE,
        "h-host-stage",
        stage,
        *guarded,
    ]


def witness(root):
    stage = root / "stage"
    mount = next(
        row.split()
        for row in Path("/proc/self/mountinfo").read_text().splitlines()
        if row.split()[4] == str(stage) and row.split()[3] == "/"
    )
    flags = set(mount[5].split(","))
    limits = os.statvfs(stage)
    capacity = limits.f_frsize * limits.f_blocks
    status = dict(line.split(":", 1) for line in Path("/proc/self/status").read_text().splitlines())
    require(
        mount[mount.index("-") + 1] == "tmpfs"
        and {"rw", "nosuid", "nodev", "noexec"} <= flags
        and capacity == 32 * 1024**2
        and all(int(status[key], 16) == 0 for key in ("CapEff", "CapPrm", "CapAmb", "CapInh"))
        and status["NoNewPrivs"].strip() == "1"
        and not Path("/var/run/docker.sock").exists(),
        "host_h_mount_or_privilege",
    )
    return {
        "filesystem": "tmpfs",
        "flags": sorted(flags),
        "capacity": capacity,
        "effective_capabilities": 0,
        "docker_socket": False,
    }


def database_counts():
    data = sql("""SELECT
        (SELECT count(*) FROM observations_batch),
        count(DISTINCT (k.site_epoch_id,k.observed_at_utc)), count(DISTINCT k.id),
        count(DISTINCT r.id), count(rc.id),
        count(*) FILTER (WHERE r.quality = 'valid'),
        count(*) FILTER (WHERE r.quality = 'missing'),
        (SELECT count(*) FROM collection_run),
        (SELECT count(*) FROM collection_run WHERE request_state <> 'unsent')
        FROM observations_stationobservation k
        LEFT JOIN observations_stationrevision r ON r.key_id = k.id
        LEFT JOIN observations_receipt rc ON rc.station_revision_id = r.id""")[0]
    require(data == (1, 192, 1152, 1152, 1152, 1130, 22, 0, 0), "host_h_sql_counts")
    return dict(
        zip(
            (
                "batches",
                "station_hours",
                "keys",
                "revisions",
                "observed_receipts",
                "valid",
                "missing",
                "collection_runs",
                "source_requests",
            ),
            data,
            strict=True,
        )
    )


def redis_pushes(env):
    with broker_client(env) as broker:
        counts = broker.info("commandstats")
        return sum(counts.get(f"cmdstat_{name}", {}).get("calls", 0) for name in ("lpush", "rpush"))


def public_statistics(payload):
    summary = payload["results"][0]
    return {
        "window_start": summary["window_start"],
        "window_end": summary["window_end"],
        "indicator": summary["indicator"],
        "unit": summary["unit"],
        "E_common": summary["E_common"],
        "P": summary["P"],
        "mean_difference": summary["mean_difference"],
        "stations": {
            name: {key: value for key, value in counts.items() if key != "statistics_version"}
            for name, counts in summary["stations"].items()
        },
        "support_pages": summary["support_pages"],
        "csv": summary["csv"],
        "postgresql": summary["postgresql"],
        "ungranted_window": payload["gap"],
        "export_detail": payload["export_detail"],
        "export_aggregate": payload["export_aggregate"],
        "export_tickets": payload["tickets"],
    }


def cold_charts(client, rows, raw):
    # The backend chart contract is statistics.chart, under the same scoped display grant.
    start = h_case.STARTS[0]
    epochs = {row["name"]: row["site_epoch"] for row in rows}
    product = rows[0]["product"]
    csv_values = h_case.csv_oracle(raw)
    pg = h_case.sql_rows(product, epochs, csv_values)
    counts = {}
    for station in h_case.STATIONS:
        query = h_case.intent(product, [epochs[station]], "CO", start)
        result = call(client, "/api/v1/analysis/statistics/", method="POST", body=query)
        chart = result["chart"]
        points = chart["points"]
        require(chart["resolution"] == "exact" and len(points) == 48, "cold_h_chart_resolution")
        missing = 0
        for index, point in enumerate(points):
            stamp = start + timedelta(hours=index)
            key = station, "CO", stamp
            quality, value = csv_values[key]
            require(
                datetime.fromisoformat(point["at"]) == stamp
                and point["pos"] == 0
                and point["expected"] is True
                and point["key_id"] == pg[key][0]
                and point["quality"] == quality
                and (None if point["value"] is None else Decimal(str(point["value"]))) == value,
                "cold_h_chart_identity_or_value",
            )
            missing += value is None
        counts[station] = {"points": len(points), "nulls": missing}
    return {"resolution": "exact", "http": 200, "csv_pg_http_match": True, "stations": counts}


def cold_cycle(host, raw, report):
    from django.db import connections

    checks = OfflineChecks(host, report, host.password)
    env = host.env
    h_case.guard(env["AQ_INSTANCE_ID"])
    product, owner, until = h_case.register(host.password)
    registered = h_case.grants(product)
    report["use_grants"] = {
        "count": len(registered),
        "terms_version": h_case.VERSION,
        "first_valid_from": min(row["valid_from"] for row in registered),
        "valid_until": until,
        "audiences": sorted({row["audience"] for row in registered}),
        "operations": sorted({row["operation"] for row in registered}),
    }
    report["database"] = check_database(env, empty=False)
    report["isolation"] = host.network()
    report["volume_before"] = volume = host.volume()
    report["pg_started_before"] = pg_before = checks.db_stamp()
    report["redis_before"] = redis_before = host.broker_id()
    host.all_start()
    checks.ready(200)
    before = redis_pushes(env)
    report["postgres_import"] = h_case.stage(raw, owner)
    pushes = redis_pushes(env) - before
    require(pushes >= 1, "host_h_broker_publication")
    report["redis_pushes_during_import"] = pushes
    report["database_before_cold"] = database_counts()

    original = Probe(host.base).login(h_case.READER, host.password)
    original_key = checks.session_id(original)
    original_rows = h_case.discover(original)
    favorite = h_case.favorite(original, original_rows)
    report["V05-H"] = {
        "status": "PASS",
        "catalog": sorted(row["name"] for row in original_rows),
        "source": "uci.501/uci501",
        "attribution": product.attribution,
        "discovery_page_size": 1,
        "station_detail_and_history": 200,
        "favorite_revisit": 200,
    }
    reference = h_case.reconcile(original, original_rows, raw, full=False)
    report["before_cold"] = public_statistics(reference)
    host.all_stop()
    # Release the test process ORM connection before restarting its PostgreSQL server.
    connections.close_all()
    require(http("/health/ready/", host.base)[0] is None, "host_h_web_still_serving")
    host.stop("redis")
    require(not (host.root / "broker/redis.sock").exists(), "host_h_redis_still_listening")
    host.stop("pg")
    report["all_stopped"] = host.stopped()
    report["volume_while_stopped"] = host.volume()
    require(report["volume_while_stopped"] == volume, "host_h_volume_while_stopped")

    host.start("pg")
    host.start("redis")
    require(checks.db_stamp() != pg_before and host.volume() == volume, "host_h_pg_cold_identity")
    require(host.broker_id() != redis_before, "host_h_redis_cold_identity")
    check_broker(env, empty=True)
    host.all_start()
    checks.ready(200)
    new = Probe(host.base).login(h_case.READER, host.password)
    require(
        original_key == checks.session_id(original) != checks.session_id(new),
        "host_h_session_identity",
    )
    for name, client in (("original_session", original), ("new_session", new)):
        rows = h_case.discover(client)
        require(
            call(client, favorite)["id"] == int(favorite.rstrip("/").rsplit("/", 1)[-1]),
            "host_h_favorite_revisit",
        )
        result = h_case.reconcile(client, rows, raw, full=False)
        require(result == reference, "host_h_selection_changed")
        report[name] = {
            "status": "PASS",
            "discovery": 200,
            "favorite_revisit": 200,
            "analysis": public_statistics(result),
            "chart": cold_charts(client, rows, raw),
        }
    report["same_session_and_distinct_new_session"] = True
    report["volume_after"] = host.volume()
    report["pg_started_after"] = checks.db_stamp()
    report["redis_after"] = host.broker_id()
    report["database_after_cold"] = database_counts()
    report["isolation_after_cold"] = host.network()
    report["stage_after"] = {
        "raw_files": len(
            [p for p in (host.root / "stage").iterdir() if not p.name.startswith(".")]
        ),
        "import_attempts": sql("SELECT count(*) FROM observations_importattempt")[0][0],
    }
    require(
        report["stage_after"] == {"raw_files": 0, "import_attempts": 1},
        "host_h_staged_input_cleanup",
    )
    host.all_stop()
    report["V14-H"] = {"status": "PASS"}
    report["T10-H-B"] = {"status": "PASS"}


def sandbox():
    env = os.environ.copy()
    root = check_environment(env)
    env["S06_RUN"] = os.environ["S06_RUN"] = env["AQ_INSTANCE_ID"]
    report = {"status": "FAIL", "cases": [], "started_utc": now()}
    runtime = Runtime(root)
    try:
        report["before"] = check_database(env, empty=True)
        report["stage_mount"] = witness(root)
        with (root / ARCHIVE).open("rb") as media:
            raw = h_case.load_inner(media)
        report["archive"] = {"size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
        with runtime.managed(), tempfile.TemporaryDirectory(prefix="offline-", dir=root) as folder:
            work = Path(folder)
            base.start_broker(runtime, env)
            report["broker_before"] = check_broker(env, empty=True)
            report["foreign_broker_rejected"] = base.rejected(
                lambda: check_broker(env, empty=True, marker="foreign"), "broker_identity"
            )
            require(
                check_database(env, empty=True) == report["before"], "host_h_before_guard_mutated"
            )
            env["AQ_BEAT_LOCK_FILE"] = str(root / "V02" / "beat.lock")
            env["AQ_STATIC_ROOT"] = str(work / "public-static")
            verify_lock(env)
            prepare(env, work)
            os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings"
            os.environ["AQ_HOST_OFFLINE"] = "1"
            import django

            django.setup()
            from django.conf import settings

            require(not settings.CELERY_TASK_ALWAYS_EAGER, "host_h_no_eager")
            host = HostOffline(
                root, work, env, runtime, base.start_broker, report, secrets.token_hex(24)
            )
            with host.stack:
                cold_cycle(host, raw, report)
        report["status"] = "PASS"
    except Exception as error:
        record_failure(report, error)
    finally:
        report["redis_lifecycle"] = runtime.lifecycle
        report["remaining_processes"] = len(runtime.children)
        report["finished_utc"] = now()
        record(root / REPORT, report)
    return int(report["status"] != "PASS")


def run_host(archive):
    os.umask(0o077)
    require(archive.is_file() and not archive.is_symlink(), "host_h_regular_input")
    with archive.open("rb") as media:
        raw = h_case.load_inner(media)
    evidence_root = ROOT / "tmp" / "s07-h-supplement"
    require(evidence_root.resolve(strict=True) == evidence_root, "host_h_evidence_root")
    info = evidence_root.stat()
    require(
        info.st_uid == os.geteuid() and not stat.S_IMODE(info.st_mode) & 0o022,
        "host_h_evidence_owner",
    )
    receipt_path = Path(tempfile.mkdtemp(prefix="B-", dir=evidence_root)) / "result.json"
    run = f"s07-b-{secrets.token_hex(6)}"
    root = base.make_root(run)
    env = base.environment(run, root)
    receipt = {
        "run": run,
        "scope": SCOPES,
        "origin": "H",
        "status": "FAIL",
        "rejections": [],
        "started_utc": now(),
        "cleanup": False,
        "entry": "python main.py",
    }
    print(receipt_path.relative_to(ROOT), flush=True)
    try:
        receipt["before"] = base.inventory(run)
        require(not any(receipt["before"].values()), "host_h_scope_exists")
        receipt["code_commit"] = base.checkout(ROOT)["head"]
        receipt["lock_sha256"] = hashlib.sha256((ROOT / "uv.lock").read_bytes()).hexdigest()
        with base.freeze_source(root) as (snapshot, digest):
            receipt["source_sha256"] = digest
            (snapshot / REPLAY.relative_to(ROOT)).mkdir(parents=True)
            receipt["replay_sha256"] = {}
            for name in ("h_case.py", "h_host.py"):
                body = (REPLAY / name).read_bytes()
                (snapshot / REPLAY.relative_to(ROOT) / name).write_bytes(body)
                receipt["replay_sha256"][name] = hashlib.sha256(body).hexdigest()
            (root / ARCHIVE).write_bytes(raw)
            with (
                owned_volume(env, receipt, enabled=True) as volume,
                base.cluster(env, receipt, volume),
            ):
                receipt["database_before"] = check_database(env, empty=True)
                base.host_negative(env, receipt)
                (root / "V02").mkdir(mode=0o700)
                (root / "V02" / "beat.lock").touch(mode=0o444, exist_ok=False)
                base.register_startup_lock(env, receipt)
                receipt["sandbox_exit"] = postgres_bridge(
                    command(env, snapshot, root), env, receipt
                )
                if (root / REPORT).is_file():
                    receipt["sandbox"] = json.loads((root / REPORT).read_text())
                require(receipt["sandbox_exit"] == 0, "host_h_sandbox_exit")
                require(receipt["sandbox"]["status"] == "PASS", "host_h_sandbox_receipt")
    except Exception as error:
        record_failure(receipt, error)
    finally:
        try:
            receipt["after"] = base.inventory(run)
            require(not any(receipt["after"].values()), "host_h_resources_remaining")
            require(
                root == base.work_root(run) and root.resolve(strict=True) == root,
                "host_h_cleanup_path",
            )
            shutil.rmtree(root)
            receipt["cleanup"] = not root.exists()
        except Exception as error:
            receipt["cleanup_error"] = type(error).__name__
        receipt["finished_utc"] = now()
        if "error" not in receipt and receipt["cleanup"]:
            receipt["status"] = "PASS"
        record(receipt_path, receipt)
    print(receipt["status"], flush=True)
    return int(receipt["status"] != "PASS")


def main():
    if sys.argv[1:] == ["_sandbox"]:
        return sandbox()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uci-archive", type=Path, required=True)
    return run_host(parser.parse_args().uci_archive)


if __name__ == "__main__":
    sys.exit(main())
