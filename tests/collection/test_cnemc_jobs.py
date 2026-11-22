"""F and injected failures on real PostgreSQL. No external HTTP transport here."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import date, timedelta
from threading import Barrier
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from collection import services
from collection.adapters import cnemc
from collection.models import ControlEvent, Job, Run, WorkWindow
from collection.recovery import scan_gaps
from collection.registration import install
from django.db import IntegrityError, connections, transaction
from observations.models import CityIdentifier, Product, UseGrant
from observations.policy import Denied, clock

pytestmark = pytest.mark.django_db(transaction=True)
LIMITS = dict(min_interval_seconds=0, max_concurrent=4, period_seconds=1, max_requests=100)


def manifest(code="station-national-hour", *, grant_total=None):
    station = code.startswith("station-")
    entries = {"F-01": ["AQI"], "F-02": ["AQI"]} if station else {"999999": ["AQI"]}
    grants = []
    for op, aud in (
        ("acquire_online", "platform"),
        ("process_local", "platform"),
        ("retain_normalized", "platform"),
        ("review", "operator"),
        ("display", "local_authenticated"),
    ):
        g = dict(
            operation=op,
            audience=aud,
            geography="*",
            object_code="*",
            indicator="*",
            data_from=date.min.isoformat(),
            data_until=date.max.isoformat(),
            fields=["*"],
            terms="F only",
            terms_version="F-v1",
            valid_from="2024-01-01T00:00:00Z",
        )
        if op == "acquire_online":
            g.update(collection_limits=LIMITS, endpoints=[cnemc.PRODUCTS[code]])
            if grant_total:
                g["max_total_requests"] = grant_total
                g["valid_until"] = (clock() + timedelta(days=1)).isoformat()
        grants.append(g)
    base = {"product": code, "from": "2024-01-01T00:00:00Z", "evidence": "synthetic directory"}
    spec = dict(product=code, executor=1, selection={"mode": "include", "objects": entries})
    if code in {"city-live-hour", "city-trend-hour", "station-city-hour"}:
        spec["city_code"] = "999999"
    if grant_total:
        spec["validation_terms_version"] = "F-v1"
    return dict(
        namespace="cnemc.fixture",
        collection_limits=LIMITS,
        products=[dict(code=code, evidence="F contract", attribution="F only", grants=grants)],
        cities=[dict(base, code="999999", name="F City", geography="F-geo")],
        stations=[dict(base, code=c, name=c, city_code="999999") for c in entries]
        if station
        else [],
        jobs=[spec],
    )


@pytest.fixture
def lab(settings, django_user_model, monkeypatch):
    settings.CNEMC_FIXTURE_HTTP = True
    operator = django_user_model.objects.create_superuser(username="F-controller")
    monkeypatch.setattr(services, "publish", lambda pk: None)

    def seed(config):
        for job in config["jobs"]:
            job["executor"] = operator.pk
        ids = install(operator.pk, config)
        return Job.objects.get(pk=ids["jobs"][0]) if ids["jobs"] else ids

    return operator, seed


def submit(job, user, reason="manual", at=None):
    at = at or clock().replace(minute=0, second=0, microsecond=0)
    return services.submit(
        user.pk, dict(job=job.pk, request_id=str(uuid4()), reason=reason, target=at.isoformat())
    )


def test_registration_idempotent_transactional_and_zero_io(lab, settings):
    operator, seed = lab
    config = manifest("city-live-hour")
    job = seed(config)
    assert not job.enabled and job.next_due_at is None
    assert seed(config).pk == job.pk and UseGrant.objects.count() == 5
    settings.CNEMC_FIXTURE_HTTP = False
    with pytest.raises(Denied, match="unregistered_profile"):
        services.change_job(operator.pk, job.pk, "resume")
    assert not Run.objects.exists()
    settings.CNEMC_FIXTURE_HTTP = True
    wrong = deepcopy(config)
    wrong["cities"] += wrong["cities"]
    with pytest.raises(Denied, match="duplicate_catalog_entry"):
        seed(wrong)
    wrong = deepcopy(config)
    wrong["jobs"][0]["selection"]["objects"]["unknown"] = ["AQI"]
    with pytest.raises(Denied, match="identity_unresolved"):
        seed(wrong)
    assert CityIdentifier.objects.count() == Job.objects.count() == 1


def test_whole_package_endpoint_and_history_authorization(lab):
    operator, seed = lab
    config = manifest()
    job = seed(config)
    acq = UseGrant.objects.get(operation="acquire_online")
    for bad in (
        dict(geography="F-geo"),
        dict(endpoints=[]),
        dict(endpoints=["A10"]),
        dict(data_from=date(2024, 1, 1)),
    ):
        with transaction.atomic():
            UseGrant.objects.filter(pk=acq.pk).update(**bad)
            with pytest.raises(Denied):
                services.change_job(operator.pk, job.pk, "resume")
            transaction.set_rollback(True)
    services.change_job(operator.pk, job.pk, "resume")
    with pytest.raises(Denied, match="backfill_unavailable"):
        submit(job, operator, "backfill")
    old = submit(
        job, operator, at=clock().replace(minute=0, second=0, microsecond=0) - timedelta(hours=5)
    )
    assert old.error_code == "backfill_unavailable" and old.reserved_at is None


def test_nonrenewing_total_across_503_retry_new_uuid_and_rollover(lab, monkeypatch):
    operator, seed = lab
    config = manifest("city-live-hour", grant_total=1)
    # A separate ordinary grant cannot enlarge this validation round.
    broad = dict(config["products"][0]["grants"][0], terms_version="routine")
    del broad["max_total_requests"]
    config["products"][0]["grants"].append(broad)
    job = seed(config)
    services.change_job(operator.pk, job.pk, "resume")
    first = submit(job, operator)
    owner = services.claim(first.pk)
    assert services.reserve(first.pk, owner)[0] == "A03"
    services.record_failure(first.pk, owner, "upstream_transient", action="retry", responded=True)
    first.refresh_from_db()
    assert first.validation_grant_id == job.validation_grant_id
    # Move the retry timer only. The original PG reservation is immutable.
    Run.objects.filter(pk=first.pk).update(next_retry_at=clock() - timedelta(seconds=1))
    services.retry_due(first.pk)
    successor = first.successors.get()
    assert (
        successor.endpoint == first.endpoint
        and successor.validation_grant_id == first.validation_grant_id
    )
    for r in (successor, submit(job, operator)):
        services.execute(r.pk)
        r.refresh_from_db()
        assert r.error_code == "validation_budget_exhausted" and r.reserved_at is None
    assert (
        Run.objects.filter(validation_grant=job.validation_grant, reserved_at__isnull=False).count()
        == 1
    )
    for model, pk, fields in (
        (Run, first.pk, {"endpoint": "A07"}),
        (Run, first.pk, {"reserved_at": None}),
        (UseGrant, job.validation_grant_id, {"max_total_requests": 2}),
    ):
        with pytest.raises(IntegrityError), transaction.atomic():
            model.objects.filter(pk=pk).update(**fields)


def test_concurrent_unknown_denominator_compares_frozen_selection(lab):
    operator, seed = lab
    config = manifest()
    left = seed(config)
    different = deepcopy(config)
    del different["jobs"][0]["selection"]["objects"]["F-01"]
    right = seed(different)
    for job in (left, right):
        services.change_job(operator.pk, job.pk, "resume")
    barrier = Barrier(2)
    at = clock().replace(minute=0, second=0, microsecond=0)

    def competing(job):
        try:
            barrier.wait(timeout=5)
            return submit(job, operator, at=at).pk
        except Denied as exc:
            return exc.code
        finally:
            connections.close_all()

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(competing, (left, right)))
    assert results.count("window_contract_conflict") == 1
    winner = Run.objects.get()
    assert WorkWindow.objects.count() == 1 and winner.window.expected is None
    assert winner.window.selection == winner.job.selection
    duplicate = submit(winner.job, operator, at=at)
    owner = services.claim(winner.pk)
    assert services.reserve(winner.pk, owner)[0] == "A07"
    assert services.claim(duplicate.pk) is None
    assert Run.objects.filter(reserved_at__isnull=False).count() == 1


def test_clock_reconciliation_skips_stale_slots_and_overlapping_schedules(lab):
    operator, seed = lab
    config = manifest()
    now = clock()
    first_due = (now - timedelta(days=10)).replace(minute=25, second=0, microsecond=0)
    config["jobs"][0]["first_due"] = first_due.isoformat()
    job = seed(config)
    services.change_job(operator.pk, job.pk, "resume")
    services.scheduled(job.pk)
    job.refresh_from_db()
    skip = ControlEvent.objects.get(action="skipped_slots")
    assert skip.slot_start == first_due and skip.slot_end - first_due > timedelta(days=9)
    assert job.next_due_at > now and job.next_due_at.minute == 25
    assert Run.objects.count() == 1 and Run.objects.get().scheduled_for == skip.slot_end
    assert not Run.objects.filter(reserved_at__isnull=False).exists() and scan_gaps(job.pk) == 0
    # The registration is repeatable even after the clock advances.
    assert seed(config).pk == job.pk
    other = manifest("station-city-hour")
    other["jobs"][0]["first_due"] = first_due.isoformat()
    with pytest.raises(Denied, match="station_schedule_conflict"):
        seed(other)
    assert not Product.objects.filter(code="station-city-hour").exists()


def test_a07_a10_same_window_finite_gaps_and_zero_on_mixed_hour(lab, monkeypatch):
    import json

    from collection.models import RunItem
    from collection.queries import read_window
    from observations.models import StationObservation

    operator, seed = lab
    config = manifest()
    current = clock().replace(minute=0, second=0, microsecond=0)
    old = current - timedelta(hours=1)
    config["jobs"][0].update(
        expected={"F-01": ["AQI"], "F-02": ["AQI"]},
        roster_basis="F independent manifest",
        backfill_policy={
            "equivalence_basis": "F equivalent hourly schemas",
            "lookback_hours": 2,
            "max_requests_per_scan": 1,
            "max_windows_per_scan": 2,
            "operating_from": old.isoformat(),
        },
        first_due=(current + timedelta(hours=1, minutes=25)).isoformat(),
    )
    config["products"][0]["grants"][0]["endpoints"].append("A10")
    job = seed(config)
    services.change_job(operator.pk, job.pk, "resume")

    def row(code, at, value, exact=False):
        result = dict(
            StationCode=code,
            PositionName=code,
            Area="F City",
            TimePoint=at.astimezone(ZoneInfo(cnemc.TIMEZONE)).isoformat(),
            AQI=value,
        )
        if not exact:
            result["CityCode"] = "999999"
        return result

    packets = [
        [row("F-01", current, 23)],
        [row("F-01", current, 23, True), row("F-02", current, 24, True)],
        [row("F-01", old, 5, True), row("F-02", current, 21, True)],
    ]
    calls = []

    def fetch(endpoint, code, name, target, *, synthetic):
        assert synthetic
        calls.append((endpoint, target))
        return json.dumps(packets.pop(0)).encode(), clock()

    monkeypatch.setattr(cnemc, "fetch", fetch)
    first = submit(job, operator, at=current)
    services.execute(first.pk)
    first.refresh_from_db()
    assert first.result == "partial"
    assert read_window(operator.pk, first.window_id)["coverage"]["O"] == 1
    second = submit(job, operator, at=current, reason="backfill")
    assert second.window_id == first.window_id and second.predecessor_id == first.pk
    services.execute(second.pk)
    c = read_window(operator.pk, first.window_id)["coverage"]
    assert [c[k] for k in ("E", "O", "V")] == [2, 2, 2] and StationObservation.objects.count() == 2
    assert (
        not RunItem.objects.filter(window_id=first.window_id)
        .exclude(accepted=True)
        .exclude(status="reread")
        .exists()
    )
    assert scan_gaps(job.pk) == 1
    gap = Run.objects.get(endpoint="A10", window__target_start=old)
    services.execute(gap.pk)
    gap.refresh_from_db()
    assert gap.error_code == "exact_target_mismatch" and not gap.items.exists()
    assert scan_gaps(job.pk) == 0
    assert calls == [("A07", current), ("A10", current), ("A10", old)]


def test_t_adjacent_acquisition_windows_do_not_transfer_receipts(lab, monkeypatch):
    import json

    from collection.queries import read_window
    from observations.models import CityObservation

    operator, seed = lab
    config = manifest("city-trend-hour")
    config["jobs"][0].update(expected={"999999": ["AQI"]}, roster_basis="F recent hourly products")
    job = seed(config)
    services.change_job(operator.pk, job.pk, "resume")
    current = clock().replace(minute=0, second=0, microsecond=0)
    first_target = current - timedelta(hours=1)
    # Inject two successive publication clocks; all leases, transactions and grants still use PG time.
    wall = [first_target + timedelta(minutes=30)]
    live = services.can_fetch
    monkeypatch.setattr(
        services, "can_fetch", lambda job, end, now, endpoint: live(job, end, wall[0], endpoint)
    )
    calls = []
    hole = current - timedelta(hours=3)

    def fetch(endpoint, code, name, target, *, synthetic):
        calls.append(endpoint)
        times = [target - timedelta(hours=i) for i in range(24)]
        if target == first_target:
            times.remove(hole)
        return json.dumps(
            [
                dict(
                    CityCode=code,
                    Area=name,
                    TimePoint=t.astimezone(ZoneInfo(cnemc.TIMEZONE)).isoformat(),
                    AQI=37,
                )
                for t in times
            ]
        ).encode(), clock()

    monkeypatch.setattr(cnemc, "fetch", fetch)
    runs = []
    for at in (first_target, current):
        wall[0] = at + timedelta(minutes=30)
        run = submit(job, operator, at=at)
        services.execute(run.pk)
        runs.append(run)
    assert calls == ["A05", "A05"] and CityObservation.objects.count() == 25
    assert not runs[0].items.filter(observed_at=hole).exists()
    assert runs[1].items.filter(observed_at=hole, accepted=True).exists()
    for run, value in zip(runs, (23, 24), strict=True):
        c = read_window(operator.pk, run.window_id)["coverage"]
        assert (c["E"], c["O"], c["V"]) == (24, value, value)


def test_processing_selection_can_exceed_independent_roster(lab, monkeypatch):
    import json

    from collection.queries import read_window
    from observations.models import StationObservation

    operator, seed = lab
    config = manifest()
    config["jobs"][0].update(expected={"F-01": ["AQI"]}, roster_basis="F documented station")
    job = seed(config)
    services.change_job(operator.pk, job.pk, "resume")
    at = clock().replace(minute=0, second=0, microsecond=0)
    extra = dict(
        StationCode="F-02",
        PositionName="F-02",
        CityCode="999999",
        Area="F City",
        TimePoint=at.astimezone(ZoneInfo(cnemc.TIMEZONE)).isoformat(),
        AQI=33,
    )
    monkeypatch.setattr(
        cnemc, "fetch", lambda *args, **kwargs: (json.dumps([extra]).encode(), clock())
    )
    run = submit(job, operator, at=at)
    services.execute(run.pk)
    window = read_window(operator.pk, run.window_id)
    assert [window["coverage"][k] for k in ("E", "O", "V")] == [1, 0, 0]
    assert window["state"] == "open"
    assert [
        (c["object_code"], c["expected"], c["valid"])
        for c in sorted(window["cells"], key=lambda c: c["object_code"])
    ] == [("F-01", True, False), ("F-02", False, True)]
    from observations.queries import read_current

    observation = StationObservation.objects.get(product=job.product)
    assert observation.object_code == "F-02"
    result = read_current(
        operator.pk,
        {
            "product": str(job.product_id),
            "scope": "synthetic",
            "site_epoch": str(observation.site_epoch_id),
            "report_kind": "hourly",
            "indicator": "AQI",
            "window_definition": "hourly_label_unspecified",
            "observed_at": at.isoformat(),
            "standard_series": "unknown",
        },
    )
    assert (
        result["count"] == 1
        and result["results"][0]["revision_id"] == observation.revisions.get(is_current=True).pk
    )


def test_split_manifests_sharing_validation_ceiling_cannot_reallocate_spent_shares(lab):
    from observations.policy import revoke_grant

    operator, seed = lab
    first = manifest("city-live-hour", grant_total=1)
    second = manifest("city-trend-hour", grant_total=1)
    allocation = {"basis": "F two requests across the source", "total": 2}
    for config in (first, second):
        config["validation_allocation"] = allocation
    a, b = seed(first), seed(second)
    assert seed(first).pk == a.pk and seed(second).pk == b.pk
    revoke_grant(operator.pk, a.validation_grant_id, "F partial withdrawal")
    third = manifest("city-list-hour", grant_total=1)
    for basis in (allocation, {**allocation, "total": 3}, {"basis": "amended label", "total": 3}):
        third["validation_allocation"] = basis
        with pytest.raises(Denied, match="invalid_validation_allocation"):
            seed(third)
    assert not Product.objects.filter(code="city-list-hour").exists()


def test_withdraw_validation_ledger_seals_allocation_and_erases_request_links(lab, monkeypatch):
    import json

    from collection.queries import read_window
    from observations.disposition import dispose_due
    from observations.models import CityObservation
    from observations.policy import register_grant, revoke_grant

    operator, seed = lab
    config = manifest("city-live-hour", grant_total=3)
    grants = config["products"][0]["grants"]
    data = next(g for g in grants if g["operation"] == "retain_normalized")
    ledger = dict(data, terms_version="F-quota", fields=["validation_ledger"])
    data["fields"] = ["catalog", "collection_ledger", "normalized", "provenance"]
    grants.append(ledger)
    job = seed(config)
    services.change_job(operator.pk, job.pk, "resume")
    at = clock().replace(minute=0, second=0, microsecond=0)
    row = dict(
        CityCode="999999",
        Area="F City",
        TimePoint=at.astimezone(ZoneInfo(cnemc.TIMEZONE)).isoformat(),
        AQI=33,
    )
    monkeypatch.setattr(cnemc, "fetch", lambda *args, **kwargs: (json.dumps(row).encode(), clock()))
    run = submit(job, operator, at=at)
    services.execute(run.pk)
    grant_id = job.validation_grant_id
    retained = job.product.usegrant_set.get(terms_version="F-quota")
    revoke_grant(operator.pk, retained.pk, "F withdraw validation accounting only")
    with pytest.raises(Denied):
        submit(job, operator, at=at)
    dispose_due(job.product_id)
    run.refresh_from_db()
    job.refresh_from_db()
    assert job.disposed and job.validation_grant_id is None and not job.enabled
    assert run.validation_grant_id is run.reserved_at is run.occupied_until is None
    assert run.request_state == "disposed" and run.status == "finished"
    services.execute(run.pk)  # A delayed broker delivery observes the terminal Run.
    assert read_window(operator.pk, run.window_id)["coverage"]["V"] == 1
    assert (
        CityObservation.objects.get(product=job.product).revisions.get(is_current=True).value == 33
    )
    closed = UseGrant.objects.get(pk=grant_id)
    assert closed.validation_disabled and closed.revoked_at is None
    source = job.product.source
    source.refresh_from_db()
    assert (
        source.collection_pause == "validation_ledger_disposed"
        and source.collection_resume_after > clock()
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        UseGrant.objects.filter(pk=grant_id).update(validation_disabled=False)
    with pytest.raises(IntegrityError), transaction.atomic():
        Run.objects.filter(pk=run.pk).update(request_state="unsent")
    # Restoring legal storage cannot reopen the old acquisition grant or reuse the spent shares.
    register_grant(
        operator.pk,
        job.product_id,
        operation="retain_normalized",
        audience="platform",
        fields=["validation_ledger"],
        geography="*",
        object_code="*",
        indicator="*",
        terms="new ledger authority",
        terms_version="F-restored",
        valid_from=clock(),
        data_from=date.min,
        data_until=date.max,
    )
    with pytest.raises(Denied, match="validation_grant_disabled"):
        seed(config)
