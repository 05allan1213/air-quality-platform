"""V12 administrator sample with separate sessions, a local F origin, Redis and Worker."""

import json
import os
import secrets
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from uuid import uuid4

from s01_runtime import FIXTURE_POST, wait_for
from s06_guard import require
from s07_contract import PREFIX, ContractDrift, ContractHttp, Witness, check_response
from s07_contract_upgrade import diagnostic, session
from s07_host_guard import check_broker, check_database, check_process_network

ADMIN = PREFIX + "admin/"
JOBS = ADMIN + "collection-jobs/"
RUNS = ADMIN + "collection-runs/"
WINDOWS = ADMIN + "collection-windows/"
QUALITY = ADMIN + "quality-issues/"
HEALTH = ADMIN + "operations/health/"
FIXTURE = ADMIN + "fixture-runs/"
LEGACY = PREFIX + "fixtures/observations/"
ACCOUNT = ADMIN + "accounts/{pk}/actions/"
AUTH = PREFIX + "auth/"


@contextmanager
def fixture_origin():
    from s03_runtime import city_packet

    accepted = []

    class Origin(BaseHTTPRequestHandler):
        def do_POST(self):
            if self.path != "/CityData/GetAQIDataPublishLiveInfo?cityCode=123":
                self.send_error(404)
                return
            slot = datetime.fromisoformat(self.headers["X-S03-Target"])
            packet = json.dumps(city_packet(slot, "123")).encode()
            accepted.append(slot)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(packet)))
            self.end_headers()
            self.wfile.write(packet)

        def log_message(self, *_args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Origin) as server:
        thread = Thread(target=server.serve_forever)
        thread.start()
        try:
            yield server.server_port, accepted
        finally:
            server.shutdown()
            thread.join(4)
            require(not thread.is_alive(), "contract_origin_remaining")


def seed():
    from collection.models import Job
    from django.contrib.auth import get_user_model
    from django.contrib.auth.models import Permission
    from observations.models import City, CityIdentifier, Source
    from psycopg.types.range import TimestamptzRange
    from s02_support import START, allow, create_product
    from s03_runtime import LIMITS

    pwd = secrets.token_urlsafe(24)
    users = get_user_model().objects
    op = users.create_user("s07-f-contract-operator", password=pwd, is_staff=True)
    reader = users.create_user("s07-f-contract-reader", password=pwd)
    op.user_permissions.add(
        *Permission.objects.filter(
            codename__in=(
                "manage_grants",
                "manage_collection",
                "run_collection",
                "review_observation",
                "diagnose_operations",
                "manage_accounts",
                "run_fixture",
            )
        )
    )
    source = Source.objects.create(
        namespace="s03.fixture", publisher="S07 contract F", collection_limits=LIMITS
    )
    prod = create_product("C", shape="C", source=source)
    prod.contract_version = "s03.f.v1"
    prod.save(update_fields=["contract_version"])
    target = City.objects.create(source=source, name="Fixture 123", geography="fixture-123")
    CityIdentifier.objects.create(
        source=source,
        city=target,
        code="123",
        active_period=TimestamptzRange(START, None),
        evidence="S07 F",
    )
    Job.objects.create(
        product=prod,
        city=target,
        executor=op,
        profile="f-exact-v1",
        expected_codes=["123"],
        roster_basis="S07 F",
        enabled=True,
    )
    for use in ("acquire_online", "process_local", "retain_normalized", "review", "display"):
        limits = {"collection_limits": LIMITS} if use == "acquire_online" else {}
        allow(op, prod, use, data_from=date.min, data_until=date.max, fields=["*"], **limits)
    return op, reader, pwd


