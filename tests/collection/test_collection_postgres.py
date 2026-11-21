"""S03 PostgreSQL ledger tests using self-authored inputs and isolated transactions."""

import json
import ssl
import subprocess
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from queue import Queue
from uuid import uuid4

import pytest
from collection import adapters, queries, services, writer
from collection.adapters import transport
from collection.models import ControlEvent, Job, Run, RunItem, WorkWindow
from collection.queries import read_window
from django.contrib.auth.models import Permission
from django.db import IntegrityError, connection, transaction
from observations.disposition import dispose_due
from observations.models import (
    Batch,
    City,
    CityIdentifier,
    CityObservation,
    CityRevision,
    Product,
    QualityIssue,
    Receipt,
    SiteEpoch,
    Source,
    Station,
    StationIdentifier,
    StationObservation,
)
from observations.policy import Denied, clock, revoke_grant
from observations.queries import read_current
from psycopg.types.range import TimestamptzRange

from tests.s02_support import (
    BOUNDARY,
    END,
    START,
    allow,
    create_product,
    in_db_session,
    observe_db,
    reject_observation_write,
    selector,
    sql_copy,
    wait_for_block,
)

pytestmark = pytest.mark.django_db(transaction=True)
LIMITS = {
    "min_interval_seconds": 1,
    "max_concurrent": 1,
    "period_seconds": 60,
    "max_requests": 12,
}


@pytest.fixture
def operator(django_user_model):
    user = django_user_model.objects.create_user("s03-pg-operator", is_staff=True)
    user.user_permissions.add(
        *Permission.objects.filter(
            content_type__app_label__in=["collection", "observations"],
            codename__in=[
                "run_collection",
                "manage_collection",
                "review_observation",
                "manage_grants",
            ],
        )
    )
    return user


@pytest.fixture
def source():
    return Source.objects.create(
        namespace="s03.fixture", publisher="S03 PostgreSQL fixture", collection_limits=LIMITS
    )


def grant(operator, product, operation, **kwargs):
    terms = dict(data_from=date.min, data_until=date.max, fields=["*"])
    if operation == "acquire_online":
        terms["collection_limits"] = LIMITS
    return allow(operator, product, operation, **(terms | kwargs))


def make_job(operator, source, shape="C", **kwargs):
    product = create_product(shape, shape, source)
    product.contract_version = "s03.f.v1"
    product.save(update_fields=["contract_version"])
    city = None
    if shape != "L":
        city = City.objects.create(source=source, name="Fixture City", geography="fixture-city")
        CityIdentifier.objects.create(
            city=city,
            source=source,
            code="123",
            active_period=TimestamptzRange(START, END),
            evidence="F synthetic directory",
        )
    options = dict(
        product=product,
        city=city,
        executor=operator,
        profile="f-exact-v1",
        expected_codes=["123"],
        roster_basis="F synthetic roster",
        enabled=True,
    )
    return Job.objects.create(**(options | kwargs))


def request(job, *, target=BOUNDARY, reason="manual", token=None):
    return {
        "job": job.pk,
        "target": target.isoformat(),
        "request_id": str(token or uuid4()),
        "reason": reason,
    }


@pytest.fixture
def job(operator, source):
    result = make_job(operator, source)
    for operation in ("acquire_online", "retain_normalized", "process_local", "review", "display"):
        grant(operator, result.product, operation)
    return result


@pytest.fixture(autouse=True)
def published(monkeypatch, settings):
    settings.S03_FIXTURE_HTTP = True
    # Redis and worker delivery are tested separately by the disposable Compose verifier.
    calls = []
    monkeypatch.setattr(services, "publish", lambda run_id: calls.append(str(run_id)))
    return calls


def test_v01_database_rejects_duplicate_window_invalid_fence_and_cross_window_item(operator, job):
    run = services.submit(operator.pk, request(job))
    other = services.submit(operator.pk, request(job, target=BOUNDARY + timedelta(hours=1)))
    assert other.window_id != run.window_id

    with pytest.raises(IntegrityError, match="s03_business_window"), transaction.atomic():
        sql_copy(run.window)
    with pytest.raises(IntegrityError, match="s03_window_fence"), transaction.atomic():
        sql_copy(run.window, target_start=BOUNDARY - timedelta(hours=1), owner=uuid4())
    with pytest.raises(IntegrityError, match="s03_request_budget"), transaction.atomic():
        Run.objects.filter(pk=run.pk).update(request_state="possible")

    item = dict(
        item_key="city:123:AQI",
        object_code="123",
        indicator="AQI",
        observed_at=BOUNDARY,
        generation=1,
        owner=uuid4(),
        accepted=True,
        status="accepted",
    )
    accepted = RunItem.objects.create(run=run, window=run.window, **item)
    with pytest.raises(IntegrityError, match="s03_item_same_window"), transaction.atomic():
        sql_copy(accepted, window=other.window_id, item_key="different-key")
    competitor = services.submit(operator.pk, request(job))
    with pytest.raises(IntegrityError, match="s03_window_accept_once"), transaction.atomic():
        sql_copy(accepted, run=competitor.pk)
    with pytest.raises(IntegrityError, match="s03_item_states"), transaction.atomic():
        sql_copy(accepted, item_key="different-key", status="reread")
    assert list(RunItem.objects.values_list("pk", flat=True)) == [accepted.pk]


def test_v07_concurrent_equivalent_jobs_and_shared_idempotency_use_one_window(operator, job):
    second_job = Job.objects.create(
        product=job.product,
        city=job.city,
        executor=job.executor,
        profile=job.profile,
        expected_codes=job.expected_codes,
        roster_basis=job.roster_basis,
        enabled=True,
    )
    original = request(job)
    duplicate, independent = original.copy(), request(second_job)
    pids = Queue()
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [
            pool.submit(in_db_session, pids, lambda data=data: services.submit(operator.pk, data))
            for data in (original, duplicate, independent)
        ]
        rows = [future.result(timeout=10) for future in futures]
    assert len({pids.get_nowait() for _ in rows}) == 3
    assert WorkWindow.objects.count() == 1
    assert rows[0].pk == rows[1].pk
    assert rows[0].window_id == rows[2].window_id
    assert rows[0].pk != rows[2].pk
    assert Run.objects.count() == 2


