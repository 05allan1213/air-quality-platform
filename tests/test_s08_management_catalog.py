"""S08 management directory over real sessions and an isolated F PostgreSQL ledger."""

import json
from datetime import date, timedelta
from time import monotonic

import pytest
import test_s06_accounts
from accounts.research_session import SessionCheck
from collection import services
from collection.models import ControlEvent, Job, Run, WorkWindow
from django.contrib.sessions.models import Session
from observations.models import QualityIssue, Source
from observations.policy import Scope, clock, revoke_grant
from s02_support import BOUNDARY, create_product, observe_db
from s08_backend_fixtures import seed_f
from test_s06_accounts import denied, json_status, login, permission, sign_in

from tests.collection.test_collection_postgres import LIMITS, grant, make_job, request

pytestmark = pytest.mark.django_db(transaction=True)
sql_hook = test_s06_accounts.sql_hook
web_clients = test_s06_accounts.web_clients

ADMIN = "/api/v1/admin/"
PRODUCTS = ADMIN + "catalog/products/"
RUNS = ADMIN + "collection-runs/"
TARGET = BOUNDARY + timedelta(hours=15)
EXECUTE = {
    "purpose": "execute",
    "scope": "synthetic",
    "target": TARGET.isoformat(),
    "reason": "manual",
}
DATES = {"scope": "synthetic", "start": "2024-01-02", "end": "2024-01-03"}
NEXT_DAY = {**DATES, "start": "2024-01-03", "end": "2024-01-04"}
RUN = "observations.run_collection"
REVIEW = "observations.review_observation"
DIAGNOSE = "accounts.diagnose_operations"
EMPTY = {"results": [], "next_cursor": None}


@pytest.fixture
def catalog(settings, monkeypatch, django_user_model, web_clients):
    settings.S03_FIXTURE_HTTP = True
    executor = django_user_model.objects.create_user("catalog-executor")
    executor.user_permissions.add(permission(RUN), permission("observations.manage_grants"))
    source = Source.objects.create(namespace="s03.fixture", publisher="F", collection_limits=LIMITS)
    job = make_job(executor, source)
    job.product.source_timezone = "Asia/Shanghai"
    job.product.save(update_fields=["source_timezone"])
    for operation in ("acquire_online", "process_local", "retain_normalized", "review"):
        grant(executor, job.product, operation)
    deliveries = []
    monkeypatch.setattr(services, "publish", deliveries.append)

    def client(name, *permissions):
        password = f"catalog-fixture-{name}-password"
        user = django_user_model.objects.create_user(f"catalog-{name}", password=password)
        user.user_permissions.add(*(permission(code) for code in permissions))
        return user, sign_in(web_clients(), user, password)

    return executor, source, job, deliveries, client


def get(web, params):
    response = web.get(PRODUCTS, params=params)
    assert "no-store" in response.headers["cache-control"]
    data = json_status(response, 200)
    assert set(data) == {"results", "next_cursor"}
    return data


