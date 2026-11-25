"""S08 operational health enhancement and first-valid-write proof."""

import json
from dataclasses import replace
from datetime import datetime

import pytest
from collection import adapters, services
from collection.models import Run
from django.contrib.auth.models import Permission
from observations.models import (
    CandidateClearance,
    CityObservation,
    CityRevision,
    QualityDecision,
    QualityIssue,
    Receipt,
    Source,
)
from observations.policy import clock, revoke_grant
from s02_support import BOUNDARY, observe_db
from s05_http import loopback
from s05_support import candidate, ingest
from test_s06_quality import action, ask, command
from test_s07_operations import account, seed_windows, success

from tests.collection.test_collection_postgres import (
    LIMITS,
    city_packet,
    grant,
    make_job,
    request,
    station_packet,
)

pytestmark = pytest.mark.django_db(transaction=True)
HEALTH = "/api/v1/admin/operations/health/"


def evidence(web, window, **selectors):
    return success(
        web.get(HEALTH, params={"window": window, "include": "first_valid_write", **selectors})
    )["window"]


def assert_first(projection, state, expected):
    assert projection["first_valid_write_state"] == state
    instant = projection["latest_first_valid_write_at"]
    assert (datetime.fromisoformat(instant) if instant is not None else None) == expected


def test_first_valid_write_is_window_bounded_and_fails_closed(
    django_user_model, monkeypatch, settings
):
    operator = account(django_user_model, "first-write", superuser=True)
    _job, attempts = seed_windows(operator, monkeypatch, settings)
    complete, _, failed, _, empty, *_ = attempts
    window = complete.window
    linked = (
        Receipt.objects.filter(
            collection_item__window_id=window.pk,
            city_revision__quality="valid",
            city_revision__value__isnull=False,
        )
        .order_by("-city_revision__created_at")
        .first()
        .city_revision
    )
    expected = linked.created_at
    pending = Run.objects.create(
        job=complete.job,
        window=window,
        actor=operator,
        executor=operator,
        reason="manual",
        error_code="budget_exhausted",
        next_retry_at=clock(),
    )

    with loopback(operator) as (web, _):
        plain = web.get(HEALTH)
        assert plain.status_code == 200
        assert plain.json()["window"] is None
        assert web.get(HEALTH, params={"include": "first_valid_write"}).json() == {
            "code": "invalid_request"
        }

        data = evidence(web, window.pk)
        assert_first(data, "known", expected)
        assert datetime.fromisoformat(data["latest_visible_fetched_at"]) == complete.fetched_at
        assert (
            datetime.fromisoformat(data["oldest_visible_pending_created_at"]) == pending.created_at
        )
        assert data["visible_pending"] == data["visible_quota_wait"] == 1
        assert data["visible_errors"] == [
            {"error_code": "budget_exhausted", "result": "", "request_state": "unsent"}
        ]
        assert_first(evidence(web, empty.window_id), "none", None)
        failed_data = evidence(web, failed.window_id)
        assert_first(failed_data, "none", None)
        assert failed_data["visible_errors"][0]["error_code"] == "timeout"

        # An incomplete historical revision sequence is not mislabeled as "none".
        CityRevision.objects.filter(pk=linked.pk).update(revision_no=2)
        assert_first(evidence(web, window.pk), "unavailable", None)


def test_first_valid_write_requires_review_permission(django_user_model, monkeypatch, settings):
    operator = account(django_user_model, "first-write-owner", superuser=True)
    _job, attempts = seed_windows(operator, monkeypatch, settings)
    diagnostics = account(django_user_model, "first-write-diagnostics")
    diagnostics.user_permissions.add(
        Permission.objects.get(content_type__app_label="accounts", codename="diagnose_operations")
    )
    with loopback(diagnostics) as (web, _):
        plain = web.get(HEALTH)
        assert plain.status_code == 200
        denied = web.get(
            HEALTH,
            params={"window": attempts[0].window_id, "include": "first_valid_write"},
        )
        assert denied.status_code == 403
        assert denied.json() in ({"code": "permission_denied"}, {"code": "use_denied"})