def test_v08_duplicate_original_uuid_has_no_second_claim(operator, job):
    run = services.submit(operator.pk, request(job))
    pids = Queue()
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(in_db_session, pids, lambda: services.claim(run.pk)) for _ in range(2)
        ]
        owners = [future.result(timeout=10) for future in futures]
    assert pids.get_nowait() != pids.get_nowait()
    assert owners.count(None) == 1
    run.refresh_from_db()
    assert run.owner == next(owner for owner in owners if owner is not None)
    assert run.request_state == "unsent"
    assert run.reserved_at is None
    assert run.status == "running"
    assert run.generation == 1
    assert WorkWindow.objects.get(pk=run.window_id).lease_until > clock()


def expire(run, *, response_budget=False):
    """Advance the test-owned lease and budget timestamps without replacing the PG clock."""
    now = clock()
    WorkWindow.objects.filter(pk=run.window_id).update(lease_until=now - timedelta(seconds=1))
    if response_budget:
        Run.objects.filter(pk=run.pk).update(
            reserved_at=now - timedelta(seconds=5), occupied_until=now - timedelta(seconds=1)
        )


def ready_for_retry(run):
    Run.objects.filter(pk=run.pk).update(next_retry_at=clock() - timedelta(seconds=1))
    services.retry_due(run.pk)
    return Run.objects.get(predecessor=run, reason="retry")


def test_v08_v09_expired_possible_request_requires_new_run_and_fences_old_owner(operator, job):
    first = services.submit(operator.pk, request(job))
    owner = services.claim(first.pk)
    assert owner
    assert services.reserve(first.pk, owner) == ("C", "123", "Fixture City", BOUNDARY, job.profile)
    first.refresh_from_db()
    assert (first.request_state, first.fetched_at, first.batch_id) == ("possible", None, None)
    assert first.reserved_at
    assert services.claim(first.pk) is None
    with pytest.raises(Denied, match="already_reserved"):
        services.reserve(first.pk, owner)

    expire(first, response_budget=True)
    with pytest.raises(Denied, match="stale_owner"):
        services.mark_response(first.pk, owner, clock())
    with pytest.raises(Denied, match="stale_owner"):
        services.record_failure(first.pk, owner, "upstream_denied", action="source", responded=True)
    services.reconcile_due()
    first.refresh_from_db()
    assert (first.status, first.request_state, first.error_code) == (
        "expired",
        "possible",
        "lease_expired",
    )
    retry = ready_for_retry(first)
    assert retry.pk != first.pk
    assert retry.window_id == first.window_id
    assert retry.request_state == "unsent"
    next_owner = services.claim(retry.pk)
    assert next_owner and next_owner != owner
    services.reserve(retry.pk, next_owner)
    services.mark_response(retry.pk, next_owner, clock())
    assert services.claim(first.pk) is None
    with pytest.raises(Denied, match="stale_owner"):
        services.record_failure(first.pk, owner, "upstream_denied", action="source", responded=True)
    retry.refresh_from_db()
    assert retry.generation == first.generation + 1
    assert retry.request_state == "responded"
    assert Run.objects.filter(window_id=first.window_id, reserved_at__isnull=False).count() == 2
    job.product.source.refresh_from_db()
    assert not job.product.source.collection_pause


@pytest.mark.parametrize("phase", ["queued", "claimed", "in_flight"])
def test_v15_withdraw_acquisition_authorization_at_every_access_boundary(operator, job, phase):
    run = services.submit(operator.pk, request(job))
    owner = None
    if phase != "queued":
        owner = services.claim(run.pk)
        assert owner
    if phase == "in_flight":
        services.reserve(run.pk, owner)
    permission = job.product.usegrant_set.get(operation="acquire_online")
    revoke_grant(operator.pk, permission.pk, "withdraw before next access boundary")
    if phase == "queued":
        assert services.claim(run.pk) is None
    elif phase == "claimed":
        with pytest.raises(Denied, match="use_denied"):
            services.reserve(run.pk, owner)
    else:
        with pytest.raises(Denied, match="use_denied"):
            services.mark_response(run.pk, owner, clock())
    run.refresh_from_db()
    assert run.request_state == ("possible" if phase == "in_flight" else "unsent")
    assert (run.reserved_at is not None) == (phase == "in_flight")
    assert run.fetched_at is None
    assert run.batch_id is None
    assert not RunItem.objects.filter(run=run).exists()


def test_v15_revoke_serializes_against_waiting_access_reservation(operator, job):
    run = services.submit(operator.pk, request(job))
    owner = services.claim(run.pk)
    permission = job.product.usegrant_set.get(operation="acquire_online")
    pid = Queue()
    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic():
            Product.objects.select_for_update().get(pk=job.product_id)
            future = pool.submit(in_db_session, pid, lambda: services.reserve(run.pk, owner))
            wait_for_block(pid.get(timeout=5))
            revoke_grant(operator.pk, permission.pk, "withdraw while access reservation waits")
        with pytest.raises(Denied, match="use_denied"):
            future.result(timeout=5)
    run.refresh_from_db()
    assert run.request_state == "unsent"
    assert run.reserved_at is None


@pytest.mark.parametrize("shape", ["L", "S", "T"])
def test_v16_has_to_authorize_actual_batch_scope(operator, source, shape):
    job = make_job(operator, source, shape, expected_codes=None)
    narrow = {
        "L": {"geography": "fixture-city"},
        "S": {"object_code": "station-01"},
        "T": {"data_from": BOUNDARY.date(), "data_until": END.date()},
    }[shape]
    grant(operator, job.product, "acquire_online", **narrow)
    grant(operator, job.product, "process_local")
    grant(operator, job.product, "retain_normalized")
    with pytest.raises(Denied, match="use_denied"):
        services.submit(operator.pk, request(job))
    assert not Run.objects.exists()
    grant(operator, job.product, "acquire_online")
    assert services.submit(operator.pk, request(job)).status == "pending"


