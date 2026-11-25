"""Fixed, private F execution chain for the S08 governance browser acceptance."""

import json
import os
import secrets
import stat
import sys
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from uuid import UUID

from s06_guard import require

MODE = "governance-chain"
SPEC = "e2e/governance-chain.spec.ts"
ROOT = Path("/tmp/s08-host")
WITNESS = ROOT / "governance-witness.json"
TARGET = datetime(2024, 2, 2, tzinfo=UTC)
ORIGIN_PORT = 18080
OBJECT_CODE = "101"
INDICATORS = {"AQI", "PM2.5", "PM10", "SO2", "NO2", "CO", "O3"}


def seed_chain(seed, run):
    from collection.models import Job, Run, WorkWindow
    from collection.services import change_job
    from django.conf import settings
    from django.contrib.auth import get_user_model
    from django.test import override_settings
    from s08_browser_fixture import grant_permissions

    users = get_user_model().objects
    roles = {}
    for role in ("executor", "peer"):
        password = secrets.token_urlsafe(32)
        user = users.create_user(f"{run}-chain-{role}", password=password)
        grant_permissions(user, ("observations.run_collection",))
        require(
            user.get_all_permissions() == {"observations.run_collection"}
            and not user.is_staff
            and not user.is_superuser,
            "governance_executor_permissions",
        )
        roles[role] = {"id": user.pk, "username": user.username, "password": password}
    fixture = seed["collection"]
    job = Job.objects.select_related("product__source").get(pk=fixture["job"])
    require(
        (job.product_id, job.city_id) == (fixture["product"], fixture["city"])
        and job.product.source.namespace == "s03.fixture"
        and job.product.shape == "C"
        and job.product.family == "F"
        and job.product.synthetic
        and job.product.contract_version == "s03.f.v1"
        and job.profile == "f-exact-v1"
        and job.expected_codes == [OBJECT_CODE]
        and job.next_due_at is None
        and job.plan_state == "legacy"
        and not job.enabled
        and not Run.objects.filter(job=job).exists()
        and not WorkWindow.objects.filter(product_id=job.product_id).exists()
        and not Job.objects.filter(enabled=True).exists(),
        "governance_fixture_job",
    )
    with override_settings(S03_FIXTURE_HTTP=True):
        enabled = change_job(seed["accounts"]["operator"]["id"], job.pk, "resume")
    require(
        not settings.S03_FIXTURE_HTTP and enabled.enabled and enabled.next_due_at is None,
        "governance_manual_job_only",
    )
    seed["governance_chain"] = {
        **roles,
        "source": job.product.source.namespace,
        "target": TARGET.isoformat(),
    }


