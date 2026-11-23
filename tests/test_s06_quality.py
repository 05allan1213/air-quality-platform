"""S06 quality adjudication and research invalidation using live HTTP and PostgreSQL."""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from queue import Queue
from threading import Event
from uuid import uuid4

import pytest
from accounts import research_exports as exports
from accounts.models import ExportTicket, GovernanceAction, SavedQuery
from accounts.research_exports import export_fields
from django.db import OperationalError, transaction
from observations.models import (
    CandidateClearance,
    Change,
    CityObservation,
    QualityDecision,
    QualityIssue,
)
from observations.policy import RAW, REQUIRED, clock
from s02_support import observe_db
from s05_support import HOUR, START, authorize, candidate, city, grant, ingest, product
from s06_witness import witness
from test_s05_races import blocked_by, in_thread, pid
from test_s05_selection import DETAIL, parent
from test_s06_accounts import destination, permission, request_for, sign_in
from test_s06_accounts import (
    sql_hook as sql_hook,
)
from test_s06_accounts import (
    web_clients as web_clients,
)

pytestmark = pytest.mark.django_db(transaction=True)
QUALITY = "/api/v1/admin/quality-issues/"
STATISTICS = "/api/v1/analysis/statistics/"
SAVED = "/api/v1/me/saved-queries/"
EXPORTS = "/api/v1/me/exports/"
PASSWORD = "s06 disposable local F user"


def check(response, code):
    assert response.status_code == code, response.content
    assert "no-store" in response.headers["Cache-Control"]
    return response.json()


def export(web, query, version):
    result = check(
        web.post(
            EXPORTS,
            json={
                "kind": "detail",
                "groups": [query | {"input_version": version}],
            },
        ),
        201,
    )
    return EXPORTS + result["id"] + "/download/?scope=synthetic"


def reject_download(web, path, status=409, code="input_changed"):
    response = web.get(path)
    assert check(response, status) == {"code": code}
    assert "Content-Disposition" not in response.headers
    assert not response.headers["Content-Type"].startswith("text/csv")
    return response


def csv_bytes(response):
    media_type = response.headers["Content-Type"].split(";", 1)[0]
    return len(response.content) if media_type == "text/csv" else 0


def ask(web, event, *, target=None):
    return check(
        web.get(f"{QUALITY}{event.pk}/", params={"mapping_target": target} if target else {}), 200
    )


def action(web, event, payload, code=200):
    return check(web.post(f"{QUALITY}{event.pk}/actions/", json=payload), code)


def command(review, verb="correct_source", **extra):
    return {
        "action": verb,
        "reason_code": {
            "correct_source": "verified_correction",
            "correct_mapping": "registered_mapping",
            "close": "review_complete",
            "claim": "claim_review",
        }[verb],
        "expected_version": review["event_version"],
        "request_id": str(uuid4()),
        "evidence_ref": review["evidence"].get(verb),
    } | extra


@pytest.fixture
def world(django_user_model, web_clients):
    op = django_user_model.objects.create_superuser("s06-quality-op", password=PASSWORD)
    reader = django_user_model.objects.create_user("s06-reader", password=PASSWORD)
    admin = sign_in(web_clients(), op, PASSWORD)
    web = sign_in(web_clients(), reader, PASSWORD)
    p = product("quality", "C")
    a = city(p, "101")
    b = city(p, "102")
    authorize(op, p, detail_fields=export_fields("detail"))
    grant(op, p, "review", fields=RAW)
    grant(op, p, "retain_normalized", fields=RAW)
    return op, p, a, b, admin, web


def ingest_value(world, value, token, **terms):
    op, p, *_ = world
    ingest(op, p, [candidate(p, terms.pop("code", "101"), value=value, token=token, **terms)])


def revisions(p):
    with observe_db() as db, db.cursor() as cur:
        cur.execute(
            """SELECT k.watermark, k.qualification_version, k.disputed, r.value,
                             array_agg(t.outcome ORDER BY t.id)
                        FROM observations_cityobservation k
                        JOIN observations_cityrevision r ON r.key_id = k.id AND r.is_current
                        JOIN observations_receipt t ON t.city_revision_id = r.id
                       WHERE k.product_id = %s
                       GROUP BY k.id, r.id""",
            [p.pk],
        )
        return cur.fetchall()