def test_execute_uses_admission_without_registering_or_reserving(catalog):
    executor, source, job, deliveries, client = catalog
    _, runner = client("runner", RUN)
    _, reviewer = client("reviewer", REVIEW)
    _, both = client("review-runner", RUN, REVIEW)
    option = {
        "product": job.product_id,
        "job": job.pk,
        "reason": "manual",
        "target": "2024-01-02T15:00:00Z",
    }
    assert get(runner, EXECUTE) == {"results": [option], "next_cursor": None}
    assert get(runner, {**EXECUTE, "target": "2024-01-02T23:00:00+08:00"})["results"] == [option]
    assert get(runner, {**EXECUTE, "scope": "formal", "product": job.product_id}) == EMPTY
    assert get(runner, {**EXECUTE, "product": job.product_id + 1}) == EMPTY
    assert get(runner, {**EXECUTE, "target": "2023-12-31T00:00:00Z"}) == EMPTY
    denied(reviewer.get(PRODUCTS, params=EXECUTE), "permission_denied")
    denied(runner.get(PRODUCTS, params={**EXECUTE, "reason": "review"}), "permission_denied")
    denied(runner.get(PRODUCTS, params={**DATES, "purpose": "ledger"}), "permission_denied")
    assert get(both, {**EXECUTE, "reason": "review"})["results"] == [{**option, "reason": "review"}]
    assert not (
        deliveries
        or Run.objects.exists()
        or WorkWindow.objects.exists()
        or ControlEvent.objects.exists()
    )

    executor.user_permissions.remove(permission(RUN))
    assert get(runner, EXECUTE) == EMPTY
    executor.user_permissions.add(permission(RUN))
    for paused in (job.product, source):
        paused.collection_pause = "fixture_hold"
        paused.save(update_fields=["collection_pause"])
        assert get(runner, EXECUTE) == EMPTY
        paused.collection_pause = ""
        paused.save(update_fields=["collection_pause"])

    receipt = json_status(
        runner.post(RUNS, json=request(job, target=TARGET)),
        202,
    )
    assert len(deliveries) == 1 and str(deliveries[0]) == receipt["run_id"]
    run = Run.objects.get(pk=receipt["run_id"])
    assert run.reserved_at is None
    assert (
        get(runner, {**EXECUTE, "reason": "backfill"}) == EMPTY
    )  # In-flight, not an eligible gap.
    run.status = "failed"
    run.request_state = "possible"
    run.reserved_at = clock()
    run.occupied_until = run.reserved_at + timedelta(minutes=2)
    run.save(update_fields=["status", "request_state", "reserved_at", "occupied_until"])
    assert get(runner, EXECUTE) == EMPTY
    assert get(runner, {**EXECUTE, "reason": "backfill"}) == EMPTY
    run.occupied_until = run.reserved_at = clock() - timedelta(minutes=2)
    run.save(update_fields=["reserved_at", "occupied_until"])
    assert get(runner, {**EXECUTE, "reason": "backfill"})["results"] == [
        {**option, "reason": "backfill"}
    ]
    assert Run.objects.count() == WorkWindow.objects.count() == len(deliveries) == 1
    denied(runner.get(f"{ADMIN}collection-windows/{receipt['window_id']}/"), "permission_denied")


def test_ledger_quality_operations_use_different_witnesses(catalog):
    executor, source, job, _, client = catalog
    _, review = client("review", REVIEW)
    _, diagnose = client("diagnose", DIAGNOSE)
    _, both = client("review-ops", REVIEW, DIAGNOSE)
    assert get(review, {**DATES, "purpose": "ledger"}) == EMPTY
    assert get(review, {**DATES, "purpose": "quality"}) == EMPTY
    denied(review.get(PRODUCTS, params={**DATES, "purpose": "operations"}), "permission_denied")
    denied(diagnose.get(PRODUCTS, params={**DATES, "purpose": "operations"}), "permission_denied")
    json_status(diagnose.get(ADMIN + "operations/health/"), 200)
    operations = get(both, {**DATES, "purpose": "operations"})["results"]
    assert len(operations) == 1
    assert set(operations[0]["links"]) == {"trends"}
    trends = json_status(both.get(operations[0]["links"]["trends"]), 200)
    assert trends["buckets"][0]["window_count"] == 0

    run = services.submit(executor.pk, request(job, target=TARGET))
    entries = get(review, {**DATES, "purpose": "ledger"})["results"]
    assert len(entries) == 1 and set(entries[0]) == {"product", "source", "shape", "links"}
    ledger = entries[0]
    assert (ledger["product"], ledger["source"], ledger["shape"]) == (
        job.product_id,
        source.namespace,
        "C",
    )
    assert set(ledger["links"]) == {"windows", "runs"}
    assert (
        json_status(review.get(ledger["links"]["windows"]), 200)["results"][0]["id"]
        == run.window_id
    )
    assert json_status(review.get(ledger["links"]["runs"]), 200)["results"][0]["id"] == str(run.pk)
    assert get(review, {**NEXT_DAY, "purpose": "ledger"}) == EMPTY
    assert get(review, {**DATES, "purpose": "quality"}) == EMPTY

    # Retained unbound issue, independent of collection jobs and batch timestamps.
    historical = create_product("quality-only", "S", source)
    grant(executor, historical, "review")
    retention = grant(executor, historical, "retain_normalized")
    issue = QualityIssue.objects.create(
        product=historical,
        code="fixture_issue",
        scope=Scope("*", "*", "*", date(2024, 1, 2), date(2024, 1, 3)).serialize(),
    )
    entries = get(review, {**DATES, "purpose": "quality"})["results"]
    assert [(row["product"], row["shape"]) for row in entries] == [(historical.pk, "S")]
    assert set(entries[0]["links"]) == {"quality_issues"}
    opened = json_status(review.get(entries[0]["links"]["quality_issues"]), 200)
    assert [event["id"] for event in opened["results"]] == [issue.pk]
    assert get(review, {**NEXT_DAY, "purpose": "quality"}) == EMPTY
    revoke_grant(executor.pk, retention.pk, "F fixture rights ended")
    assert get(review, {**DATES, "purpose": "quality"}) == EMPTY