@contextmanager
def fixed_origin():
    from s03_runtime import city_packet

    packet = city_packet(TARGET, OBJECT_CODE) | {"Area": "Collection Sample"}
    payload = json.dumps(packet).encode()
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            valid = (
                self.path == f"/CityData/GetAQIDataPublishLiveInfo?cityCode={OBJECT_CODE}"
                and self.headers.get_all("X-S03-Target") == [TARGET.isoformat()]
                and self.headers.get("Content-Length", "0") == "0"
                and self.headers.get("Transfer-Encoding") is None
                and not calls
            )
            calls.append(valid)
            if not valid:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            calls.append(False)
            self.send_error(405)

        def log_message(self, *_args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", ORIGIN_PORT), Handler) as server:
        thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
        thread.start()
        try:
            yield calls
        finally:
            server.shutdown()
            thread.join(2)
            require(not thread.is_alive(), "governance_origin_thread")


def read_private_json(path, limit):
    with open(
        path,
        encoding="utf-8",
        opener=lambda name, flags: os.open(name, flags | os.O_NOFOLLOW | os.O_NONBLOCK),
    ) as source:
        info = os.fstat(source.fileno())
        require(
            stat.S_ISREG(info.st_mode)
            and stat.S_IMODE(info.st_mode) == 0o600
            and info.st_uid == os.geteuid()
            and info.st_nlink == 1
            and 0 < info.st_size <= limit,
            "governance_private_artifact",
        )
        text = source.read(limit + 1)
    require(len(text) <= limit, "governance_private_artifact_size")
    return json.loads(text)


def service(role):
    import django
    from config.host_config import QUEUES, broker_ready, config_file, endpoints
    from s06_sandbox import check_network
    from s08_browser_host import DEV_PORTS, verify_database

    run = os.environ["AQ_INSTANCE_ID"]
    require(os.environ.get("S06_HOST_NET_NS"), "governance_host_namespace")
    check_network()
    values = config_file(ROOT / "host.env")
    root, port, socket_only = endpoints(values)
    require(
        (root, port, socket_only) == (ROOT, DEV_PORTS["api"], True)
        and values["AQ_INSTANCE_ID"] == run
        and values["CELERY_BROKER_URL"] == f"redis+socket://{ROOT}/broker/redis.sock"
        and values["AQ_WORKER_NODE"] == f"worker@{run}"
        and values.get("AQ_S08_FIXTURE_SCHEDULE") == "1",
        "governance_service_identity",
    )
    verify_database(values, run)
    broker_ready(values)
    fixture = read_private_json(ROOT / "browser-seed.json", 32768)["governance_chain"]
    require(
        fixture["source"] == "s03.fixture" and fixture["target"] == TARGET.isoformat(),
        "governance_service_fixture",
    )
    os.environ.update(values, DJANGO_SETTINGS_MODULE="config.settings", AQ_HOST_OFFLINE="1")
    if role == "worker":
        os.environ["AQ_S08_FIXTURE_SCHEDULE"] = "0"
    django.setup()
    from collection.adapters import transport
    from django.conf import settings

    require(
        not settings.S03_FIXTURE_HTTP
        and not settings.CNEMC_FIXTURE_HTTP
        and settings.CNEMC_MODE == "off",
        "governance_initial_source_mode",
    )
    transport.ORIGIN = f"http://127.0.0.1:{ORIGIN_PORT}"
    settings.S03_FIXTURE_HTTP = True
    if role == "web":
        from gunicorn.app.wsgiapp import run as gunicorn

        sys.argv = [
            "gunicorn",
            "config.wsgi:application",
            "-c",
            "python:config.gunicorn_local",
            "--bind",
            f"127.0.0.1:{port}",
        ]
        gunicorn()
    else:
        from config.celery import app

        app.worker_main(
            [
                "worker",
                "-l",
                "WARNING",
                "--pool",
                "solo",
                "-Q",
                ",".join(sorted(QUEUES)),
                "-n",
                settings.OPERATIONS_WORKER,
                "--without-gossip",
                "--without-mingle",
                "--prefetch-multiplier",
                "1",
            ]
        )


def verify_chain(env, seed, calls, before, after):
    """Reconcile the exact browser command and authorized read with an independent PG snapshot."""
    import psycopg

    require(calls == [True], "governance_single_origin_request")
    require(after == [n + 1 for n in before], "governance_execution_delta")
    witness = read_private_json(WITNESS, 4096)
    require(
        set(witness) == {"command", "run", "window", "batch", "first_write", "fetched"},
        "governance_witness_schema",
    )
    fixture = seed["collection"]
    run_id, command = UUID(witness["run"]), UUID(witness["command"])
    window, batch = witness["window"], witness["batch"]
    actor = seed["governance_chain"]["executor"]["id"]
    require(
        all(type(value) is int and value > 0 for value in (window, batch)) and command.version == 4,
        "governance_witness_keys",
    )
    first_write = datetime.fromisoformat(witness["first_write"])
    fetched = datetime.fromisoformat(witness["fetched"])
    with psycopg.connect(
        host=env["POSTGRES_HOST"],
        port=5432,
        dbname=env["POSTGRES_DB"],
        user=env["POSTGRES_USER"],
        password=env["POSTGRES_PASSWORD"],
        connect_timeout=3,
        options="-c default_transaction_read_only=on -c statement_timeout=2500",
    ) as db:
        db.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        rows = db.execute(
            """
            SELECT r.id, r.request_id, r.job_id, r.actor_id, r.window_id, r.batch_id,
                   r.reason, r.status, r.dispatch, r.request_state, r.attempt, r.deliveries,
                   r.fetched_at, w.target_start, w.target_end, w.closed_at IS NOT NULL,
                   b.product_id, b.profile, b.fetched_at, b.redacted
              FROM collection_run r
              JOIN collection_workwindow w ON w.id = r.window_id
              JOIN observations_batch b ON b.id = r.batch_id
             WHERE r.job_id = %s OR w.product_id = %s
        """,
            (fixture["job"], fixture["product"]),
        ).fetchall()
        require(
            rows
            == [
                (
                    run_id,
                    command,
                    fixture["job"],
                    actor,
                    window,
                    batch,
                    "manual",
                    "finished",
                    "sent",
                    "responded",
                    1,
                    1,
                    fetched,
                    TARGET,
                    TARGET + timedelta(hours=1),
                    True,
                    fixture["product"],
                    "f-exact-v1",
                    fetched,
                    False,
                )
            ],
            "governance_pg_command_window_run_batch",
        )
        counts = db.execute(
            """
            SELECT (SELECT count(*) FROM collection_workwindow WHERE product_id = %s),
                   (SELECT count(*) FROM observations_batch WHERE product_id = %s),
                   (SELECT count(*) FROM observations_cityobservation WHERE product_id = %s),
                   (SELECT count(*) FROM collection_runitem WHERE window_id = %s),
                   (SELECT count(*) FROM observations_receipt WHERE batch_id = %s)
        """,
            (fixture["product"], fixture["product"], fixture["product"], window, batch),
        ).fetchone()
        require(counts == (1, 1, 7, 7, 7), "governance_pg_no_duplicates")
        accepted = db.execute(
            """
            SELECT i.id, i.item_key, i.indicator, o.id, r.created_at
              FROM collection_runitem i
              JOIN observations_receipt p ON p.collection_item_id = i.id
              JOIN observations_cityrevision r ON r.id = p.city_revision_id
              JOIN observations_cityobservation o ON o.id = r.key_id AND o.indicator = i.indicator
             WHERE i.run_id = %s AND i.window_id = %s AND i.object_code = %s
               AND i.observed_at = %s AND i.accepted AND i.status = 'accepted'
               AND p.batch_id = %s AND p.outcome = 'first'
               AND r.is_current AND NOT r.candidate AND r.kind = 'initial'
               AND r.revision_no = 1 AND r.quality = 'valid'
               AND o.product_id = %s AND o.city_id = %s AND o.observed_at_utc = %s
               AND (SELECT count(*) FROM observations_cityrevision WHERE key_id = o.id) = 1
        """,
            (
                run_id,
                window,
                OBJECT_CODE,
                TARGET,
                batch,
                fixture["product"],
                fixture["city"],
                TARGET,
            ),
        ).fetchall()
        require(
            len(accepted) == 7
            and {row[2] for row in accepted} == INDICATORS
            and all(len({row[index] for row in accepted}) == 7 for index in (0, 1, 3))
            and TARGET < fetched <= min(row[4] for row in accepted)
            and first_write == max(row[4] for row in accepted),
            "governance_pg_first_valid_write",
        )
    return {
        "runs": 1,
        "windows": 1,
        "batches": 1,
        "accepted_items": 7,
        "receipts": 7,
        "first_valid_revisions": 7,
        "origin_requests": 1,
        "command_correlated": True,
        "first_valid_write_correlated": True,
        "beat_started": False,
    }


if __name__ == "__main__":
    require(len(sys.argv) == 2 and sys.argv[1] in {"web", "worker"}, "governance_fixed_role")
    service(sys.argv[1])