def test_v06_watermark_content_and_precise_ticket(world, record_property):
    _, p, a, _, _, web = world
    ingest_value(world, 10, 1)
    (before,) = revisions(p)
    assert before == (1, 1, False, Decimal(10), ["first"])
    query = parent(p, [a], hours=1, method="statistics")
    first = check(web.post(STATISTICS, json=query), 200)
    saved = check(web.post(SAVED, json=query | {"title": "Private F intent"}), 201)
    old = export(web, query, first["input_version"])
    ingest_value(world, 10, 3)
    (same_value,) = revisions(p)
    assert same_value == (3, 1, False, Decimal(10), ["first", "watermark_advanced"])
    third = check(web.post(STATISTICS, json=query), 200)
    same_content = {k: v for k, v in first.items() if k != "input_version"} == {
        k: v for k, v in third.items() if k != "input_version"
    }
    input_changed = first["input_version"] != third["input_version"]
    assert same_content and input_changed
    revisit = check(web.post(SAVED + saved["id"] + "/run/?scope=synthetic", json={}), 200)
    assert revisit["state"] == "current"
    third_ticket = export(web, query, third["input_version"])
    details = web.post(DETAIL, json=query | {"input_version": first["input_version"]})
    assert check(details, 409) == {"code": "input_changed"}
    blocked = reject_download(web, old)
    ingest_value(world, 20, 2)
    final = check(web.post(STATISTICS, json=query), 200)
    assert final["input_version"] == third["input_version"]
    assert final == third
    assert SavedQuery.objects.get(pk=saved["id"]).baseline
    committed = revisions(p)
    assert committed == [(3, 1, False, Decimal(10), ["first", "watermark_advanced", "late"])]
    (late,) = committed
    response = web.get(third_ticket)
    assert response.status_code == 200 and response.headers["Content-Type"].startswith("text/csv")
    assert len(response.content) > 0
    witness(
        record_property,
        pg_at=clock(),
        condition="V06",
        watermark_before=before[0],
        watermark_same_value=same_value[0],
        watermark_late=late[0],
        receipts_before=len(before[4]),
        receipts_same_value=len(same_value[4]),
        receipts_late=len(late[4]),
        receipt_late=late[4][-1] == "late",
        current_keys=len(committed),
        current_revisions=CityObservation.objects.get(product=p)
        .revisions.filter(is_current=True)
        .count(),
        same_content=same_content,
        input_changed_on_same_value=input_changed,
        input_unchanged_on_late=final["input_version"] == third["input_version"],
        saved_state=revisit["state"],
        details_http=details.status_code,
        http=blocked.status_code,
        csv_bytes=csv_bytes(blocked),
        peer_http=response.status_code,
        peer_csv_bytes=csv_bytes(response),
    )
    ingest_value(world, 30, 4)
    key = CityObservation.objects.get(product=p)
    assert key.revisions.count() == 2 and key.revisions.get(is_current=True).value == 30
    reject_download(web, third_ticket)


def entered_conflict(world):
    _, p, _, _, admin, _ = world
    ingest_value(world, 10, 1)
    ingest_value(world, 20, 1)
    event = QualityIssue.objects.get(product=p, code="same_version_conflict")
    assert event.receipt.city_revision.candidate
    assert "correct_source" not in ask(admin, event)["evidence"]
    ingest_value(world, 30, 3)
    return event