def sign_in(web, user, password):
    csrf = check_response(web.get(AUTH + "csrf/"), AUTH + "csrf/", 200)["csrf_token"]
    initial = web.cookies.get("csrftoken")
    login = web.post(
        AUTH + "login/",
        json={"username": user.username, "password": password},
        headers={"X-CSRFToken": csrf},
    )
    require(
        check_response(login, AUTH + "login/", 200)["username"] == user.username,
        "contract_session_user",
    )
    require(initial != web.cookies.get("csrftoken"), "contract_csrf_not_rotated")
    web.headers["X-CSRFToken"] = check_response(web.get(AUTH + "csrf/"), AUTH + "csrf/", 200)[
        "csrf_token"
    ]


def run_window(web, job, at, expected):
    from collection.models import Run

    output = check_response(
        web.post(
            RUNS,
            json={
                "job": job,
                "target": at.isoformat(),
                "reason": "manual",
                "request_id": str(uuid4()),
            },
        ),
        RUNS,
        202,
    )
    run_id = output["run_id"]
    window = output["window_id"]

    def settled():
        row = Run.objects.filter(pk=run_id).first()
        return row if row and row.status == expected else None

    record = wait_for("contract F worker to settle", settled, seconds=16)
    require(record.window_id == window and record.deliveries == 1, "contract_worker_delivery")
    query = {"scope": "synthetic", "product": record.job.product_id}
    listing = check_response(web.get(RUNS, params=query), RUNS, 200)
    row = next(i for i in listing["results"] if i["id"] == run_id)
    info = check_response(web.get(row["links"]["detail"]), RUNS + "{run_id}/", 200)
    require(info["window_id"] == window, "contract_run_window")
    view = check_response(web.get(row["links"]["window"]), WINDOWS + "{pk}/", 200)
    issues = check_response(web.get(row["links"]["quality_issues"]), QUALITY, 200)
    health = check_response(web.get(HEALTH, params={"window": window}), HEALTH, 200)
    require(view["window_id"] == window, "contract_window_receipt")
    return {
        "status": record.status,
        "dispatch": output["dispatch"],
        "http": 202,
        "result": record.result,
        "error_code": record.error_code,
        "E": view["coverage"]["E"],
        "O": view["coverage"]["O"],
        "V": view["coverage"]["V"],
        "quality_events": len(issues["results"]),
        "health_pg": health["components"]["pg"]["state"],
    }


def legacy_fixture(admin, reader):
    from samples.models import FixtureRun

    check_response(reader.post(FIXTURE, json=FIXTURE_POST), FIXTURE, 403, code="permission_denied")
    receipt = check_response(admin.post(FIXTURE, json=FIXTURE_POST), FIXTURE, 202)
    wait_for(
        "legacy F worker commit",
        lambda: FixtureRun.objects.filter(pk=receipt["run_id"], state="succeeded").exists(),
        seconds=16,
    )
    data = check_response(
        reader.get(LEGACY, params={"product": "F", "observed_at": "2001-02-03T00:00:00Z"}),
        LEGACY,
        200,
    )
    require(data["count"] == 1 and data["results"][0]["synthetic"], "contract_legacy_fixture")
    return {"state": "succeeded", "rows": data["count"]}


def account_action(admin, reader, target):
    path = ACCOUNT.format(pk=target.pk)
    snapshot = check_response(admin.get(path), ACCOUNT, 200)
    body = {
        "action": "disable",
        "request_id": str(uuid4()),
        "reason_code": "account_review",
        "expected_version": snapshot["version"],
    }
    check_response(reader.post(path, json=body), ACCOUNT, 403, code="permission_denied")
    check_response(admin.get(ACCOUNT.format(pk=target.pk + 9000)), ACCOUNT, 404, code="not_found")
    csrf = admin.headers.pop("X-CSRFToken")
    check_response(admin.post(path, json=body), ACCOUNT, 403, code="csrf_failed")
    admin.headers["X-CSRFToken"] = csrf
    check_response(
        admin.post(path, json=body | {"expected_version": snapshot["version"] + 1}),
        ACCOUNT,
        409,
        code="precondition_conflict",
    )
    changed = check_response(admin.post(path, json=body), ACCOUNT, 200)
    require(
        changed["active"] is False and changed["version"] == snapshot["version"] + 1,
        "contract_account_write",
    )
    require(
        check_response(admin.post(path, json=body), ACCOUNT, 200) == changed,
        "contract_account_replay",
    )
    check_response(reader.get(PREFIX + "me/"), PREFIX + "me/", 403, code="not_authenticated")
    return {
        "ordinary": 403,
        "missing_id": 404,
        "missing_csrf": 403,
        "stale_version": 409,
        "disable": 200,
        "replay": 200,
        "inactive_session": 403,
    }