def test_v16_source_budget_is_shared_across_products_and_keeps_failed_reservation(operator, job):
    source = job.product.source
    source.collection_limits = LIMITS | {"max_requests": 1}
    source.save(update_fields=["collection_limits"])
    other_job = make_job(operator, source, "L", expected_codes=None)
    for operation in ("acquire_online", "retain_normalized", "process_local"):
        grant(operator, other_job.product, operation)
    first = services.submit(operator.pk, request(job))
    second = services.submit(operator.pk, request(other_job))
    first_owner, second_owner = services.claim(first.pk), services.claim(second.pk)
    assert first_owner and second_owner
    services.reserve(first.pk, first_owner)
    services.record_failure(first.pk, first_owner, "timeout", action="retry")
    with pytest.raises(Denied, match="budget_exhausted"):
        services.reserve(second.pk, second_owner)
    first.refresh_from_db()
    second.refresh_from_db()
    assert first.request_state == "possible"
    assert first.reserved_at and first.next_retry_at
    assert second.request_state == "unsent" and second.reserved_at is None


def city_packet(code="123", name="Fixture City", *, at=BOUNDARY, aqi=43):
    return {
        "Area": name,
        "CityCode": code,
        "TimePoint": at.isoformat(),
        "AQI": aqi,
        "PM2_5": 12,
        "PM10": 23,
        "SO2": 5,
        "NO2": 7,
        "CO": "0.31",
        "O3": 34,
    }


@pytest.fixture
def upstream(monkeypatch):
    """Substitute only the wire response. Domain services and commits use the actual database."""
    calls, responses = [], []

    def fetch(shape, code, name, target, profile):
        calls.append((shape, code, name, target, profile))
        payload = responses.pop(0)
        return json.dumps(payload).encode(), clock()

    monkeypatch.setattr(adapters, "fetch", fetch)
    return calls, responses


def allow_collection(operator, job):
    for operation in ("acquire_online", "process_local", "retain_normalized", "review", "display"):
        grant(operator, job.product, operation)


