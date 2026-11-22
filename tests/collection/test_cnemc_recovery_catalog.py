"""F station relocation and cross-day recovery on disposable PostgreSQL."""

import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from collection import recovery, services
from collection.adapters import cnemc
from collection.models import Job, Run
from collection.registration import install
from observations.catalog import relocate
from observations.disposition import dispose_due
from observations.models import SiteEpoch, StationIdentifier, StationObservation
from observations.policy import Denied, clock, revoke_grant
from observations.queries import read_current

from tests.collection.test_cnemc_jobs import manifest, submit

pytestmark = pytest.mark.django_db(transaction=True)
HOUR = timedelta(hours=1)
LOCAL = ZoneInfo(cnemc.TIMEZONE)


@pytest.fixture
def station_setup(settings, django_user_model, monkeypatch):
    settings.CNEMC_FIXTURE_HTTP = True
    operator = django_user_model.objects.create_superuser(username="F-station-edge")
    config = manifest()
    config["stations"] = config["stations"][:1]
    spec = config["jobs"][0]
    spec.update(executor=operator.pk, expected={"F-01": ["AQI"]}, roster_basis="F roster")
    spec["selection"]["objects"] = spec["expected"]
    monkeypatch.setattr(services, "publish", lambda pk: None)
    return operator, config


def start_job(operator, config):
    job = Job.objects.get(pk=install(operator.pk, config)["jobs"][0])
    services.change_job(operator.pk, job.pk, "resume")
    return job


def publication(monkeypatch, at, samples):
    # Shift the source publication clock only. PG grants, reservations and leases use real time.
    live = services.can_fetch
    monkeypatch.setattr(
        services, "can_fetch", lambda job, end, now, endpoint: live(job, end, at[0], endpoint)
    )
    monkeypatch.setattr(recovery, "clock", lambda: at[0])
    calls = []

    def fetch(endpoint, code, name, target, *, synthetic):
        assert synthetic
        calls.append((endpoint, target))
        position, value = samples[target]
        row = dict(
            StationCode="F-01",
            PositionName=position,
            Area="F City",
            TimePoint=target.astimezone(LOCAL).isoformat(),
            AQI=value,
        )
        if endpoint == "A07":
            row["CityCode"] = "999999"
        return json.dumps([row]).encode(), clock()

    monkeypatch.setattr(cnemc, "fetch", fetch)
    return calls


def test_disposition_retains_authorized_observations_across_relocation(station_setup, monkeypatch):
    operator, config = station_setup
    job = start_job(operator, config)
    original = SiteEpoch.objects.get()
    before = datetime(2025, 1, 1, 23, tzinfo=LOCAL)
    after = before + HOUR
    at = [before]
    publication(monkeypatch, at, {before: (original.name, 21), after: ("F relocated", 34)})
    services.execute(submit(job, operator, at=before).pk)
    moved = relocate(
        original.station,
        after,
        city=original.city,
        name="F relocated",
        location_evidence="F relocation",
        certainty="documented",
    )
    at[0] = after
    services.execute(submit(job, operator, at=after).pk)
    rows = list(StationObservation.objects.order_by("observed_at_utc"))
    assert [row.site_epoch_id for row in rows] == [original.pk, moved.pk]
    queries = [
        dict(
            product=str(job.product_id),
            scope="synthetic",
            site_epoch=str(row.site_epoch_id),
            indicator="AQI",
            observed_at=row.observed_at_utc.isoformat(),
            report_kind="hourly",
            window_definition=row.window_definition,
            standard_series=row.standard_series,
        )
        for row in rows
    ]
    acquisition = job.product.usegrant_set.get(operation="acquire_online")
    revoke_grant(operator.pk, acquisition.pk, "F stop acquisition")
    dispose_due(job.product_id)
    assert StationIdentifier.objects.filter(code="F-01").count() == 1
    assert [read_current(operator.pk, q)["results"][0]["value"] for q in queries] == [
        "21.000000",
        "34.000000",
    ]

    retention = job.product.usegrant_set.get(operation="retain_normalized")
    revoke_grant(operator.pk, retention.pk, "F withdraw retention")
    dispose_due(job.product_id)
    assert not StationIdentifier.objects.exists() and not SiteEpoch.objects.exists()
    assert not StationObservation.objects.exists()
    for q in queries:
        with pytest.raises(Denied):
            read_current(operator.pk, q)


@pytest.mark.parametrize("scan_limit", [1, 2])
def test_a10_scan_counts_registered_windows_across_local_midnight(
    station_setup, monkeypatch, scan_limit
):
    operator, config = station_setup
    midnight = datetime(2025, 1, 2, tzinfo=LOCAL)
    oldest = midnight - HOUR
    latest = midnight + HOUR
    spec = config["jobs"][0]
    spec.update(
        first_due=(latest + HOUR).isoformat(),
        backfill_policy=dict(
            operating_from=oldest.isoformat(),
            equivalence_basis="F equivalent hourly schemas",
            lookback_hours=2,
            max_windows_per_scan=scan_limit,
            max_requests_per_scan=1,
        ),
    )
    grants = config["products"][0]["grants"]
    grants.append({**grants[0], "endpoints": ["A10"], "data_from": midnight.date().isoformat()})
    job = start_job(operator, config)
    calls = publication(monkeypatch, [latest], {midnight: ("F-01", 23), latest: ("F-01", 24)})
    services.execute(submit(job, operator, at=latest).pk)
    assert StationObservation.objects.filter(observed_at_utc=latest).exists()
    with pytest.raises(Denied):
        submit(job, operator, at=oldest, reason="backfill")

    if scan_limit == 1:
        assert recovery.scan_gaps(job.pk) == 0
        assert not Run.objects.filter(endpoint="A10").exists()
        assert calls == [("A07", latest)]
        job.refresh_from_db()
        assert job.backfill_cursor == midnight
    assert recovery.scan_gaps(job.pk) == 1
    gap = Run.objects.get(endpoint="A10")
    assert gap.window.target_start == midnight
    services.execute(gap.pk)
    assert gap.items.filter(accepted=True, observed_at=midnight).count() == 1
    for _ in range(3):
        assert recovery.scan_gaps(job.pk) == 0
    assert calls == [("A07", latest), ("A10", midnight)]