def test_operations_skips_catalog_only_locks(web_clients, sql_hook, record_property):
    seed = seed_f("s08-catalog-probe")
    fixture = seed["operations"]
    account = seed["accounts"]["ops_reviewer"]
    web = web_clients()
    web.headers.update(login(web, account["username"], account["password"]))
    query = {
        "purpose": "operations",
        "scope": "synthetic",
        "start": fixture["start"],
        "end": fixture["end"],
    }
    sql_states = []
    sql_elapsed = []

    def inspect(execute, sql, params, many, context):
        started = monotonic()
        try:
            return execute(sql, params, many, context)
        except Exception as exc:
            sql_states.append(
                getattr(exc, "sqlstate", None) or getattr(exc.__cause__, "sqlstate", None)
            )
            raise
        finally:
            sql_elapsed.append(monotonic() - started)

    sql_hook["catalog-operations"] = inspect
    options = {"params": query, "headers": {"X-S06-Test-Hook": "catalog-operations"}}
    normal = json_status(web.get(PRODUCTS, **options), 200)
    result_ids = {row["product"] for row in normal["results"]}
    assert fixture["product"] in result_ids and seed["products"]["L"] not in result_ids

    def while_product_locked(product_id):
        sql_states.clear()
        sql_elapsed.clear()
        with observe_db() as control, control.cursor() as cursor:
            cursor.execute(
                "SELECT id FROM observations_product WHERE id=%s FOR UPDATE", [product_id]
            )
            return web.get(PRODUCTS, **options)

    contended = while_product_locked(seed["products"]["L"])
    assert json_status(contended, 200) == normal
    assert sql_states == []
    assert len(sql_elapsed) < 80
    required_lock = while_product_locked(fixture["product"])
    denied(required_lock, "database_unavailable", 503)
    assert sql_states == ["55P03"]
    record_property(
        "s06_observed",
        json.dumps(
            {
                "http": contended.status_code,
                "replay_http": required_lock.status_code,
                "pg_lock_wait": True,
            }
        ),
    )