def committed_facts(run):
    """Count recognizable facts through an independent PostgreSQL connection."""
    with observe_db() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT count(DISTINCT i.id) FILTER (WHERE i.accepted), count(r.id),
                   count(DISTINCT r.batch_id), count(DISTINCT rev.key_id)
            FROM collection_runitem i
            LEFT JOIN observations_receipt r ON r.collection_item_id = i.id
            LEFT JOIN observations_cityrevision rev ON rev.id = r.city_revision_id
            WHERE i.run_id = %s
            """,
            [run.pk],
        )
        return cur.fetchone()


@pytest.mark.parametrize("source_change", ["unchanged", "same_slot_conflict", "newer_slot"])
def test_v10_v14_committed_chunk_survives_and_retry_uses_fresh_batch(
    operator, source, upstream, source_change
):
    codes = [f"{i:03d}" for i in range(1, 201)]
    job = make_job(operator, source, "L", expected_codes=codes)
    cities = City.objects.bulk_create(
        [City(source=source, name=f"Fixture {code}", geography=f"fixture-{code}") for code in codes]
    )
    CityIdentifier.objects.bulk_create(
        [
            CityIdentifier(
                city=city,
                source=source,
                code=code,
                active_period=TimestamptzRange(START, END),
                evidence="F synthetic directory",
            )
            for city, code in zip(cities, codes, strict=True)
        ]
    )
    allow_collection(operator, job)
    calls, responses = upstream
    packet = [
        {
            "Area": f"Fixture {code}",
            "CityCode": code,
            "TimePoint": BOUNDARY.isoformat(),
            "AQI": 43,
            **{f"{name}Level": 1 for name in ("PM2_5", "PM10", "SO2", "NO2", "CO", "O3")},
        }
        for code in codes
    ]
    responses.append(packet)
    first = services.submit(operator.pk, request(job))
    with reject_observation_write(Receipt, "INSERT", "NEW.ordinal=130"):
        services.execute(first.pk)
    first.refresh_from_db()
    assert first.request_state == "responded"
    assert first.status == "failed" and first.error_code == "database_error"
    assert first.batch_id and first.manifest_count == 1400
    assert committed_facts(first) == (128, 128, 1, 128)
    assert CityObservation.objects.filter(product=job.product).count() == 128
    assert not first.window.closed_at
    retained = list(first.items.order_by("id").values_list("id", "item_key"))
    services.execute(first.pk)
    assert len(calls) == 1

    Run.objects.filter(pk=first.pk).update(reserved_at=clock() - timedelta(seconds=2))
    retry = ready_for_retry(first)
    if source_change == "unchanged":
        responses.append(list(reversed(packet)))
    elif source_change == "same_slot_conflict":
        responses.append([row | {"AQI": 55} for row in packet])
    else:
        responses.append(
            [row | {"TimePoint": (BOUNDARY + timedelta(hours=1)).isoformat()} for row in packet]
        )
    services.execute(retry.pk)
    retry.refresh_from_db()
    assert retry.request_state == "responded" and len(calls) == 2
    assert calls[0] == calls[1]
    assert retry.predecessor_id == first.pk and retry.window_id == first.window_id
    assert retry.reserved_at and retry.reserved_at > Run.objects.get(pk=first.pk).reserved_at
    assert committed_facts(first) == (128, 128, 1, 128)
    assert retained == list(first.items.order_by("id").values_list("id", "item_key"))
    coverage = read_window(operator.pk, first.window_id)["coverage"]
    assert coverage["E"] == 1400
    if source_change == "newer_slot":
        assert retry.batch_id and retry.batch_id != first.batch_id
        assert retry.manifest_count == 0
        assert list(
            QualityIssue.objects.filter(batch_id=retry.batch_id).values_list("code", flat=True)
        ) == ["outside_target"]
        assert retry.result == "no_new"
        assert committed_facts(retry) == (0, 0, 0, 0)
        assert coverage["O"] == coverage["V"] == 128
        assert not retry.window.closed_at
    else:
        assert retry.batch_id and retry.batch_id != first.batch_id
        assert committed_facts(retry) == (1272, 1400, 1, 1400)
        assert coverage["O"] == coverage["accepted"] == 1400
        assert retry.window.closed_at
        assert CityObservation.objects.filter(product=job.product).count() == 1400
        conflicts = CityObservation.objects.filter(product=job.product, disputed=True).count()
        if source_change == "unchanged":
            assert conflicts == 0
        else:
            assert conflicts == first.items.filter(indicator="AQI").count() > 0
        assert coverage["V"] == 1400 - conflicts
        services.execute(retry.pk)
        assert len(calls) == 2
    assert (
        not CityObservation.objects.filter(product=job.product)
        .exclude(observed_at_utc=BOUNDARY)
        .exists()
    )


def station_packet(job, source, codes=("A001", "B002")):
    period = TimestamptzRange(START, END)
    packet = []
    for code in codes:
        station = Station.objects.create(source=source, name="Fixture Station")
        StationIdentifier.objects.create(
            source=source, station=station, code=code, active_period=period, evidence="F"
        )
        SiteEpoch.objects.create(
            station=station,
            city=job.city,
            active_period=period,
            name="Fixture Station",
            location_evidence="F",
            certainty="confirmed",
        )
        packet.append(city_packet() | {"StationCode": code, "PositionName": "Fixture Station"})
    return packet


@pytest.mark.parametrize("known", [True, False])
def test_v13_two_station_roster_is_not_inferred_from_response(operator, source, upstream, known):
    job = make_job(operator, source, "S", expected_codes=["A001", "B002"] if known else None)
    packet = station_packet(job, source)
    packet[1]["CO"] = None
    del packet[1]["SO2"]
    allow_collection(operator, job)
    calls, responses = upstream
    responses.append(packet)
    run = services.submit(operator.pk, request(job))
    services.execute(run.pk)
    services.execute(run.pk)
    assert len(calls) == 1
    output = read_window(operator.pk, run.window_id)
    coverage = output["coverage"]
    assert {k: coverage[k] for k in ("E", "O", "V")} == {
        "E": 14 if known else None,
        "O": 13,
        "V": 12,
    }
    assert coverage["object_slots"] == {"E": 2 if known else None, "O": 2}
    assert coverage["by_indicator"]["CO"] == {"E": 2 if known else None, "O": 2, "V": 1}
    assert coverage["by_indicator"]["SO2"] == {"E": 2 if known else None, "O": 1, "V": 1}
    assert coverage["received_ratio"] == (13 / 14 if known else None)
    assert coverage["valid_ratio"] == (12 / 14 if known else None)
    assert (
        read_window(operator.pk, run.window_id, {"indicator": "COLevel"})["state"]
        == "not_applicable"
    )
    assert StationObservation.objects.filter(product=job.product).count() == 13


def test_v18_closed_window_coalesces_and_independent_review_updates_qualification(
    operator, job, upstream
):
    calls, responses = upstream
    responses.append(city_packet())
    first = services.submit(operator.pk, request(job))
    services.execute(first.pk)
    first.refresh_from_db()
    assert first.status == "finished"
    finished = first.window.closed_at
    accepted = tuple(first.items.order_by("id").values_list("id", flat=True))
    assert finished and len(accepted) == 7

    repeated = services.submit(operator.pk, request(job))
    services.execute(repeated.pk)
    assert repeated.status == "coalesced" and len(calls) == 1

    prior = first
    for aqi in (43, 76, 43):
        Run.objects.filter(pk=prior.pk).update(reserved_at=clock() - timedelta(seconds=2))
        responses.append(city_packet(aqi=aqi))
        review = services.submit(operator.pk, request(job, reason="review"))
        services.execute(review.pk)
        review.refresh_from_db()
        assert review.status == "finished" and review.batch_id not in (None, prior.batch_id)
        assert not review.items.filter(accepted=True).exists()
        assert review.window.closed_at == finished
        prior = review
    assert len(calls) == 4
    key = CityObservation.objects.get(product=job.product, indicator="AQI")
    assert key.disputed and key.watermark is None
    assert key.revisions.count() == 2
    assert key.revisions.get(is_current=True).value == 43
    assert key.revisions.get(candidate=True).value == 76
    assert tuple(first.items.order_by("id").values_list("id", flat=True)) == accepted
    assert first.items.filter(accepted=True).count() == 7
    assert read_window(operator.pk, first.window_id)["coverage"]["V"] == 6


def test_v14_latest_only_historical_backfill_is_a_gap_without_network(operator, job, upstream):
    job.profile = "f-latest-v1"
    job.save(update_fields=["profile"])
    historical = services.submit(operator.pk, request(job, reason="backfill"))
    services.execute(historical.pk)
    assert historical.error_code == "backfill_unavailable"
    assert historical.request_state == "unsent"
    assert not upstream[0]
    coverage = read_window(operator.pk, historical.window_id)["coverage"]
    assert {k: coverage[k] for k in ("E", "O", "V")} == {"E": 7, "O": 0, "V": 0}


def test_v17_display_withdrawal_and_retention_disposition_have_different_effects(
    operator, job, upstream
):
    calls, responses = upstream
    detail = "F optional source text unfaltering-shoal"
    responses.append(city_packet() | {"Quality": detail, "AqiLevel": 1})
    first = services.submit(operator.pk, request(job))
    services.execute(first.pk)
    first.refresh_from_db()
    batch_id = first.batch_id
    assert batch_id and first.window.closed_at
    assert (
        Receipt.objects.get(
            batch_id=batch_id, collection_item__indicator="AQI"
        ).collection_metadata["source_description"]
        == detail
    )
    assert read_window(operator.pk, first.window_id)["coverage"]["V"] == 7

    display = job.product.usegrant_set.get(operation="display")
    revoke_grant(operator.pk, display.pk, "withdraw display only")
    dispose_due(job.product_id)
    assert Batch.objects.filter(pk=batch_id).exists()
    assert first.items.filter(accepted=True).count() == 7
    assert read_window(operator.pk, first.window_id)["coverage"]["V"] == 7
    with pytest.raises(Denied, match="use_denied"):
        read_current(operator.pk, selector(job.product, job.city, at=BOUNDARY))

    broad = job.product.usegrant_set.get(operation="retain_normalized")
    narrower = grant(
        operator,
        job.product,
        "retain_normalized",
        fields=["normalized", "provenance", "collection_ledger"],
    )
    revoke_grant(operator.pk, broad.pk, "withdraw optional fields")
    dispose_due(job.product_id)
    assert Batch.objects.filter(pk=batch_id).exists()
    assert first.items.filter(accepted=True).count() == 7
    assert not Receipt.objects.filter(batch_id=batch_id).exclude(collection_metadata=None).exists()
    assert (
        not CityRevision.objects.filter(key__product=job.product).exclude(raw_value=None).exists()
    )
    assert read_window(operator.pk, first.window_id)["coverage"]["V"] == 7

    revoke_grant(operator.pk, narrower.pk, "withdraw normalized retention")
    dispose_due(job.product_id)
    first.refresh_from_db()
    assert first.window.disposed and first.window.expected is None
    assert first.manifest_count is None and first.batch_id is None
    assert not first.items.exists()
    assert not Batch.objects.filter(pk=batch_id).exists()
    assert not Receipt.objects.filter(batch_id=batch_id).exists()
    assert not CityObservation.objects.filter(product=job.product).exists()
    with pytest.raises(Denied, match="use_denied"):
        read_window(operator.pk, first.window_id)
    grant(operator, job.product, "retain_normalized")
    assert read_window(operator.pk, first.window_id) == {
        "window_id": first.window_id,
        "state": "disposed",
        "coverage": None,
    }
    services.execute(first.pk)
    with pytest.raises(Denied, match="window_disposed"):
        services.submit(operator.pk, request(job, reason="backfill"))
    assert len(calls) == 1


def test_l2_01_resume_is_limited_to_the_recorded_scope(operator, source, job):
    national = make_job(operator, source, "L", expected_codes=None)
    allow_collection(operator, national)
    services.change_job(operator.pk, national.pk, "pause")
    Source.objects.filter(pk=source.pk).update(
        collection_pause="upstream_rate_limited",
        collection_resume_after=clock() + timedelta(minutes=1),
    )
    Product.objects.filter(pk=national.product_id).update(collection_pause="invalid_response")

    resumed = services.change_job(operator.pk, national.pk, "resume")
    assert resumed.enabled and not resumed.pause_reason
    national.product.refresh_from_db()
    source.refresh_from_db()
    assert national.product.collection_pause == "invalid_response"
    assert source.collection_pause == "upstream_rate_limited"
    with pytest.raises(Denied, match="collection_paused"):
        services.submit(operator.pk, request(job))
    with pytest.raises(Denied, match="resume_too_early"):
        services.change_job(operator.pk, national.pk, "resume_source")

    Source.objects.filter(pk=source.pk).update(collection_resume_after=clock())
    services.change_job(operator.pk, national.pk, "resume_source")
    national.product.refresh_from_db()
    source.refresh_from_db()
    assert not source.collection_pause and source.collection_resume_after is None
    assert national.product.collection_pause == "invalid_response"
    assert services.submit(operator.pk, request(job)).status == "pending"
    services.change_job(operator.pk, national.pk, "resume_product")
    national.product.refresh_from_db()
    assert not national.product.collection_pause
    assert list(ControlEvent.objects.order_by("id").values_list("action", flat=True)) == [
        "pause",
        "resume",
        "resume_source",
        "resume_product",
    ]


def test_l2_02_grant_expiring_during_budget_check_cannot_open_http(
    operator, job, upstream, monkeypatch
):
    permission = job.product.usegrant_set.get(operation="acquire_online")
    expires = clock() + timedelta(seconds=2)
    type(permission).objects.filter(pk=permission.pk).update(valid_until=expires)
    real_budget = services.budget
    checks = []

    def wait_for_expiry(job, caps, now):
        assert now < expires
        real_budget(job, caps, now)
        wait_for_pg_time(expires)
        checks.append(now)

    monkeypatch.setattr(services, "budget", wait_for_expiry)
    run = services.submit(operator.pk, request(job))
    services.execute(run.pk)
    run.refresh_from_db()
    assert len(checks) == 1 and upstream[0] == []
    assert (run.status, run.request_state, run.error_code) == ("paused", "unsent", "use_denied")
    assert run.reserved_at is None and run.batch_id is None
    assert run.window.owner is None


@pytest.mark.parametrize("stage", ["queued", "manifest"])
def test_l2_03_disposition_erases_roster_and_manifest_even_without_batch(
    operator, job, upstream, stage
):
    run = services.submit(operator.pk, request(job))
    assert len(run.window.expected) == 7
    if stage == "manifest":
        owner = services.claim(run.pk)
        services.reserve(run.pk, owner)
        fetched = clock()
        services.mark_response(run.pk, owner, fetched)
        parsed = adapters.parse(
            "C",
            json.dumps(city_packet()).encode(),
            fetched,
            start=BOUNDARY,
            end=BOUNDARY + timedelta(hours=1),
            city_code="123",
            city_name="Fixture City",
            source_timezone=job.product.source_timezone,
        )
        assert len(writer.manifest(run.pk, owner, parsed)) == 7
        run.refresh_from_db()
        assert run.manifest_count == 7
    assert run.batch_id is None

    g = job.product.usegrant_set.get(operation="retain_normalized")
    revoke_grant(operator.pk, g.pk, "withdraw collection evidence retention before Batch")
    dispose_due(job.product_id)
    run.refresh_from_db()
    assert run.window.disposed and run.window.expected is None and not run.window.roster_basis
    assert not run.window.owner and run.status == "paused"
    assert run.manifest_count is None and run.fetched_at is None and run.batch_id is None
    assert not run.items.exists()
    grant(operator, job.product, "retain_normalized")
    assert read_window(operator.pk, run.window_id)["coverage"] is None
    services.execute(run.pk)
    assert upstream[0] == []


def test_l2_04_backfill_only_on_retrievable_closed_window_with_a_gap(operator, job, upstream):
    calls, responses = upstream
    partial_packet = city_packet()
    del partial_packet["O3"]
    responses.append(partial_packet)
    first = services.submit(operator.pk, request(job))
    services.execute(first.pk)
    first.refresh_from_db()
    assert first.status == "finished" and not first.window.closed_at
    accepted = set(first.items.filter(accepted=True).values_list("id", flat=True))
    assert len(accepted) == 6
    # Reproduce a previously closed window whose stored evidence has a gap.
    closed = clock()
    WorkWindow.objects.filter(pk=first.window_id).update(closed_at=closed)
    Run.objects.filter(pk=first.pk).update(reserved_at=closed - timedelta(seconds=2))
    ordinary = services.submit(operator.pk, request(job))
    assert ordinary.status == "coalesced"
    services.execute(ordinary.pk)
    assert len(calls) == 1

    responses.append(city_packet())
    backfill = services.submit(operator.pk, request(job, reason="backfill"))
    assert backfill.status == "pending"
    services.execute(backfill.pk)
    backfill.refresh_from_db()
    assert backfill.status == "finished" and backfill.window.closed_at == closed
    assert set(first.items.filter(accepted=True).values_list("id", flat=True)) == accepted
    assert list(backfill.items.filter(accepted=True).values_list("indicator", flat=True)) == ["O3"]
    assert read_window(operator.pk, backfill.window_id)["coverage"]["V"] == 7
    second_backfill = services.submit(operator.pk, request(job, reason="backfill"))
    assert second_backfill.status == "coalesced"
    services.execute(second_backfill.pk)
    assert len(calls) == 2


def test_l2_05_admin_redelivery_only_republishes_exhausted_unsent_run(
    operator, job, upstream, published, django_user_model
):
    run = services.submit(operator.pk, request(job))
    Run.objects.filter(pk=run.pk).update(deliveries=services.MAX_DELIVERIES, dispatch_due=clock())
    services.reconcile_due()
    run.refresh_from_db()
    assert (run.dispatch, run.dispatch_due) == ("manual", None)
    viewer = django_user_model.objects.create_user("redelivery-without-permission", is_staff=True)
    with pytest.raises(Denied, match="permission_denied"):
        services.redeliver(viewer.pk, run.pk)
    assert len(published) == 1
    replay = services.redeliver(operator.pk, run.pk)
    assert replay.pk == run.pk and Run.objects.count() == 1
    assert published == [str(run.pk)] * 2
    assert (replay.dispatch, replay.deliveries, replay.request_state) == ("pending", 0, "unsent")
    assert ControlEvent.objects.filter(run=run, actor=operator, action="redeliver").count() == 1

    upstream[1].append(city_packet())
    services.execute(run.pk)
    run.refresh_from_db()
    assert run.status == "finished" and run.request_state == "responded"
    with pytest.raises(Denied, match="invalid_redelivery"):
        services.redeliver(operator.pk, run.pk)
    assert len(published) == 2 and len(upstream[0]) == 1
    assert Run.objects.filter(reserved_at__isnull=False).count() == 1


def test_l2_06_whole_packet_acquisition_and_selective_processing_are_separate(
    operator, source, upstream
):
    job = make_job(operator, source, "L")
    for code in ("123", "987"):
        city = City.objects.create(source=source, name=f"F city {code}", geography=f"f-{code}")
        CityIdentifier.objects.create(
            city=city,
            source=source,
            code=code,
            active_period=TimestamptzRange(START, END),
            evidence="F roster",
        )
    for operation in ("acquire_online", "process_local", "retain_normalized", "review"):
        grant(operator, job.product, operation, geography="f-123", object_code="123")
    with pytest.raises(Denied, match="use_denied"):
        services.submit(operator.pk, request(job))
    assert Run.objects.count() == 0 and upstream[0] == []

    grant(operator, job.product, "acquire_online")
    upstream[1].append(
        [
            {
                "Area": f"F city {code}",
                "CityCode": code,
                "TimePoint": BOUNDARY.isoformat(),
                "AQI": 43,
                **{f"{name}Level": 1 for name in ("PM2_5", "PM10", "SO2", "NO2", "CO", "O3")},
            }
            for code in ("123", "987")
        ]
    )
    run = services.submit(operator.pk, request(job))
    services.execute(run.pk)
    run.refresh_from_db()
    assert run.status == "finished" and run.batch_id and run.reserved_at
    assert run.manifest_count == 7 and len(upstream[0]) == 1
    assert set(run.items.values_list("object_code", flat=True)) == {"123"}
    assert not QualityIssue.objects.filter(batch_id=run.batch_id).exists()
    assert set(
        CityObservation.objects.filter(product=job.product).values_list(
            "city__geography", flat=True
        )
    ) == {"f-123"}
    assert read_window(operator.pk, run.window_id)["coverage"]["V"] == 7


def wait_for_pg_time(boundary):
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_sleep(GREATEST(0, EXTRACT(EPOCH FROM (%s::timestamptz - clock_timestamp()))))",
            [boundary],
        )
    assert clock() >= boundary


def test_l2_followup_02_newly_effective_quota_rechecks_prior_reservation(
    operator, job, upstream, monkeypatch
):
    calls, packets = upstream
    packets.append(city_packet())
    first = services.submit(operator.pk, request(job))
    services.execute(first.pk)
    first.refresh_from_db()
    assert first.status == "finished" and len(calls) == 1
    Run.objects.filter(pk=first.pk).update(reserved_at=clock() - timedelta(seconds=2))

    new_starts = clock() + timedelta(seconds=2)
    grant(
        operator,
        job.product,
        "acquire_online",
        valid_from=new_starts,
        collection_limits=LIMITS | {"max_requests": 1},
    )
    second = services.submit(operator.pk, request(job, target=BOUNDARY + timedelta(hours=1)))
    real_budget = services.budget
    checked = []

    def activate_after_budget(job, caps, now):
        if not checked:
            assert now < new_starts
            real_budget(job, caps, now)
            checked.append(now)
            wait_for_pg_time(new_starts)
        else:
            real_budget(job, caps, now)

    monkeypatch.setattr(services, "budget", activate_after_budget)
    packets.append(city_packet(at=BOUNDARY + timedelta(hours=1)))
    services.execute(second.pk)
    second.refresh_from_db()
    assert checked and len(calls) == 1
    assert second.request_state == "unsent" and second.reserved_at is None
    assert second.batch_id is None and not second.items.exists()
    assert Run.objects.filter(reserved_at__isnull=False).count() == 1


def test_l2_followup_02_optional_metadata_expiry_during_final_tally(
    operator, job, upstream, monkeypatch
):
    broad = job.product.usegrant_set.get(operation="retain_normalized")
    grant(
        operator,
        job.product,
        "retain_normalized",
        fields=["normalized", "provenance", "collection_ledger"],
    )
    revoke_grant(operator.pk, broad.pk, "replace broad evidence grant")
    expires = clock() + timedelta(seconds=2)
    metadata_fields = {"source_description", "primary_pollutant", "source_grade"}
    grant(
        operator,
        job.product,
        "retain_normalized",
        fields=sorted(metadata_fields),
        valid_until=expires,
        expiry_mode="recoverable",
        indicator="AQI",
    )
    run = services.submit(operator.pk, request(job))
    upstream[1].append(
        city_packet() | {"Quality": "F metadata", "AqiLevel": 2, "PrimaryPollutant": "PM2.5"}
    )
    real_tally = queries.tally
    checked = []

    def expire_during_tally(window, *args):
        summary = real_tally(window, *args)
        if not checked:
            before = Receipt.objects.get(
                collection_item__run=run, collection_item__indicator="AQI"
            ).collection_metadata
            assert before is not None and set(before) == metadata_fields
            assert clock() < expires
            checked.append(before)
            wait_for_pg_time(expires)
        return summary

    monkeypatch.setattr(queries, "tally", expire_during_tally)
    services.execute(run.pk)
    run.refresh_from_db()
    assert len(checked) == len(upstream[0]) == 1
    receipts = Receipt.objects.filter(collection_item__run=run)
    for details in receipts.values_list("collection_metadata", flat=True):
        assert not (set(details or {}) & metadata_fields)
    if run.status == "finished":
        assert receipts.count() == 7 and run.items.filter(accepted=True).count() == 7
    else:
        assert not receipts.exists() and not run.items.exists()
        assert not run.window.closed_at and not run.batch_id
        assert not CityObservation.objects.filter(product=job.product).exists()


def test_l2_followup_03_partial_station_withdrawal_preserves_other_station(
    operator, source, upstream
):
    job = make_job(operator, source, "S", expected_codes=["A001", "B002"])
    packet = station_packet(job, source)
    packet[0]["Quality"] = "F station A only marker"
    allow_collection(operator, job)
    upstream[1].append(packet)
    run = services.submit(operator.pk, request(job))
    services.execute(run.pk)
    run.refresh_from_db()
    batch_id = run.batch_id
    assert run.window.closed_at and run.manifest_count == 14
    assert read_window(operator.pk, run.window_id)["coverage"]["V"] == 14
    original_b = set(
        run.items.filter(object_code="B002", accepted=True).values_list("pk", flat=True)
    )
    assert len(original_b) == 7

    broad = job.product.usegrant_set.get(operation="retain_normalized")
    grant(
        operator,
        job.product,
        "retain_normalized",
        object_code="B002",
        fields=["normalized", "provenance", "collection_ledger"],
    )
    revoke_grant(operator.pk, broad.pk, "withdraw station A evidence and whole packet provenance")
    dispose_due(job.product_id)
    run.refresh_from_db()
    assert run.manifest_count is None and run.fetched_at is None
    assert run.batch_id == batch_id
    assert not run.items.filter(object_code="A001").exists()
    assert (
        set(run.items.filter(object_code="B002", accepted=True).values_list("pk", flat=True))
        == original_b
    )
    assert (
        set(Receipt.objects.filter(batch_id=batch_id).values_list("collection_item_id", flat=True))
        == original_b
    )
    assert not Receipt.objects.filter(batch_id=batch_id).exclude(collection_metadata=None).exists()
    assert StationObservation.objects.filter(product=job.product).count() == 7
    current_b = read_window(operator.pk, run.window_id, {"object_code": "B002"})
    assert (
        current_b["coverage"]["O"]
        == current_b["coverage"]["V"]
        == current_b["coverage"]["accepted"]
        == 7
    )
    assert current_b["coverage"]["E"] is None
    assert {cell["object_code"] for cell in current_b["cells"]} == {"B002"}
    assert current_b["state"] == "disposed" and current_b["runs"] == []
    assert {item["id"] for item in current_b["items"] if item["accepted"]} == original_b
    assert {item["batch_id"] for item in current_b["items"]} == {batch_id}
    assert {receipt["item_id"] for receipt in current_b["receipts"]} == original_b
    assert {receipt["batch_id"] for receipt in current_b["receipts"]} == {batch_id}
    assert all("metadata" not in receipt for receipt in current_b["receipts"])
    with pytest.raises(Denied, match="use_denied"):
        read_window(operator.pk, run.window_id)
    with pytest.raises(Denied, match="use_denied"):
        read_window(operator.pk, run.window_id, {"object_code": "A001"})

    grant(operator, job.product, "retain_normalized")
    all_visible = read_window(operator.pk, run.window_id)
    assert all_visible["coverage"]["V"] == 7 and all_visible["coverage"]["E"] is None
    assert all_visible["runs"] == [] and "A001" not in json.dumps(all_visible, default=str)
    services.execute(run.pk)
    with pytest.raises(Denied, match="window_disposed"):
        services.submit(operator.pk, request(job, reason="backfill"))
    assert len(upstream[0]) == 1 and not run.items.filter(object_code="A001").exists()


def test_disposed_empty_station_does_not_reveal_other_survivors(operator, source, upstream):
    codes = ["A001", "B002", "C003"]
    job = make_job(operator, source, "S", expected_codes=codes)
    upstream[1].append(station_packet(job, source, codes)[:2])  # C exists only in the roster.
    allow_collection(operator, job)
    run = services.submit(operator.pk, request(job))
    services.execute(run.pk)
    broad = job.product.usegrant_set.get(operation="retain_normalized")
    b = grant(operator, job.product, "retain_normalized", object_code="B002")
    grant(operator, job.product, "retain_normalized", object_code="C003")
    revoke_grant(operator.pk, broad.pk, "withdraw A")
    dispose_due(job.product_id)
    assert run.items.filter(object_code="B002", accepted=True).count() == 7
    projection = read_window(operator.pk, run.window_id, {"object_code": "C003"})
    assert projection == {"window_id": run.window_id, "state": "disposed", "coverage": None}
    revoke_grant(operator.pk, b.pk, "withdraw B")
    dispose_due(job.product_id)
    assert not run.items.exists()
    assert read_window(operator.pk, run.window_id, {"object_code": "C003"}) == projection


@pytest.mark.parametrize("filled_before_claim", [False, True])
def test_l2_followup_04_closed_backfill_successor_checks_gap_and_original_role(
    operator, job, upstream, monkeypatch, django_user_model, filled_before_claim
):
    calls, packets = upstream
    only_collection = django_user_model.objects.create_user(
        "backfill-without-review", is_staff=True
    )
    only_collection.user_permissions.add(
        Permission.objects.get(content_type__app_label="observations", codename="run_collection")
    )
    assert not only_collection.has_perm("observations.review_observation")
    partial = city_packet()
    del partial["O3"]
    packets.append(partial)
    first = services.submit(operator.pk, request(job))
    services.execute(first.pk)
    first.refresh_from_db()
    assert len(calls) == 1 and first.items.filter(accepted=True).count() == 6
    # Reproduce a closed historical window with one missing cell.
    closed_at = clock()
    WorkWindow.objects.filter(pk=first.window_id).update(closed_at=closed_at)
    Run.objects.filter(pk=first.pk).update(reserved_at=closed_at - timedelta(seconds=2))

    capture = adapters.fetch

    def timeout(*args):
        calls.append(args)
        raise adapters.WireError("timeout")

    backfill = services.submit(only_collection.pk, request(job, reason="backfill"))
    with monkeypatch.context() as injected:
        injected.setattr(adapters, "fetch", timeout)
        services.execute(backfill.pk)
    backfill.refresh_from_db()
    assert backfill.error_code == "timeout" and backfill.next_retry_at and len(calls) == 2
    Run.objects.filter(pk=backfill.pk).update(
        reserved_at=clock() - timedelta(seconds=2), occupied_until=clock()
    )
    successor = ready_for_retry(backfill)
    assert successor.status == "pending" and successor.actor_id == only_collection.pk
    packets.append(city_packet())
    if filled_before_claim:
        newer = services.submit(operator.pk, request(job, reason="backfill"))
        services.execute(newer.pk)
    assert adapters.fetch is capture
    services.execute(successor.pk)
    successor.refresh_from_db()
    assert len(calls) == 3 and successor.window.closed_at == closed_at
    if filled_before_claim:
        assert successor.status == "coalesced" and successor.reserved_at is None
        assert successor.request_state == "unsent"
    else:
        assert successor.status == "finished" and successor.request_state == "responded"
        assert list(successor.items.filter(accepted=True).values_list("indicator", flat=True)) == [
            "O3"
        ]
    assert read_window(operator.pk, first.window_id)["coverage"]["V"] == 7


def test_l2_followup_06_t_needs_processing_and_retention_only_for_aqi(operator, source, upstream):
    job = make_job(operator, source, "T")
    grant(operator, job.product, "acquire_online")
    for operation in ("process_local", "retain_normalized", "review"):
        grant(operator, job.product, operation, indicator="AQI")
    upstream[1].append([city_packet()])
    run = services.submit(operator.pk, request(job))
    services.execute(run.pk)
    run.refresh_from_db()
    assert len(upstream[0]) == 1 and run.status == "finished"
    assert set(
        CityObservation.objects.filter(product=job.product).values_list("indicator", flat=True)
    ) == {"AQI"}
    coverage = read_window(operator.pk, run.window_id)["coverage"]
    assert coverage["E"] == 24 and coverage["O"] == coverage["V"] == 1


@pytest.mark.parametrize("response", [401, 403, "bad_certificate"])
def test_v12_real_refusal_pauses_source(response, operator, job, monkeypatch):
    hits = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            hits.append(self.path)
            self.send_response(response)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args):
            pass

    with (
        tempfile.TemporaryDirectory() as ram,
        ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server,
    ):
        tls = response == "bad_certificate"
        if tls:
            key, cert = [str(Path(ram) / name) for name in ("key", "crt")]
            subprocess.run(
                [
                    "openssl",
                    "req",
                    "-x509",
                    "-newkey",
                    "rsa:2048",
                    "-sha256",
                    "-nodes",
                    "-keyout",
                    key,
                    "-out",
                    cert,
                    "-days",
                    "1",
                    "-subj",
                    "/CN=localhost",
                    "-addext",
                    "subjectAltName=IP:127.0.0.1",
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(cert, key)
            server.socket = ctx.wrap_socket(server.socket, server_side=True)
        # Process-local fault injection, no deployment setting or public origin.
        monkeypatch.setattr(
            transport, "ORIGIN", f"{'https' if tls else 'http'}://127.0.0.1:{server.server_port}"
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            run = services.submit(operator.pk, request(job))
            services.execute(run.pk)
            run.refresh_from_db()
            job.product.source.refresh_from_db()
            code = "transport_security" if tls else "upstream_denied"
            assert (
                run.status == "paused"
                and run.error_code == job.product.source.collection_pause == code
            )
            assert run.request_state == ("possible" if tls else "responded")
            assert run.reserved_at and not run.items.exists() and run.next_retry_at is None
            with pytest.raises(Denied, match="collection_paused"):
                services.submit(operator.pk, request(job))
            services.execute(run.pk)
            assert len(hits) == (0 if tls else 1)
        finally:
            server.shutdown()
            thread.join()