@pytest.fixture
def collect(django_user_model, monkeypatch, settings):
    operator = account(django_user_model, "first-write-collector", superuser=True)
    settings.S03_FIXTURE_HTTP = True
    source = Source.objects.create(
        namespace="s03.fixture", publisher="F history", collection_limits=LIMITS
    )
    monkeypatch.setattr(services, "publish", lambda _run_id: None)

    def run(job, packet):
        for operation in ("acquire_online", "process_local", "retain_normalized", "review"):
            grant(operator, job.product, operation)
        payload = json.dumps(packet).encode()
        monkeypatch.setattr(adapters, "fetch", lambda *_: (payload, clock()))
        attempt = services.submit(operator.pk, request(job))
        services.execute(attempt.pk)
        attempt.refresh_from_db()
        return attempt

    return operator, source, run


@pytest.fixture
def missing_co(collect):
    operator, source, execute = collect
    job = make_job(operator, source)
    run = execute(job, city_packet() | {"CO": "NA"})
    assert run.result == "partial"
    initial = Receipt.objects.get(batch_id=run.batch_id, collection_item__indicator="CO")
    assert initial.collection_item.accepted and initial.outcome == "first"
    assert initial.city_revision.quality == "missing"
    return operator, job.product, run, initial.city_revision.key


def ordered_write(operator, product, indicator, value, token):
    row = candidate(product, "123", BOUNDARY, value, indicator=indicator, token=token)
    # Match the timezone evidence stored by the unversioned F collector for the same label.
    revised = replace(row.data, time=replace(row.data.time, timezone_basis="fixture_source_label"))
    ingest(operator, product, [replace(row, data=revised)])


@pytest.mark.parametrize("disputed", [False, True])
def test_first_valid_write_uses_the_first_eligible_transition(missing_co, disputed):
    operator, product, run, key = missing_co
    with loopback(operator) as (web, _):

        def health():
            return evidence(web, run.window_id, indicator="CO")

        initial = health()
        assert_first(initial, "none", None)
        assert initial["coverage"]["O"] == 1 and initial["coverage"]["V"] == 0
        ordered_write(operator, product, "CO", "NA", 1)
        assert key.revisions.count() == 1  # Same value advanced the watermark only.
        if disputed:
            ordered_write(operator, product, "CO", 10, 1)
            ordered_write(operator, product, "CO", 11, 1)
            event = QualityIssue.objects.get(city_key=key, code="same_version_conflict")
            assert key.revisions.filter(candidate=True, quality="valid").count() == 2
            assert_first(health(), "none", None)
            action(web, event, command(ask(web, event), "close"))
            assert_first(health(), "none", None)

        ordered_write(operator, product, "CO", 0, 2)
        head = key.revisions.get(is_current=True)
        assert head.quality == "valid" and head.value == 0
        assert key.observed_at_utc == BOUNDARY < run.fetched_at < head.created_at
        if disputed:
            assert_first(health(), "none", None)
            corrected = action(web, event, command(ask(web, event)))
            decision = QualityDecision.objects.get(action_id=corrected["action_id"])
            assert decision.city_before_id == decision.city_after_id == head.pk
            expected = decision.action.created_at
            assert expected > head.created_at
            assert CandidateClearance.objects.filter(action=decision.action).count() == 2
        else:
            with observe_db() as db, db.cursor() as sql:
                sql.execute(
                    """SELECT r.created_at FROM observations_cityrevision r
                         JOIN observations_receipt t ON t.city_revision_id=r.id
                        WHERE r.key_id=%s AND r.quality='valid' AND r.value=0
                          AND NOT r.candidate AND t.outcome='content_changed'""",
                    [key.pk],
                )
                [(expected,)] = sql.fetchall()
        assert_first(health(), "known", expected)
        for token in (2, 3, 1):
            ordered_write(operator, product, "CO", 0, token)
        receipts = Receipt.objects.filter(city_revision=head)
        assert set(receipts.values_list("outcome", flat=True)) >= {
            "duplicate",
            "watermark_advanced",
            "late",
        }
        ordered_write(operator, product, "CO", 1, 4)
        assert key.revisions.get(is_current=True).pk != head.pk
        assert_first(health(), "known", expected)
        if disputed:
            clearance = CandidateClearance.objects.filter(action=decision.action).first()
            clearance.delete()
            assert_first(health(), "unavailable", None)


