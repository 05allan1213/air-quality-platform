"""Self receipts through the mounted route, real database Session and CSRF (F/I)."""

from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from accounts.assigned_permissions import CONFIGURE_SCHEDULE as CONFIGURE
from accounts.models import GovernanceAction
from collection import services
from collection.models import ControlEvent, Job, Run, WorkWindow
from collection.s08_schedule import register_template
from django.core import signing
from django.test import Client
from observations.models import (
    CityIdentifier,
    CityObservation,
    QualityIssue,
    Source,
    StationIdentifier,
)
from observations.policy import RAW, REQUIRED, clock, revoke_grant
from psycopg.types.range import TimestamptzRange
from s05_support import HOUR, candidate, city, ingest, product, site
from s05_support import grant as data_grant
from s07_contract import dereference, operation, validate_schema
from test_s06_accounts import permission

from tests.collection.test_collection_postgres import BOUNDARY, LIMITS, START, grant, make_job
from tests.collection.test_collection_postgres import request as run_command

pytestmark = pytest.mark.django_db(transaction=True)
RECEIPTS = "/api/v1/admin/command-receipts/lookup/"
RUNS = "/api/v1/admin/collection-runs/"
JOBS = "/api/v1/admin/collection-jobs/"
RUN_PERMISSION = "observations.run_collection"
PASSWORD = "disposable receipt fixture password"


def check(response, status):
    assert response.status_code == status, response.content
    return response.json()


def csrf(client):
    return check(client.get("/api/v1/auth/csrf/"), 200)["csrf_token"]


def post(client, path, payload, token):
    return client.post(path, payload, content_type="application/json", HTTP_X_CSRFTOKEN=token)


def sign_in(user):
    client = Client(enforce_csrf_checks=True, HTTP_HOST="localhost")
    check(
        post(
            client,
            "/api/v1/auth/login/",
            {"username": user.username, "password": PASSWORD},
            csrf(client),
        ),
        200,
    )
    return client, csrf(client)


def lookup(web, family, target, command, status=200):
    client, token = web
    response = post(
        client, RECEIPTS, {"family": family, "target": target, "command": command}, token
    )
    assert "no-store" in response["Cache-Control"]
    result = check(response, status)
    contract = operation(RECEIPTS, "POST")
    validate_schema(
        result,
        dereference(contract["responses"][str(status)])["content"]["application/json"]["schema"],
    )
    if status == 200:
        validate_schema(
            {"family": family, "target": target, "command": command},
            contract["requestBody"]["content"]["application/json"]["schema"],
        )
    if status == 404:
        assert result == {"code": "not_found"}
    return result


def action(name, reason, version, **extra):
    return {
        "action": name,
        "reason_code": reason,
        "expected_version": version,
        "request_id": str(uuid4()),
        **extra,
    }


@pytest.fixture
def registered(django_user_model, settings):
    settings.CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": str(uuid4()),
        }
    }
    settings.S03_FIXTURE_HTTP = True
    settings.S08_FIXTURE_SCHEDULE = True

    def principal(name, *permissions):
        user = django_user_model.objects.create_user(name, password=PASSWORD)
        user.user_permissions.add(*(permission(name) for name in permissions))
        return user

    manager = principal(
        "receipt-manager",
        RUN_PERMISSION,
        "observations.manage_collection",
        "observations.manage_grants",
    )
    source = Source.objects.create(namespace="s03.fixture", publisher="F", collection_limits=LIMITS)
    job = make_job(manager, source)
    CityIdentifier.objects.filter(city=job.city).update(active_period=TimestamptzRange(START, None))
    grants = {
        operation: grant(manager, job.product, operation)
        for operation in ("acquire_online", "process_local", "retain_normalized", "review")
    }
    return manager, job, grants, principal