def verify(root, runtime):
    import django
    import httpx
    from django.conf import settings
    from django.core.management import call_command

    env = os.environ.copy()
    check_process_network(env["S06_HOST_NET_NS"])
    check_database(env, empty=True)
    check_broker(env, empty=True)
    target = root / "V12"
    target.mkdir(mode=0o700)
    witness = Witness()
    result = {"origin": "F/I", "status": "FAIL", "started_utc": datetime.now(UTC).isoformat()}
    try:
        phase = "fixture_startup"
        with fixture_origin() as (port, requests):
            os.environ.update(
                DJANGO_SETTINGS_MODULE="s07_contract_settings", S07_CONTRACT_ORIGIN_PORT=str(port)
            )
            django.setup()
            call_command("migrate", interactive=False, verbosity=0)
            op, reader, password = seed()
            queues = {settings.CELERY_TASK_DEFAULT_QUEUE}
            queues.update(route["queue"] for route in settings.CELERY_TASK_ROUTES.values())
            result["worker_queues"] = runtime.worker(queues=queues)
            phase = "authenticated_collection"
            with (
                session(witness=witness) as web,
                httpx.Client(
                    base_url=web.base_url,
                    trust_env=False,
                    timeout=8,
                    event_hooks={"response": [witness.inspect]},
                ) as user,
            ):
                sign_in(web, op, password)
                sign_in(user, reader, password)
                catalog = check_response(web.get(JOBS, params={"scope": "synthetic"}), JOBS, 200)
                (job,) = catalog["results"]
                control = check_response(web.get(job["links"]["detail"]), JOBS + "{pk}/", 200)
                require(control["job"] == job["id"], "contract_job_catalog")
                at = datetime.now(UTC).replace(minute=0, second=0, microsecond=0) - timedelta(
                    hours=2
                )
                result["collection"] = run_window(web, job["id"], at, "finished")
                require(
                    result["collection"]["V"] == 7 and requests == [at],
                    "contract_first_business_cells",
                )
                phase = "legacy_fixture"
                result["legacy_fixture"] = legacy_fixture(web, user)
                phase = "account"
                result["account"] = account_action(web, user, reader)
        phase = "origin_disconnected"
        with session(witness=witness) as web:
            sign_in(web, op, password)
            result["outage"] = run_window(web, job["id"], at + timedelta(hours=1), "failed")
            require(result["outage"]["error_code"] == "upstream_transient", "contract_outage_class")
            latest = check_response(
                web.get(
                    PREFIX + "cities/{city}/".format(city=job_city(job["id"])),
                    params={"product": job["product"], "scope": "synthetic"},
                ),
                PREFIX + "cities/{city}/",
                200,
            )
            require(
                datetime.fromisoformat(latest["snapshot_at"]) == at,
                "contract_last_authorized_batch",
            )
            result["outage"]["last_valid_unchanged"] = True
        result["http_contract"] = witness.receipt()
        result["status"] = "PASS"
    except Exception as exc:
        result["phase"] = phase
        result["failure"] = diagnostic(exc)
        if isinstance(exc, (ContractDrift, ContractHttp)):
            # Only status comparisons or validator paths enter this receipt.
            result["comparator"] = exc.args[0]
        raise
    finally:
        result["finished_utc"] = datetime.now(UTC).isoformat()
        (target / "admin.json").write_text(json.dumps(result, indent=2) + "\n")


def job_city(job):
    from collection.models import Job

    return Job.objects.values_list("city_id", flat=True).get(pk=job)