def test_first_valid_write_ignores_writes_to_unreviewable_fields(missing_co):
    operator, product, run, _ = missing_co
    ordered_write(operator, product, "CO", "NA", 1)
    ordered_write(operator, product, "CO", 0, 2)
    broad = product.usegrant_set.get(operation="review")
    grant(operator, product, "review", object_code="123", indicator="AQI")
    revoke_grant(operator.pk, broad.pk, "F audit restricted to AQI")
    key = CityObservation.objects.get(product=product, indicator="AQI")
    with loopback(operator) as (web, _):
        selectors = {"object_code": "123", "indicator": "AQI"}
        before = evidence(web, run.window_id, **selectors)
        assert before["state"] == "partially_authorized"
        assert_first(before, "known", key.revisions.get().created_at)
        assert before["latest_visible_fetched_at"] is None and before["visible_errors"] == []
        ordered_write(operator, product, "CO", 1, 3)
        after = evidence(web, run.window_id, **selectors)
        assert after["coverage"].pop("observation_age_seconds") >= before["coverage"].pop(
            "observation_age_seconds"
        )
        assert after == before
        denied = web.get(
            HEALTH,
            params={
                "window": run.window_id,
                "include": "first_valid_write",
                **(selectors | {"indicator": "CO"}),
            },
        )
        assert denied.status_code == 403 and denied.json() == {"code": "use_denied"}


@pytest.mark.parametrize("truncated", [False, True])
def test_health_rechecks_original_run_page_at_session_finish(collect, monkeypatch, truncated):
    from collection import queries

    operator, source, execute = collect
    if truncated:
        codes = [f"A{i:03}" for i in range(8)]
        job = make_job(operator, source, "S", expected_codes=codes)
        run = execute(job, station_packet(job, source, codes))
        selectors = {}
    else:
        job = make_job(operator, source, "C")
        run = execute(job, [city_packet()])
        selectors = {"object_code": "123", "indicator": "AQI"}
        grant(operator, job.product, "review", **selectors)

    with loopback(operator) as (web, _):
        params = {"window": run.window_id, "include": "first_valid_write", **selectors}
        before = success(web.get(HEALTH, params=params))["window"]
        assert before["latest_visible_fetched_at"] is not None
        assert before["first_valid_write_page_truncated"] is truncated
        # Only the initial page reads at the earlier PG instant. Session.finish uses
        # the live database clock, by which time the broad review grant has expired.
        page_time = clock()
        monkeypatch.setattr(queries, "clock", lambda: page_time)
        job.product.usegrant_set.filter(operation="review", object_code="*").update(
            valid_until=clock()
        )
        denied = web.get(HEALTH, params=params)
        assert denied.status_code == 403 and denied.json() == {"code": "use_denied"}


def test_first_valid_write_proves_station_epoch_through_accepted_receipt(collect):
    operator, source, execute = collect
    job = make_job(operator, source, "S", expected_codes=["A001", "B002"])
    run = execute(job, station_packet(job, source))
    assert run.result == "new"
    receipt = Receipt.objects.get(
        batch_id=run.batch_id, collection_item__object_code="A001", collection_item__indicator="CO"
    )
    sibling = Receipt.objects.get(
        batch_id=run.batch_id, collection_item__object_code="B002", collection_item__indicator="CO"
    )
    assert receipt.station_revision.key.site_epoch_id != sibling.station_revision.key.site_epoch_id
    with loopback(operator) as (web, _):
        selectors = {"object_code": "A001", "indicator": "CO"}
        first = evidence(web, run.window_id, **selectors)
        assert_first(first, "known", receipt.station_revision.created_at)
        assert datetime.fromisoformat(first["coverage"]["latest_valid_at"]) == BOUNDARY
        receipt.delete()  # Inject loss of the accepted item's lineage, not loss of the key.
        assert_first(evidence(web, run.window_id, **selectors), "unavailable", None)
        assert_first(
            evidence(web, run.window_id, object_code="B002", indicator="CO"),
            "known",
            sibling.station_revision.created_at,
        )