@pytest.mark.parametrize(("dispatch", "status"), [("sent", 202), ("unconfirmed", 503)])
def test_execute_only_discovers_own_committed_submission_without_replaying(
    registered, monkeypatch, settings, dispatch, status
):
    manager, job, _, principal = registered
    first_user = principal("receipt-executor-a", RUN_PERMISSION)
    second_user = principal("receipt-executor-b", RUN_PERMISSION)
    first, second = sign_in(first_user), sign_in(second_user)
    management = sign_in(manager)
    deliveries = []

    def attempt(run_id):
        deliveries.append(str(run_id))
        Run.objects.filter(pk=run_id).update(dispatch=dispatch)

    monkeypatch.setattr(services, "publish", attempt)
    original = run_command(job)
    accepted = check(post(first[0], RUNS, original, first[1]), status)
    lookup(second, "manual_run", job.pk, original, 404)
    peer_accepted = check(post(second[0], RUNS, original, second[1]), status)
    assert accepted["run_id"] != peer_accepted["run_id"]
    assert accepted["window_id"] == peer_accepted["window_id"]
    assert lookup(second, "manual_run", job.pk, original)["run_id"] == peer_accepted["run_id"]
    assert first[0].get(f"{RUNS}{accepted['run_id']}/").status_code == 403

    # Pause after submission and close the fixture execution gate: neither should
    # force a fetch, grant ledger review, or prevent disclosure of a valid receipt.
    pause = action("pause", "operator_pause", job.control_version)
    check(post(management[0], f"{JOBS}{job.pk}/", pause, management[1]), 200)
    settings.S03_FIXTURE_HTTP = False
    before = (Run.objects.count(), WorkWindow.objects.count(), ControlEvent.objects.count())
    own = lookup(first, "manual_run", job.pk, original)
    assert own == {
        "state": "submitted",
        "action": "manual",
        "recorded_at": Run.objects.get(pk=accepted["run_id"]).created_at.isoformat(),
        **accepted,
        "status": "pending",
    }
    for wrong in (
        {"reason": "review"},
        {"target": (BOUNDARY + timedelta(hours=1)).isoformat()},
        {"job": job.pk + 1},
        {"unexpected": True},
    ):
        lookup(first, "manual_run", job.pk, original | wrong, 404)
    lookup(first, "manual_run", job.pk + 1, original, 404)
    assert check(
        post(
            first[0], RECEIPTS, {"family": "manual_run", "target": job.pk, "command": original}, ""
        ),
        403,
    ) == {"code": "csrf_failed"}
    first_user.user_permissions.remove(permission(RUN_PERMISSION))
    lookup(first, "manual_run", job.pk, original, 404)
    assert before == (Run.objects.count(), WorkWindow.objects.count(), ControlEvent.objects.count())
    assert deliveries == [accepted["run_id"], peer_accepted["run_id"]]


def test_control_and_redelivery_receipts_keep_the_committed_identity(registered, monkeypatch):
    manager, job, _, principal = registered
    web = sign_in(manager)
    peer = sign_in(principal("receipt-peer-manager", "observations.manage_collection"))
    deliveries = []

    def manual(run_id):
        deliveries.append(str(run_id))
        Run.objects.filter(pk=run_id).update(dispatch="manual")

    monkeypatch.setattr(services, "publish", manual)
    accepted = check(post(web[0], RUNS, run_command(job), web[1]), 202)
    run = Run.objects.get(pk=accepted["run_id"])
    redelivery = action("redeliver", "operator_recovery", run.control_version)
    check(post(web[0], f"{RUNS}{run.pk}/", redelivery, web[1]), 202)
    recorded = lookup(web, "redeliver", str(run.pk), redelivery)
    assert recorded["state"] == "pending" and recorded["dispatch"] == "manual"
    assert recorded["window_id"] == run.window_id and "result" not in recorded
    lookup(peer, "redeliver", str(run.pk), redelivery, 404)

    pause = action("pause", "operator_pause", job.control_version)
    path = f"{JOBS}{job.pk}/"
    check(post(web[0], path, pause, web[1]), 200)
    job.refresh_from_db()
    check(post(web[0], path, action("resume", "operator_resume", job.control_version), web[1]), 200)
    before = (Run.objects.count(), GovernanceAction.objects.count(), ControlEvent.objects.count())
    old = lookup(web, "job_control", job.pk, pause)
    job.refresh_from_db()
    assert old["state"] == "paused" and old["version"] < job.control_version
    lookup(web, "job_control", job.pk, pause | {"expected_version": job.control_version}, 404)
    lookup(web, "job_control", job.pk, pause | {"reason_code": "incident_review"}, 404)
    lookup(peer, "job_control", job.pk, pause, 404)
    # Inject one receipt from the previous reason-code vocabulary. The owner can
    # still verify its exact original envelope, including the retired reason.
    GovernanceAction.objects.filter(pk=old["action_id"]).update(reason_code="incident_review")
    historical = pause | {"reason_code": "incident_review"}
    assert lookup(web, "job_control", job.pk, historical)["action_id"] == old["action_id"]
    assert before == (
        Run.objects.count(),
        GovernanceAction.objects.count(),
        ControlEvent.objects.count(),
    )
    assert deliveries == [str(run.pk), str(run.pk)]