def test_v07_complete_evidence_clearance_and_closed_unordered_conflict(world):
    _, p, a, _, admin, web = world
    event = entered_conflict(world)
    review = ask(admin, event)
    ingest_value(world, 20, 1)  # late ordered evidence doesn't alter the snapshot
    assert ask(admin, event)["event_version"] == review["event_version"]
    assert action(web, event, command(review), 403)["code"] in {"use_denied", "permission_denied"}
    assert action(admin, event, command(review, value=999), 400)["code"] == "invalid_request"
    assert (
        action(admin, event, command(review, evidence_ref="arbitrary-proof"), 403)["code"]
        == "mapping_evidence_mismatch"
    )
    result = action(admin, event, command(review))
    assert result["status"] == "resolved"
    key = CityObservation.objects.get(product=p)
    assert (key.watermark, key.qualification_version, key.disputed) == (3, 3, False)
    assert key.revisions.count() == 3
    assert CandidateClearance.objects.get().correction.city_revision.value == 30
    decision = QualityDecision.objects.get(action_id=result["action_id"])
    assert (
        decision.city_before_id == decision.city_after_id == key.revisions.get(is_current=True).pk
    )
    assert (decision.qualification_before, decision.qualification_after) == (2, 3)
    assert Change.objects.filter(city_key=key, kind="qualification_changed").count() == 2
    ingest_value(world, 90, None)
    unordered = QualityIssue.objects.get(product=p, code="unordered_conflict")
    review = ask(admin, unordered)
    assert "correct_source" not in review["evidence"]
    action(admin, unordered, command(review, "close"))
    ingest_value(world, 90, None)
    assert QualityIssue.objects.filter(product=p, code="unordered_conflict").count() == 1
    key.refresh_from_db()
    assert key.disputed
    query = parent(p, [a], hours=1, method="statistics")
    result = check(web.post(STATISTICS, json=query), 200)
    assert (result["columns"][0]["O"], result["columns"][0]["V"]) == (1, 0)


def test_v07_candidates_from_closed_and_open_issues_are_all_covered(world):
    _, p, _, _, admin, _ = world
    ingest_value(world, 10, 1)
    ingest_value(world, 20, 1)
    old = QualityIssue.objects.get(product=p, code="same_version_conflict")
    action(admin, old, command(ask(admin, old), "close"))
    ingest_value(world, 25, 1)
    current = QualityIssue.objects.exclude(pk=old.pk).get(product=p, code="same_version_conflict")
    assert len(ask(admin, current)["candidate_revisions"]) == 2
    ingest_value(world, 30, 3)
    proof = ask(admin, current)
    assert len(proof["candidate_revisions"]) == 2
    action(admin, current, command(proof))
    assert CandidateClearance.objects.count() == 2
    assert not CityObservation.objects.get(product=p).disputed


def test_v07_substantive_evidence_reopens_review_without_clearing_isolation(world):
    _, p, _, _, admin, _ = world
    ingest_value(world, 10, 1)
    ingest_value(world, 20, 1)
    event = QualityIssue.objects.get(product=p, code="same_version_conflict")
    closed = action(admin, event, command(ask(admin, event), "close"))
    ingest_value(world, 10, 2)
    key = CityObservation.objects.get(product=p)
    assert key.disputed and (key.watermark, key.qualification_version) == (2, 2)
    reopened = ask(admin, event)
    assert reopened["event_version"] == closed["event_version"] + 1 and reopened["status"] == "open"
    assert reopened["handler"] is None and "correct_source" in reopened["evidence"]


def test_v07_mapping_requires_retained_original_and_review(world):
    op, p, a, _, admin, _ = world
    ingest_value(world, 10, 1)
    key = CityObservation.objects.get(product=p, city=a)
    event = QualityIssue.objects.create(product=p, city_key=key, code="mapping_review_required")
    proof = ask(admin, event)
    assert "correct_mapping" in proof["evidence"]
    grant(op, p, "review", effect="deny", object_code="101", fields=RAW - REQUIRED)
    assert "correct_mapping" not in ask(admin, event)["evidence"]
    assert action(admin, event, command(proof, "correct_mapping"), 403)["code"] == "use_denied"
    assert key.revisions.count() == 1


