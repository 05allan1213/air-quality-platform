"""S08 single-object audit over live loopback HTTP, sessions and PostgreSQL (F/I)."""

from datetime import timedelta
from uuid import uuid4

import pytest
from accounts.governance import receipt
from accounts.limits import LIMITS
from collection import control, services
from collection.models import Job, Run
from django.contrib.sessions.models import Session
from observations.models import CityObservation, QualityIssue, StationIdentifier
from observations.policy import RAW, REQUIRED, clock, revoke_grant
from s02_support import BOUNDARY
from s05_support import HOUR, START, candidate, city, grant, ingest, product, site
from test_s06_accounts import permission, sign_in
from test_s06_accounts import sql_hook as sql_hook
from test_s06_accounts import web_clients as web_clients
from test_s06_catalog import edit, preview
from test_s06_collection import collection_job as collection_job
from test_s06_collection import operators as operators
from test_s06_collection import payload
from test_s06_quality import action as quality_action
from test_s06_quality import ask, command
from test_s08_schedule import JOBS, decide, draft, propose
from test_s08_schedule import registered as registered

from tests.collection.test_collection_postgres import grant as grant_collection
from tests.collection.test_collection_postgres import request

pytestmark = pytest.mark.django_db(transaction=True)

URL = "/api/v1/admin/object-audit/"
PASSWORD = "s08 object audit disposable password"
FIELDS = {"at", "action", "outcome", "actor_relation", "before_version", "after_version"}


@pytest.fixture
def audit_client(django_user_model, web_clients):
    user = django_user_model.objects.create_user("s08-audit-operator", password=PASSWORD)
    user.user_permissions.add(
        permission("observations.manage_collection"),
        permission("observations.review_observation"),
        permission("observations.manage_grants"),
        permission("accounts.manage_catalog"),
        permission("accounts.adjudicate_quality"),
    )
    return user, sign_in(web_clients(), user, PASSWORD)


def audit(web, kind, target, **extra):
    return web.get(
        URL,
        params={
            "kind": kind,
            "id": str(target),
            "start": (clock() - timedelta(days=1)).isoformat(),
            "end": (clock() + timedelta(days=1)).isoformat(),
            **extra,
        },
    )


def check(response, status=200, code=None):
    assert response.status_code == status, response.content
    assert "no-store" in response.headers["Cache-Control"]
    data = response.json()
    if code:
        assert data == {"code": code}
    else:
        assert set(data) == {"results", "page"}
        assert all(set(row) == FIELDS for row in data["results"])
    return data


def test_job_receipts_cursor_and_origin(collection_job, operators, audit_client, web_clients):
    job = collection_job
    operator, web = audit_client
    peer, password = operators
    other = sign_in(web_clients(), peer, password)
    path = f"/api/v1/admin/collection-jobs/{job.pk}/"
    assert check(audit(web, "job", job.pk))["results"] == []
    assert web.post(path, json=payload("pause", job.control_version)).status_code == 200
    job.refresh_from_db()
    assert other.post(path, json=payload("resume", job.control_version)).status_code == 200
    job.refresh_from_db()
    assert web.post(path, json=payload("pause", job.control_version)).status_code == 200

    # A sibling product without ledger review denies a source-wide record, even
    # though it has a genuine origin and is interleaved above the visible results.
    product("audit-unreviewable-source-sibling", "S", source=job.product.source)
    source = job.product.source
    control.automatic(job, "source", source, "active", "paused", 0, 1, "test_pause")
    another = Job.objects.create(
        product=job.product, city=job.city, executor=operator, profile=job.profile
    )
    control.automatic(another, "job", another, "active", "paused", 0, 1, "test_pause")

    args = {
        "start": (clock() - HOUR).isoformat(),
        "end": (clock() + HOUR).isoformat(),
        "page_size": 2,
    }
    first = check(audit(web, "job", job.pk, **args))
    assert [(row["action"], row["actor_relation"]) for row in first["results"]] == [
        ("pause", "self"),
        ("resume", "other"),
    ]
    cursor = first["page"]["next_cursor"]
    assert cursor
    # A newly committed head must not move the old keyset boundary.
    job.refresh_from_db()
    assert web.post(path, json=payload("resume", job.control_version)).status_code == 200
    tail = check(audit(web, "job", job.pk, cursor=cursor, **args))
    assert [row["action"] for row in tail["results"]] == ["pause"]
    assert tail["page"]["next_cursor"] is None
    second_session = sign_in(web_clients(), operator, PASSWORD)
    for client, target in ((other, job), (second_session, job), (web, another)):
        check(audit(client, "job", target.pk, cursor=cursor, **args), 400, "invalid_cursor")

    check(audit(web, "job", 2**63 - 1), 403, "use_denied")
    Job.objects.filter(pk=another.pk).update(disposed=True, enabled=False)
    check(audit(web, "job", another.pk), 403, "use_denied")
    operator.user_permissions.remove(permission("observations.manage_collection"))
    check(audit(web, "job", job.pk, cursor=cursor, **args), 403, "permission_denied")