def test_schedule_lookup_uses_template_and_original_version_not_new_job_id(registered):
    manager, basis, _, principal = registered
    editor = principal("receipt-editor", CONFIGURE)
    editor.is_superuser = True
    editor.save(update_fields=["is_superuser"])
    web = sign_in(editor)
    template = register_template(manager.pk, basis.pk, [3600, 7200], 0)
    next_due = clock().replace(minute=0, second=0, microsecond=0) + timedelta(hours=2)
    create = action(
        "create_draft",
        "schedule_create",
        template.version,
        template=template.pk,
        cadence_seconds=3600,
        first_due_at=next_due.isoformat(),
    )
    original = check(post(web[0], JOBS, create, web[1]), 201)
    discard = action(
        "discard",
        "schedule_discard",
        original["version"],
        template_version=original["template_version"],
        expected_predecessor_version=None,
    )
    check(post(web[0], f"{JOBS}{original['job']}/schedule/", discard, web[1]), 200)
    before = (Job.objects.count(), GovernanceAction.objects.count(), ControlEvent.objects.count())
    receipt = lookup(web, "schedule", template.pk, create)
    assert {k: receipt[k] for k in ("action_id", "state", "job", "version")} == {
        k: original[k] for k in ("action_id", "state", "job", "version")
    }
    assert lookup(web, "schedule", original["job"], discard)["state"] == "discarded"
    lookup(web, "schedule", template.pk, create | {"cadence_seconds": 7200}, 404)
    editor.user_permissions.clear()
    lookup(web, "schedule", template.pk, create, 404)
    assert before == (
        Job.objects.count(),
        GovernanceAction.objects.count(),
        ControlEvent.objects.count(),
    )
    assert not Run.objects.exists()


@pytest.mark.parametrize("operation", ["acquire_online", "process_local"])
def test_schedule_lookup_mirrors_command_replay_after_execution_use_withdrawn(
    registered, operation
):
    manager, basis, grants, principal = registered
    web = sign_in(principal("receipt-schedule-editor", CONFIGURE))
    template = register_template(manager.pk, basis.pk, [3600], 0)
    due = clock().replace(minute=0, second=0, microsecond=0) + timedelta(hours=2)
    create = action(
        "create_draft",
        "schedule_create",
        template.version,
        template=template.pk,
        cadence_seconds=3600,
        first_due_at=due.isoformat(),
    )
    draft = check(post(web[0], JOBS, create, web[1]), 201)
    job = draft["job"]
    path = f"{JOBS}{job}/schedule/"
    discard = action(
        "discard",
        "schedule_discard",
        draft["version"],
        template_version=draft["template_version"],
        expected_predecessor_version=None,
    )
    discarded = check(post(web[0], path, discard, web[1]), 200)
    assert lookup(web, "schedule", template.pk, create)["action_id"] == draft["action_id"]
    discard_receipt = lookup(web, "schedule", job, discard)
    assert discard_receipt["action_id"] == discarded["action_id"]

    revoke_grant(manager.pk, grants[operation].pk, "fixture execution use withdrawn")
    before = (Job.objects.count(), GovernanceAction.objects.count(), ControlEvent.objects.count())
    assert check(post(web[0], JOBS, create, web[1]), 403) == {"code": "use_denied"}
    lookup(web, "schedule", template.pk, create, 404)
    assert check(post(web[0], path, discard, web[1]), 200) == discarded
    assert lookup(web, "schedule", job, discard) == discard_receipt
    assert before == (
        Job.objects.count(),
        GovernanceAction.objects.count(),
        ControlEvent.objects.count(),
    )
    assert not Run.objects.exists()


def test_grant_withdrawal_checks_recorded_grant_and_current_manager(registered):
    manager, job, grants, principal = registered
    web = sign_in(manager)
    peer = sign_in(principal("receipt-peer-grant-manager", "observations.manage_grants"))
    subject = grants["review"]
    job.product.refresh_from_db()
    withdraw = action("withdraw", "terms_ended", job.product.rights_version)
    original = check(
        post(web[0], f"/api/v1/admin/grants/{subject.pk}/actions/", withdraw, web[1]), 200
    )
    grant(manager, job.product, "display")
    receipt = lookup(web, "grant_withdraw", subject.pk, withdraw)
    assert {k: receipt[k] for k in ("action_id", "state", "version")} == original
    lookup(peer, "grant_withdraw", subject.pk, withdraw, 404)
    lookup(web, "grant_withdraw", grants["process_local"].pk, withdraw, 404)
    manager.user_permissions.remove(permission("observations.manage_grants"))
    lookup(web, "grant_withdraw", subject.pk, withdraw, 404)