def test_v08_mapping_does_not_restore_an_unretained_original_time(world):
    op, p, a, _, admin, _ = world
    grant(op, p, "retain_normalized", effect="deny", object_code="101", fields={"source_time"})
    ingest_value(world, 10, 1)
    key = CityObservation.objects.get(product=p, city=a)
    initial = key.revisions.get(is_current=True)
    assert initial.provenance["source_time"] is None
    event = QualityIssue.objects.create(product=p, city_key=key, code="mapping_review_required")
    result = action(admin, event, command(ask(admin, event), "correct_mapping"))
    decision = QualityDecision.objects.get(action_id=result["action_id"])
    assert decision.city_before == initial
    assert decision.city_after == key.revisions.get(is_current=True)
    assert decision.city_after.provenance["source_time"] is None
    assert decision.city_after.kind == "local_mapping_fix"


@pytest.mark.parametrize("first", ["candidate", "governance"])
def test_v17_candidate_and_adjudication_commit_order(world, sql_hook, first, record_property):
    _, p, _, _, admin, _ = world
    event = entered_conflict(world)
    review = ask(admin, event)
    payload = command(review)
    blocked = Queue()

    def hook(execute, sql, params, many, context):
        if "FOR UPDATE" in sql and '"observations_product"' in sql:
            blocked.put(pid())
        return execute(sql, params, many, context)

    sql_hook["candidate-race"] = hook
    admin.headers["X-S06-Test-Hook"] = "candidate-race"
    if first == "candidate":
        with ThreadPoolExecutor(max_workers=1) as pool:
            with transaction.atomic():
                holder = pid()
                ingest_value(world, 50, None)
                future = pool.submit(admin.post, f"{QUALITY}{event.pk}/actions/", json=payload)
                blocked_by(blocked.get(timeout=5), holder)
                wait_at = clock()
            response = future.result(timeout=5)
            assert check(response, 409)["code"] == "precondition_conflict"
        assert CandidateClearance.objects.count() == 0
        assert not GovernanceAction.objects.filter(request_id=payload["request_id"]).exists()
        refreshed = ask(admin, event)
        assert len(refreshed["candidate_revisions"]) == 2
        assert "correct_source" not in refreshed["evidence"]
    else:
        locked = Queue()
        release = Event()

        def hold_governance(execute, sql, params, many, context):
            if sql.startswith('INSERT INTO "accounts_governanceaction"'):
                locked.put(pid())
                assert release.wait(4)
            return execute(sql, params, many, context)

        sql_hook["candidate-race"] = hold_governance

        def write():
            blocked.put(pid())
            ingest_value(world, 50, None)

        with ThreadPoolExecutor(max_workers=2) as pool:
            review_future = pool.submit(admin.post, f"{QUALITY}{event.pk}/actions/", json=payload)
            holder = locked.get(timeout=5)
            write_future = pool.submit(in_thread, write)
            try:
                blocked_by(blocked.get(timeout=5), holder)
                wait_at = clock()
            finally:
                release.set()
            response = review_future.result(timeout=5)
            check(response, 200)
            write_future.result(timeout=5)
        assert CandidateClearance.objects.count() == 1
    key = CityObservation.objects.get(product=p)
    assert key.disputed and key.watermark == 3
    assert key.revisions.get(is_current=True).value == 30
    event.refresh_from_db()
    witness(
        record_property,
        pg_at=wait_at,
        condition="C04",
        order=first,
        lock_target="product",
        pg_lock_wait=True,
        http=response.status_code,
        event_version_before=review["event_version"],
        event_version_after=event.event_version,
        quality_clearances=CandidateClearance.objects.count(),
        success_audit_count=GovernanceAction.objects.filter(
            request_id=payload["request_id"]
        ).count(),
        dispute=key.disputed,
    )