def test_cursor_binds_user_purpose_scope_and_conditions(catalog):
    executor, source, job, _, client = catalog
    _, web = client("page", RUN, REVIEW)
    _, other = client("other", RUN, REVIEW)
    make_job(executor, source, "L")  # No rights; must not supply a continuation position.
    product = create_product("T", "T", source)
    product.contract_version = "s03.f.v1"
    product.save(update_fields=["contract_version"])
    next_job = Job.objects.create(
        product=product,
        city=job.city,
        executor=executor,
        profile="f-exact-v1",
        enabled=True,
    )
    for operation in ("acquire_online", "process_local", "retain_normalized", "review"):
        grant(executor, next_job.product, operation)
    one = {**EXECUTE, "page_size": 1}
    first = get(web, one)
    assert [row["job"] for row in first["results"]] == [job.pk]
    assert first["next_cursor"]
    continued = {**one, "cursor": first["next_cursor"]}
    assert get(web, continued) == {
        "results": [
            {
                "product": next_job.product_id,
                "job": next_job.pk,
                "reason": "manual",
                "target": "2024-01-02T15:00:00Z",
            }
        ],
        "next_cursor": None,
    }
    denied(other.get(PRODUCTS, params=continued), "invalid_cursor", 400)
    for change in (
        {"page_size": 2},
        {"scope": "formal"},
        {"reason": "review"},
        {"target": "2024-01-02T14:00:00Z"},
        {"product": job.product_id},
    ):
        denied(web.get(PRODUCTS, params=continued | change), "invalid_cursor", 400)
    denied(
        web.get(
            PRODUCTS,
            params={**DATES, "purpose": "ledger", "page_size": 1, "cursor": first["next_cursor"]},
        ),
        "invalid_cursor",
        400,
    )
    next_job.enabled = False
    next_job.save(update_fields=["enabled"])
    assert get(web, one)["next_cursor"] is None
    assert get(web, continued) == EMPTY


def test_strict_input_and_end_session_recheck(catalog, monkeypatch):
    _, _, _, _, client = catalog
    _, web = client("check", RUN, REVIEW)
    for query, code in (
        ({"scope": "synthetic"}, "invalid_request"),
        ({**EXECUTE, "target": "2024-01-02T15:01:00Z"}, "invalid_target"),
        ({**EXECUTE, "target": "2024-01-02T15:00:00"}, "invalid_target"),
        ({**EXECUTE, "reason": "retry"}, "invalid_request"),
        ({**EXECUTE, "start": DATES["start"]}, "invalid_request"),
        ({**EXECUTE, "product": 2**63}, "invalid_request"),
        ({**EXECUTE, "page_size": 51}, "query_too_large"),
        ({**DATES, "purpose": "quality", "end": "2024-02-03"}, "query_too_large"),
        ({**DATES, "purpose": "ledger", "end": DATES["start"]}, "invalid_request"),
        ({**DATES, "purpose": "quality", "reason": "manual"}, "invalid_request"),
        ([*EXECUTE.items(), ("reason", "manual")], "invalid_request"),
    ):
        denied(web.get(PRODUCTS, params=query), code, 400)
    finish = SessionCheck.finish

    def expire_auth(session, relations):
        Session.objects.filter(session_key=session.key).update(expire_date=session.now)
        return finish(session, relations)

    monkeypatch.setattr(SessionCheck, "finish", expire_auth)
    denied(web.get(PRODUCTS, params=EXECUTE), "not_authenticated")
    assert not WorkWindow.objects.exists()


def test_expiring_lookahead_cannot_create_a_continuation(catalog, monkeypatch):
    executor, source, _, _, client = catalog
    _, web = client("expiry", REVIEW, DIAGNOSE)
    expiring = create_product("expiration", "C", source)
    deadline = clock() + timedelta(minutes=10)
    grant(executor, expiring, "review", valid_until=deadline)
    grant(executor, expiring, "retain_normalized")
    one = {**DATES, "purpose": "operations", "page_size": 1}
    assert get(web, one)["next_cursor"]
    finish = SessionCheck.finish

    def after_deadline(session, relations):
        assert finish(session, relations) < deadline
        return deadline

    monkeypatch.setattr(SessionCheck, "finish", after_deadline)
    denied(web.get(PRODUCTS, params=one), "use_denied")