def test_schedule_receipts_keep_the_original_template_and_predecessor(registered):
    host, basis, template, [(editor, web), (_, peer)], first, _ = registered
    editor.is_superuser = True
    editor.save(update_fields=["is_superuser"])
    plan_response = web.post(JOBS, json=draft(web, template, first))
    assert plan_response.status_code == 201, plan_response.content
    plan = decide(web, plan_response.json())
    child = propose(peer, plan, first + 4 * HOUR)
    assert [row["action"] for row in check(audit(web, "job", basis.pk))["results"]] == []
    assert [row["action"] for row in check(audit(web, "job", plan["job"]))["results"]] == [
        "activate",
        "create_draft",
    ]
    assert [
        (row["action"], row["actor_relation"])
        for row in check(audit(web, "job", child["job"]))["results"]
    ] == [("propose", "other")]
    unrelated = Job.objects.create(
        product=basis.product, city=basis.city, executor=host, profile=basis.profile
    )
    check(audit(web, "job", unrelated.pk), 403, "use_denied")
    editor.user_permissions.remove(permission("observations.configure_collection_schedule"))
    check(audit(web, "job", child["job"]), 403, "permission_denied")
    editor.user_permissions.add(permission("observations.manage_collection"))
    assert [row["action"] for row in check(audit(web, "job", child["job"]))["results"]] == [
        "propose"
    ]


def test_schedule_audit_excludes_activation_after_partial_takeover_review_loss(registered):
    host, basis, template, [(_, web), _], first, _ = registered
    response = web.post(JOBS, json=draft(web, template, first))
    assert response.status_code == 201, response.content
    plan = decide(web, response.json())
    args = {
        "page_size": 1,
        "start": (clock() - HOUR).isoformat(),
        "end": (clock() + HOUR).isoformat(),
    }
    head = check(audit(web, "job", plan["job"], **args))
    assert [row["action"] for row in head["results"]] == ["activate"]
    cursor = head["page"]["next_cursor"]
    assert cursor

    # The new restriction overlaps the original takeover range but leaves the
    # current review and the first scheduled slot covered.
    hidden = first.date() + timedelta(days=4)
    grant_collection(
        host,
        basis.product,
        "review",
        data_from=hidden,
        data_until=hidden + timedelta(days=1),
        effect="deny",
    )

    for page in (
        check(audit(web, "job", plan["job"], **args)),
        check(audit(web, "job", plan["job"], cursor=cursor, **args)),
    ):
        assert [row["action"] for row in page["results"]] == ["create_draft"]
        assert page["page"]["next_cursor"] is None


def test_run_requires_its_full_window_rights(collection_job, operators, audit_client, monkeypatch):
    job = collection_job
    peer, _ = operators
    _, web = audit_client
    monkeypatch.setattr(services, "publish", lambda run_id: None)
    run = services.submit(peer.pk, request(job))
    Run.objects.filter(pk=run.pk).update(dispatch="manual")
    run.refresh_from_db()
    response = web.post(
        f"/api/v1/admin/collection-runs/{run.pk}/", json=payload("redeliver", run.control_version)
    )
    assert response.status_code == 202, response.content
    result = check(audit(web, "run", run.pk))
    assert [row["action"] for row in result["results"]] == ["redeliver"]

    review = job.product.usegrant_set.get(operation="review")
    revoke_grant(peer.pk, review.pk, "audit test withdraw review")
    check(audit(web, "run", run.pk), 403, "use_denied")
    check(audit(web, "run", uuid4()), 403, "use_denied")


def test_quality_bound_actions_and_excludes_unprovable_correction(audit_client):
    operator, web = audit_client
    p = product("s08-audit-quality", "C")
    entity = city(p)
    review = grant(operator, p, "review")
    grant(operator, p, "retain_normalized")
    event = QualityIssue.objects.create(
        product=p,
        code="fixture_issue",
        scope={
            "geography": entity.geography,
            "object_code": "001",
            "indicator": "CO",
            "start": START.date().isoformat(),
            "end": (START + timedelta(days=1)).date().isoformat(),
        },
    )
    path = f"/api/v1/admin/quality-issues/{event.pk}/actions/"
    for version, (action, reason) in enumerate(
        (("claim", "claim_review"), ("close", "review_complete"))
    ):
        response = web.post(
            path,
            json={
                "action": action,
                "reason_code": reason,
                "request_id": str(uuid4()),
                "expected_version": version,
                "evidence_ref": None,
            },
        )
        assert response.status_code == 200, response.content
    receipt(
        operator.pk,
        {"action": "correct_mapping", "reason_code": "registered_mapping", "request_id": uuid4()},
        "quality",
        event.pk,
        "open",
        "resolved",
        0,
        1,
    )
    first = check(audit(web, "quality_issue", event.pk, page_size=2))
    assert [row["action"] for row in first["results"]] == ["close", "claim"]
    assert first["page"]["next_cursor"] is None
    revoke_grant(operator.pk, review.pk, "audit test withdraw review")
    check(audit(web, "quality_issue", event.pk), 403, "use_denied")