@pytest.mark.parametrize("first", ["revoke", "adjudicate"])
def test_v17_role_and_adjudication_commit_order(
    world,
    sql_hook,
    web_clients,
    django_user_model,
    first,
):
    _, p, _, _, administrator, _ = world
    reviewer = django_user_model.objects.create_user("s06-adjudicator", password=PASSWORD)
    reviewer.user_permissions.add(
        permission("observations.review_observation"), permission("accounts.adjudicate_quality")
    )
    web = sign_in(web_clients(), reviewer, PASSWORD)
    event = entered_conflict(world)
    proof = command(ask(web, event))
    review_url = f"{QUALITY}{event.pk}/actions/"
    account_url = destination(reviewer)
    edition = check(administrator.get(account_url), 200)["version"]
    revoke = request_for("revoke", edition, permission="accounts.adjudicate_quality")
    waiting, held = Queue(), Queue()
    release = Event()

    def monitor(execute, sql, params, many, context):
        if " FOR UPDATE" in sql and '"auth_user"' in sql:
            waiting.put(pid())
        return execute(sql, params, many, context)

    def hold_audit(execute, sql, params, many, context):
        if sql.startswith('INSERT INTO "accounts_governanceaction"'):
            held.put(pid())
            assert release.wait(4)
        return execute(sql, params, many, context)

    sql_hook.update({"role-holding": hold_audit, "role-waiting": monitor})
    winner = (administrator, account_url, revoke) if first == "revoke" else (web, review_url, proof)
    loser = (web, review_url, proof) if first == "revoke" else (administrator, account_url, revoke)

    def post_selected(selected, role):
        client, url, payload = selected
        return client.post(url, json=payload, headers={"X-S06-Test-Hook": role})

    with ThreadPoolExecutor(max_workers=2) as pool:
        win = pool.submit(post_selected, winner, "role-holding")
        holder = held.get(timeout=5)
        lose = pool.submit(post_selected, loser, "role-waiting")
        try:
            blocked_by(waiting.get(timeout=5), holder)
        finally:
            release.set()
        check(win.result(timeout=5), 200)
        check(lose.result(timeout=5), 403 if first == "revoke" else 200)
    assert CityObservation.objects.get(product=p).disputed == (first == "revoke")
    assert CandidateClearance.objects.count() == (0 if first == "revoke" else 1)
    assert GovernanceAction.objects.filter(request_id=proof["request_id"]).count() == (
        0 if first == "revoke" else 1
    )


@pytest.mark.parametrize("destination", ["same", "empty", "collision"])
def test_v08_v09_mapping_scope_and_exact_read_invalidation(world, destination):
    _, p, a, b, admin, web = world
    ingest_value(world, "100", 7)
    key = CityObservation.objects.get(product=p, city=a)
    event = QualityIssue.objects.create(product=p, city_key=key, code="mapping_review_required")
    if destination == "same":
        ingest_value(world, "101", 7)
    elif destination == "collision":
        ingest_value(world, "20", 7, code="102")
    query = parent(p, [a], hours=1, method="statistics")
    original = check(web.post(STATISTICS, json=query), 200)
    target_query = parent(p, [b], hours=1, method="statistics")
    initial_target = check(web.post(STATISTICS, json=target_query), 200)
    saved = check(web.post(SAVED, json=query | {"title": "Private F intent"}), 201)
    ticket = export(web, query, original["input_version"]) if destination != "same" else None
    unaffected = parent(p, [a], hours=2, method="statistics")
    unaffected["selection"]["start"] = (START + HOUR).isoformat()
    control = check(web.post(STATISTICS, json=unaffected), 200)
    review = ask(admin, event, target=None if destination == "same" else "102")
    response = action(admin, event, command(review, "correct_mapping"))
    decision = QualityDecision.objects.get(action_id=response["action_id"])
    key.refresh_from_db()
    assert key.watermark == 7
    if destination == "same":
        assert response["outcome"] == "local_mapping_fix"
        assert key.disputed and key.rule_floor == 2
        assert key.revisions.get(is_current=True).value == Decimal("0.1")
    else:
        assert {row["city_id"] for row in response["affected"]} == {a.pk, b.pk}
        target = CityObservation.objects.get(product=p, city=b)
        if destination == "collision":
            assert response["outcome"] == "mapping_conflict"
            assert key.disputed and target.disputed and not key.retired
            assert target.revisions.get(is_current=True).value == 20
            assert target.revisions.filter(candidate=True, kind="mapping_conflict").count() == 1
            assert decision.city_after.candidate and decision.city_after.key_id == target.pk
        else:
            assert response["outcome"] == "local_mapping_fix"
            assert key.retired and not target.disputed and target.watermark == 7
            assert target.revisions.get(is_current=True).value == Decimal("0.1")
            assert initial_target["columns"][0]["V"] == 0
            refreshed_target = check(web.post(STATISTICS, json=target_query), 200)
            assert refreshed_target["columns"][0]["V"] == 1
            assert refreshed_target["input_version"] != initial_target["input_version"]
        assert ExportTicket.objects.count() == 1
        reject_download(web, ticket)
    assert (
        check(web.post(DETAIL, json=query | {"input_version": original["input_version"]}), 409)[
            "code"
        ]
        == "input_changed"
    )
    latest = check(web.post(STATISTICS, json=query), 200)
    assert latest["columns"][0]["V"] == 0
    revisit = check(web.post(SAVED + saved["id"] + "/run/?scope=synthetic", json={}), 200)
    if destination != "same":
        assert revisit["state"] == "data_changed"
    assert (
        control["input_version"]
        == check(web.post(STATISTICS, json=unaffected), 200)["input_version"]
    )
    assert "Private F intent" not in str(response)