def test_quality_correction_rechecks_both_scopes_of_signed_original(registered, monkeypatch):
    _, _, _, principal = registered
    reviewer = principal(
        "receipt-quality-reviewer",
        "observations.manage_grants",
        "observations.review_observation",
        "observations.import_history",
        "accounts.adjudicate_quality",
    )
    web = sign_in(reviewer)
    subject = product("s08-receipt-quality", "C")
    old = city(subject, "101", geography="F-old-city")
    new = city(subject, "102", geography="F-new-city")
    data_grant(reviewer, subject, "process_local")
    data_grant(reviewer, subject, "retain_normalized", fields=RAW)
    data_grant(reviewer, subject, "review", fields=RAW, geography=old.geography)
    target_review = data_grant(reviewer, subject, "review", fields=RAW, geography=new.geography)
    ingest(reviewer, subject, [candidate(subject, "101", value="100", token=7)])
    issue = QualityIssue.objects.create(
        product=subject,
        city_key=CityObservation.objects.get(product=subject, city=old),
        code="mapping_review_required",
    )
    path = f"/api/v1/admin/quality-issues/{issue.pk}/"
    proof = check(web[0].get(path, {"mapping_target": "102"}), 200)
    correct = action(
        "correct_mapping",
        "registered_mapping",
        proof["event_version"],
        evidence_ref=proof["evidence"]["correct_mapping"],
    )
    with monkeypatch.context() as patch:
        later = signing.time.time() + 2
        patch.setattr(signing, "time", SimpleNamespace(time=lambda: later))
        refreshed = check(web[0].get(path, {"mapping_target": "102"}), 200)["evidence"][
            "correct_mapping"
        ]
    assert refreshed != correct["evidence_ref"]
    original = check(post(web[0], f"{path}actions/", correct, web[1]), 200)
    lookup(web, "quality", issue.pk, correct | {"evidence_ref": refreshed}, 404)
    assert check(
        post(web[0], f"{path}actions/", correct | {"evidence_ref": refreshed}, web[1]), 409
    ) == {"code": "request_conflict"}
    receipt = lookup(web, "quality", issue.pk, correct)
    assert (receipt["action_id"], receipt["state"], receipt["version"]) == (
        original["action_id"],
        original["status"],
        original["event_version"],
    )
    assert receipt["impact"] == {
        "state": "available",
        "outcome": "local_mapping_fix",
        "affected": original["affected"],
    }
    with monkeypatch.context() as patch:
        later = signing.time.time() + 1801
        patch.setattr(signing, "time", SimpleNamespace(time=lambda: later))
        assert lookup(web, "quality", issue.pk, correct) == receipt
        assert check(post(web[0], f"{path}actions/", correct, web[1]), 403) == {
            "code": "mapping_evidence_mismatch"
        }
    # The original signed scope remains readable. The target revision now joins a
    # geography outside the current review grant and cannot be safely projected.
    new.geography = "F-restricted-geography"
    new.save(update_fields=["geography"])
    partial = lookup(web, "quality", issue.pk, correct)["impact"]
    assert partial == {"state": "available", "affected": [original["affected"][0]]}
    lookup(web, "quality", issue.pk, correct | {"evidence_ref": "different-ref"}, 404)
    revoke_grant(reviewer.pk, target_review.pk, "fixture destination review withdrawn")
    lookup(web, "quality", issue.pk, correct, 404)


def test_station_change_requires_both_directory_scopes(registered):
    _, _, _, principal = registered
    manager = principal(
        "receipt-station-manager",
        "observations.manage_grants",
        "observations.review_observation",
        "accounts.manage_catalog",
    )
    web = sign_in(manager)
    subject = product("s08-receipt-station", "S")
    old = site(subject, "101")
    new = city(subject, "102", geography="F-destination")
    ident = StationIdentifier.objects.get(station=old.station)
    data_grant(manager, subject, "retain_normalized", fields=REQUIRED | {"catalog"})
    data_grant(manager, subject, "review", fields={"catalog"}, geography=old.city.geography)
    target_review = data_grant(
        manager, subject, "review", fields={"catalog"}, geography=new.geography
    )
    path = f"/api/v1/admin/stations/{old.station_id}/changes/"
    proof = check(
        web[0].get(
            path,
            {
                "product": subject.pk,
                "action": "relocate",
                "identifier": ident.pk,
                "at": (START + HOUR).isoformat(),
                "target": new.pk,
            },
        ),
        200,
    )
    change = action(
        "relocate",
        "documented_relocation",
        proof["version"],
        evidence_ref=proof["evidence_ref"],
    )
    original = check(post(web[0], path, change, web[1]), 200)
    receipt = lookup(web, "station_change", old.station_id, change)
    assert (receipt["action_id"], receipt["state"], receipt["version"]) == (
        original["action_id"],
        "applied",
        original["version"],
    )
    revoke_grant(manager.pk, target_review.pk, "fixture destination review withdrawn")
    lookup(web, "station_change", old.station_id, change, 404)
