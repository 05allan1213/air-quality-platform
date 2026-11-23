"""S06 collection control on real sessions and PostgreSQL."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from queue import Queue
from secrets import token_urlsafe
from threading import Event
from time import monotonic, sleep
from uuid import uuid4

import pytest
import test_s06_accounts
from accounts.models import GovernanceAction
from collection import services
from collection.models import ControlEvent, Job, Run
from django.db import connection, transaction
from observations.disposition import dispose_due
from observations.models import Batch, CityIdentifier, QualityIssue, Source
from observations.policy import clock, revoke_grant
from psycopg.types.range import TimestamptzRange
from s02_support import BOUNDARY, START
from s06_witness import witness
from test_s06_accounts import (
    denied,
    json_status,
    permission,
    sign_in,
)

from tests.collection.test_collection_postgres import LIMITS, grant, make_job, request

pytestmark = pytest.mark.django_db(transaction=True)

fixtures = (test_s06_accounts.sql_hook, test_s06_accounts.web_clients)
sql_hook, web_clients = fixtures
JOBS = "/api/v1/admin/collection-jobs/"
RUNS = "/api/v1/admin/collection-runs/"


@pytest.fixture
def operators(django_user_model):
    password = token_urlsafe(24)
    operator = django_user_model.objects.create_user("s06-collection-operator", password=password)
    operator.user_permissions.add(
        *[
            permission(f"observations.{code}")
            for code in (
                "run_collection",
                "manage_collection",
                "review_observation",
                "manage_grants",
            )
        ]
    )
    return operator, password


@pytest.fixture
def collection_job(operators, settings):
    settings.S03_FIXTURE_HTTP = True
    operator, _ = operators
    source = Source.objects.create(namespace="s03.fixture", publisher="F", collection_limits=LIMITS)
    job = make_job(operator, source)
    CityIdentifier.objects.filter(city=job.city).update(active_period=TimestamptzRange(START, None))
    for operation in ("acquire_online", "retain_normalized", "process_local", "review", "display"):
        grant(operator, job.product, operation)
    return job


def payload(action, version):
    return dict(
        action=action,
        request_id=str(uuid4()),
        reason_code="incident_review",
        expected_version=version,
    )


def blocked_sql(sql_hook, name, table):
    pid = Queue()

    def hook(execute, sql, params, many, context):
        if f'FROM "{table}"' in sql and "FOR UPDATE" in sql and pid.empty():
            pid.put(connection.connection.pgconn.backend_pid)
        return execute(sql, params, many, context)

    sql_hook[name] = hook
    return pid


def assert_blocked(waiter, holder):
    deadline = monotonic() + 0.8
    with connection.cursor() as cursor:
        while monotonic() < deadline:
            cursor.execute("SELECT %s = ANY(pg_blocking_pids(%s))", (holder, waiter))
            if cursor.fetchone()[0]:
                return
            sleep(0.02)
    raise AssertionError("Shared target lock was not held by the winning HTTP session")


def target(web, job, kind):
    return json_status(web.get(f"{JOBS}{job.pk}/"), 200)["targets"][kind]


@pytest.mark.parametrize("kind", ["source", "product"])
def test_v04_v17_cross_job_control_and_same_reason_worker_pause(
    kind, collection_job, operators, web_clients, sql_hook, monkeypatch, record_property
):
    operator, password = operators
    a = sign_in(web_clients(), operator, password)
    b = sign_in(web_clients(), operator, password)
    assert a.cookies["sessionid"] != b.cookies["sessionid"]
    job = collection_job
    second = Job.objects.create(
        product=job.product,
        city=job.city,
        executor=operator,
        profile=job.profile,
        expected_codes=job.expected_codes,
        roster_basis=job.roster_basis,
        enabled=True,
    )
    monkeypatch.setattr(services, "publish", lambda _: None)
    now = BOUNDARY

    def failed_run(at):
        run = services.submit(operator.pk, request(job, target=at))
        owner = services.claim(run.pk)
        assert owner
        services.record_failure(run.pk, owner, "unexpected_redirect", action=kind)
        return run

    first_run = failed_run(now)
    observed = target(a, job, kind)
    assert observed == target(b, second, kind)
    assert observed["state"] == "paused" and observed["control_version"] == 1
    assert ControlEvent.objects.get(run=first_run).receipt.before_version == 0
    action = f"resume_{kind}"
    route_a, route_b = f"{JOBS}{job.pk}/", f"{JOBS}{second.pk}/"
    denied(a.post(route_a, json={"action": action}), "invalid_request", 400)
    command = payload(action, observed["control_version"])
    competitor = payload(action, observed["control_version"])
    b.headers["X-S06-Test-Hook"] = "shared-resume"
    waiting = blocked_sql(sql_hook, "shared-resume", "observations_source")
    acquired, release = Queue(), Event()
    a.headers["X-S06-Test-Hook"] = "first-resume"

    def holds_source(execute, sql, params, many, context):
        result = execute(sql, params, many, context)
        if 'FROM "observations_source"' in sql and "FOR UPDATE" in sql:
            acquired.put(connection.connection.pgconn.backend_pid)
            assert release.wait(timeout=3)
        return result

    sql_hook["first-resume"] = holds_source
    with ThreadPoolExecutor(max_workers=2) as pool:
        winning = pool.submit(a.post, route_a, json=command)
        try:
            pg_holder = acquired.get(timeout=3)
            losing = pool.submit(b.post, route_b, json=competitor)
            assert_blocked(waiting.get(timeout=3), pg_holder)
            wait_at = clock()
        finally:
            release.set()
        resumed, conflict = winning.result(timeout=5), losing.result(timeout=5)
        result = json_status(resumed, 200)
        denied(conflict, "precondition_conflict", 409)
    replay = a.post(route_a, json=command)
    assert json_status(replay, 200) == result
    assert result == {"job": job.pk, "enabled": True, "reason": ""}
    assert GovernanceAction.objects.filter(request_id=command["request_id"]).count() == 1
    denied(
        a.post(route_a, json=command | {"reason_code": "operator_resume"}), "request_conflict", 409
    )
    failed_run(now + timedelta(hours=1))
    again = target(a, job, kind)
    assert (again["state"], again["reason"], again["control_version"]) == (
        "paused",
        "unexpected_redirect",
        3,
    )
    stale = a.post(route_a, json=payload(action, 1))
    denied(stale, "precondition_conflict", 409)
    audits = list(
        GovernanceAction.objects.filter(target_kind=kind)
        .order_by("created_at")
        .values_list("before_version", "after_version")
    )
    assert audits == [(0, 1), (1, 2), (2, 3)]
    witness(
        record_property,
        pg_at=wait_at,
        condition="C03",
        order="first_resume",
        lock_target="source",
        control_target=kind,
        pg_lock_wait=True,
        control_version_before=observed["control_version"],
        control_version_after=again["control_version"],
        http=resumed.status_code,
        peer_http=conflict.status_code,
        replay_http=replay.status_code,
        stale_http=stale.status_code,
        success_audit_count=len(audits),
    )


def test_v04_v17_legacy_and_idempotent_redelivery(
    collection_job, operators, web_clients, monkeypatch
):
    operator, password = operators
    a = sign_in(web_clients(), operator, password)
    b = sign_in(web_clients(), operator, password)
    job = collection_job
    route = f"{JOBS}{job.pk}/"
    assert json_status(a.post(route, json={"action": "pause"}), 200)["reason"] == "operator_pause"
    assert json_status(a.post(route, json={"action": "pause"}), 200)["enabled"] is False
    legacy = ControlEvent.objects.get(job=job)
    assert (
        legacy.legacy_protocol
        and legacy.receipt.request_id is None
        and legacy.receipt.reason_code == ""
    )
    for action in ("resume", "resume_source", "resume_product"):
        denied(a.post(route, json={"action": action}), "invalid_request", 400)
    version = target(a, job, "job")["control_version"]
    denied(
        a.post(route, json=payload("resume", version) | {"expected_version": True}),
        "invalid_request",
        400,
    )
    assert json_status(a.post(route, json=payload("resume", version)), 200)["enabled"]
    calls = []
    monkeypatch.setattr(services, "publish", lambda run_id: calls.append(run_id))
    run = services.submit(operator.pk, request(job, target=BOUNDARY))
    Run.objects.filter(pk=run.pk).update(dispatch="manual", dispatch_due=None)
    path = f"{RUNS}{run.pk}/"
    denied(a.post(path, json={"action": "redeliver"}), "invalid_request", 400)
    command = payload("redeliver", json_status(a.get(path), 200)["target"]["control_version"])
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(client.post, path, json=command) for client in (a, b)]
        responses = [json_status(result.result(timeout=5), 202) for result in futures]
    assert responses == [dict(run_id=str(run.pk), window_id=run.window_id)] * 2
    assert Run.objects.filter(window=run.window).count() == 1 and calls == [run.pk, run.pk]
    count = GovernanceAction.objects.count()
    with transaction.atomic():
        type(operator).objects.select_for_update().get(pk=operator.pk)
        operator.user_permissions.remove(permission("observations.manage_collection"))
    denied(a.post(path, json=command), "permission_denied")
    assert GovernanceAction.objects.count() == count and calls == [run.pk, run.pk]


def test_v05_v16_pre_manifest_failure_ledger(collection_job, operators, web_clients, monkeypatch):
    operator, password = operators
    web = sign_in(web_clients(), operator, password)
    job = collection_job
    monkeypatch.setattr(services, "publish", lambda _: None)
    run = services.submit(operator.pk, request(job, target=BOUNDARY))
    owner = services.claim(run.pk)
    assert owner
    services.reserve(run.pk, owner)
    services.record_failure(run.pk, owner, "invalid_shape", action="product", responded=True)
    assert not Batch.objects.exists() and not run.items.exists()
    event = QualityIssue.objects.get(run=run)
    assert event.batch_id is None and event.run_item_id is None and event.rule_version == 1
    trace = json_status(web.get(f"{RUNS}{run.pk}/"), 200)
    assert trace["window_id"] == run.window_id
    assert trace["runs"][0]["error_code"] == "invalid_shape"
    assert trace["quality_issues"][0]["id"] == event.pk
    assert trace["quality_issues"][0]["run"] == str(run.pk)
    assert trace["receipts"] == [] and trace["items"] == []
    grant = job.product.usegrant_set.get(operation="retain_normalized")
    revoke_grant(operator.pk, grant.pk, "F retention withdrawal")
    denied(web.get(f"{RUNS}{run.pk}/"), "use_denied")
    dispose_due(job.product_id)
    assert not QualityIssue.objects.filter(pk=event.pk).exists()


def test_v17_manual_submission_checks_session_after_job_wait(
    collection_job, operators, web_clients, sql_hook, monkeypatch, django_user_model
):
    op, password = operators
    admin = django_user_model.objects.create_superuser("s06-collection-admin", password=password)
    management = sign_in(web_clients(), admin, password)
    client = sign_in(web_clients(), op, password)
    route = test_s06_accounts.destination(op)
    calls = []
    monkeypatch.setattr(services, "publish", calls.append)
    client.headers["X-S06-Test-Hook"] = "manual-submit"
    waiting = blocked_sql(sql_hook, "manual-submit", "observations_source")
    command = request(collection_job)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic():
            Source.objects.select_for_update().get(pk=collection_job.product.source_id)
            pending = pool.submit(client.post, RUNS, json=command)
            assert_blocked(waiting.get(timeout=3), connection.connection.pgconn.backend_pid)
            for version, action in enumerate(("disable", "enable")):
                json_status(
                    management.post(route, json=test_s06_accounts.request_for(action, version)), 200
                )
        denied(pending.result(timeout=5), "not_authenticated")
    assert not Run.objects.exists() and calls == []
    fresh = sign_in(web_clients(), op, password)
    result = json_status(fresh.post(RUNS, json=command), 202)
    assert [str(pk) for pk in calls] == [result["run_id"]]


@pytest.mark.parametrize(
    "action", ["pause", "resume", "resume_source", "resume_product", "redeliver"]
)
def test_v04_five_command_contracts(action, collection_job, operators, web_clients, monkeypatch):
    op, password = operators
    web = sign_in(web_clients(), op, password)
    job = collection_job
    path = f"{JOBS}{job.pk}/"
    calls = []
    monkeypatch.setattr(services, "publish", calls.append)
    # Prepare each kind of target independently, then observe its real version via HTTP.
    if action in ("resume_source", "resume_product"):
        failed = services.submit(op.pk, request(job))
        services.record_failure(
            failed.pk,
            services.claim(failed.pk),
            "unexpected_redirect",
            action=action.removeprefix("resume_"),
        )
    elif action == "resume":
        json_status(web.post(path, json={"action": "pause"}), 200)
    if action == "redeliver":
        run = services.submit(op.pk, request(job))
        Run.objects.filter(pk=run.pk).update(dispatch="manual", dispatch_due=None)
        path = f"{RUNS}{run.pk}/"
        current = json_status(web.get(path), 200)["target"]["control_version"]
    else:
        kind = action.removeprefix("resume_") if action.startswith("resume_") else "job"
        current = target(web, job, kind)["control_version"]
    command = payload(action, current)
    before = GovernanceAction.objects.count()
    before_calls = len(calls)
    for missing in ("request_id", "reason_code", "expected_version"):
        denied(
            web.post(path, json={k: v for k, v in command.items() if k != missing}),
            "invalid_request",
            400,
        )
    if action != "pause":
        denied(web.post(path, json={"action": action}), "invalid_request", 400)
    denied(web.post(path, json=payload(action, current + 1)), "precondition_conflict", 409)
    assert GovernanceAction.objects.count() == before and len(calls) == before_calls
    status = 202 if action == "redeliver" else 200
    result = json_status(web.post(path, json=command), status)
    assert json_status(web.post(path, json=command), status) == result
    denied(
        web.post(path, json=command | {"reason_code": "operator_recovery"}), "request_conflict", 409
    )
    denied(web.post(path, json=payload(action, current)), "precondition_conflict", 409)
    with transaction.atomic():
        type(op).objects.select_for_update().get(pk=op.pk)
        op.user_permissions.remove(permission("observations.manage_collection"))
    denied(web.post(path, json=command), "permission_denied")
    assert GovernanceAction.objects.count() == before + 1
    assert len(calls) == before_calls + (action == "redeliver")