@pytest.mark.parametrize("first", ["revision", "download", "failed_commit"])
def test_v17_download_transaction_releases_no_uncommitted_csv(
    world, sql_hook, monkeypatch, first, record_property
):
    _, p, a, _, _, web = world
    ingest_value(world, 10, 1)
    query = parent(p, [a], hours=1, method="statistics")
    version = check(web.post(STATISTICS, json=query), 200)["input_version"]
    path = export(web, query, version)
    waiting = Queue()
    hold = Event()
    read_locked = Queue()
    encoded = Event()
    original = exports.encode_csv

    def encode(*args, **kwargs):
        body = original(*args, **kwargs)
        encoded.set()
        if first == "download":
            read_locked.put(pid())
            assert hold.wait(4)
        return body

    monkeypatch.setattr(exports, "encode_csv", encode)

    def watch(execute, sql, params, many, context):
        if '"observations_product"' in sql and "FOR UPDATE" in sql:
            waiting.put(pid())
            if first == "failed_commit":
                db = context["connection"]
                commit = db._commit

                def fail_commit():
                    db._commit = commit
                    assert encoded.is_set()
                    raise OperationalError("injected disposable download commit failure")

                db._commit = fail_commit
        return execute(sql, params, many, context)

    sql_hook["download-race"] = watch
    web.headers["X-S06-Test-Hook"] = "download-race"
    if first == "failed_commit":
        response = web.get(path)
        observed_at = clock()
        assert response.status_code in {500, 503}
        assert "Content-Disposition" not in response.headers
        assert not response.headers["Content-Type"].startswith("text/csv")
        web.headers.pop("X-S06-Test-Hook")
        replay = web.get(path)
        assert replay.headers["Content-Type"].startswith("text/csv")
    elif first == "revision":
        with ThreadPoolExecutor(max_workers=1) as pool:
            with transaction.atomic():
                holder = pid()
                ingest_value(world, 20, 2)
                reading = pool.submit(reject_download, web, path)
                blocked_by(waiting.get(timeout=5), holder)
                observed_at = clock()
            response = reading.result(timeout=5)
        assert not encoded.is_set()
        replay = reject_download(web, path)
    else:
        writer_pid = Queue()

        def write():
            writer_pid.put(pid())
            ingest_value(world, 20, 2)

        with ThreadPoolExecutor(max_workers=2) as pool:
            reading = pool.submit(web.get, path)
            holder = read_locked.get(timeout=5)
            writing = pool.submit(in_thread, write)
            try:
                blocked_by(writer_pid.get(timeout=5), holder)
                observed_at = clock()
            finally:
                hold.set()
            response = reading.result(timeout=5)
            writing.result(timeout=5)
        assert response.status_code == 200
        assert response.headers["Content-Type"].startswith("text/csv") and response.content
        replay = reject_download(web, path)
    witness(
        record_property,
        pg_at=observed_at,
        condition="C04",
        order=first,
        lock_target="product",
        pg_lock_wait=first != "failed_commit",
        http=response.status_code,
        csv_bytes=csv_bytes(response),
        replay_http=replay.status_code,
        replay_csv_bytes=csv_bytes(replay),
    )