def test_quality_cross_key_correction_checks_both_objects(audit_client):
    operator, web = audit_client
    p = product("s08-audit-correction", "C")
    a = city(p, "101", geography="F-old-city")
    b = city(p, "102", geography="F-new-city")
    grant(operator, p, "process_local")
    grant(operator, p, "retain_normalized", fields=RAW)
    grant(operator, p, "review", fields=RAW, geography=a.geography)
    other_review = grant(operator, p, "review", fields=RAW, geography=b.geography)
    operator.user_permissions.add(permission("observations.import_history"))
    ingest(operator, p, [candidate(p, "101", value="100", token=7)])
    key = CityObservation.objects.get(product=p, city=a)
    event = QualityIssue.objects.create(product=p, city_key=key, code="mapping_review_required")
    proof = ask(web, event, target="102")
    result = quality_action(web, event, command(proof, "correct_mapping"))
    assert {row["city_id"] for row in result["affected"]} == {a.pk, b.pk}
    assert [row["action"] for row in check(audit(web, "quality_issue", event.pk))["results"]] == [
        "correct_mapping"
    ]
    revoke_grant(operator.pk, other_review.pk, "audit correction destination review withdrawn")
    assert check(audit(web, "quality_issue", event.pk))["results"] == []


def test_station_loses_linked_history_and_grant_audit_is_minimal(audit_client):
    operator, web = audit_client
    p = product("s08-audit-station")
    a = site(p, "101")
    destination = city(p, "102", geography="F-second-city")
    target = site(p, "103")
    ident = StationIdentifier.objects.get(station=a.station)
    grant(operator, p, "retain_normalized", fields=REQUIRED | {"catalog"})
    review = grant(operator, p, "review", fields={"catalog"})
    result = edit(
        web, ident, preview(web, p, ident, "rename", START + HOUR, "F local label"), "rename"
    )
    assert result["status"] == "applied"
    assert [row["action"] for row in check(audit(web, "station", a.station_id))["results"]] == [
        "rename"
    ]
    assert check(audit(web, "station", target.station_id))["results"] == []
    moved = edit(
        web, ident, preview(web, p, ident, "relocate", START + HOUR * 2, destination.pk), "relocate"
    )
    assert moved["status"] == "applied"
    assert [row["action"] for row in check(audit(web, "station", a.station_id))["results"]] == [
        "relocate",
        "rename",
    ]
    grant(operator, p, "review", fields={"catalog"}, effect="deny", geography=destination.geography)
    limited = check(audit(web, "station", a.station_id, page_size=1))
    assert [row["action"] for row in limited["results"]] == ["rename"]
    assert limited["page"]["next_cursor"] is None
    p.refresh_from_db()
    response = web.post(
        f"/api/v1/admin/grants/{review.pk}/actions/",
        json={
            "action": "withdraw",
            "reason_code": "rights_review",
            "expected_version": p.rights_version,
            "request_id": str(uuid4()),
        },
    )
    assert response.status_code == 200, response.content
    check(audit(web, "station", a.station_id), 403, "use_denied")
    assert [row["action"] for row in check(audit(web, "grant", review.pk))["results"]] == [
        "withdraw"
    ]


def test_malformed_and_stale_session(audit_client, web_clients):
    _, web = audit_client
    check(audit(web, "run", "not-a-uuid"), 400, "invalid_request")
    check(audit(web, "job", 1, page_size=51), 400, "invalid_request")
    check(
        audit(web, "job", 1, start=START.isoformat(), end=(START + timedelta(days=31)).isoformat()),
        400,
        "invalid_request",
    )
    check(web.get(URL + "?kind=job&kind=run"), 400, "invalid_request")
    check(web.get(URL + "?request_id=anything"), 400, "invalid_request")
    check(web.post(URL), 405, "method_not_allowed")
    check(audit(web_clients(), "station", 1), 403, "not_authenticated")
    Session.objects.filter(session_key=web.cookies["sessionid"]).update(expire_date=BOUNDARY)
    check(audit(web, "station", 1), 403, "not_authenticated")


def test_object_audit_shares_management_workbench_rate_limit(
    collection_job, audit_client, monkeypatch
):
    _, web = audit_client
    monkeypatch.setitem(LIMITS, "collection", 1)
    assert web.get(JOBS, params={"scope": "synthetic"}).status_code == 200
    check(audit(web, "job", collection_job.pk), 429, "rate_limited")
