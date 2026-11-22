"""CNEMC grants and frozen collection evidence against disposable PostgreSQL."""

import json
from datetime import date, timedelta
from zoneinfo import ZoneInfo

import pytest
from collection import services
from collection.adapters import cnemc
from collection.admission import data_scope
from collection.models import Job, Run
from collection.queries import read_window
from collection.registration import install
from django.core.management import call_command
from django.db import IntegrityError, transaction
from observations.disposition import dispose_due
from observations.models import (
    City,
    CityIdentifier,
    DispositionJob,
    Product,
    QualityIssue,
    Receipt,
    SiteEpoch,
    Station,
    StationIdentifier,
    StationObservation,
)
from observations.policy import REQUIRED, Denied, clock, revoke_grant
from observations.queries import read_current, read_evidence

from tests.collection.test_cnemc_jobs import manifest, submit
from tests.s02_support import allow

pytestmark = pytest.mark.django_db(transaction=True)
LEDGER = REQUIRED | {"collection_ledger"}


@pytest.fixture
def station_job(settings, django_user_model, monkeypatch):
    settings.CNEMC_FIXTURE_HTTP = True
    operator = django_user_model.objects.create_superuser(username="F-disposition")
    monkeypatch.setattr(services, "publish", lambda pk: None)

    def start(*, known):
        config = manifest()
        spec = config["jobs"][0]
        spec["executor"] = operator.pk
        if known:
            spec["expected"] = spec["selection"]["objects"]
            spec["roster_basis"] = "F independent roster"
        job = Job.objects.get(pk=install(operator.pk, config)["jobs"][0])
        services.change_job(operator.pk, job.pk, "resume")
        target = clock().replace(minute=0, second=0, microsecond=0)
        packet = [
            {
                "StationCode": code,
                "CityCode": 999999,
                "Area": "F City",
                "PositionName": code,
                "TimePoint": target.astimezone(ZoneInfo(cnemc.TIMEZONE)).isoformat(),
                "AQI": 45,
                "Quality": f"F optional text {code}",
            }
            for code in spec["selection"]["objects"]
        ]
        monkeypatch.setattr(cnemc, "fetch", lambda *a, **kw: (json.dumps(packet).encode(), clock()))
        run = submit(job, operator, at=target)
        services.execute(run.pk)
        run.refresh_from_db()
        assert run.status == "finished", run.error_code
        assert run.items.filter(accepted=True).count() == 2
        return operator, job, run, target

    return start


def grant(actor, product, operation, **kwargs):
    return allow(
        actor,
        product,
        operation,
        **({"data_from": date.min, "data_until": date.max} | kwargs),
    )


def test_partial_review_of_window_with_unknown_expected(station_job):
    operator, job, run, _ = station_job(known=False)
    wide = job.product.usegrant_set.get(operation="review")
    grant(
        operator, job.product, "review", object_code="F-02", indicator="AQI", fields=sorted(LEDGER)
    )
    revoke_grant(operator.pk, wide.pk, "F narrower review")
    result = read_window(operator.pk, run.window_id)
    coverage = result["coverage"]
    assert (coverage["E"], coverage["O"], coverage["V"]) == (None, 1, 1)
    assert set(coverage["by_indicator"]) == {"AQI"}
    assert not result["runs"] and result["result"] is None
    assert "F-01" not in json.dumps(result, default=str)
    assert set(cell["object_code"] for cell in result["cells"]) == {"F-02"}
    assert len(result["receipts"]) == 1 and "metadata" not in result["receipts"][0]
    with pytest.raises(Denied):
        read_window(operator.pk, run.window_id, {"object_code": "F-01"})
    assert run.items.count() == StationObservation.objects.count() == 2


def test_batch_issues_need_package_ledger_rights(station_job):
    operator, job, run, _ = station_job(known=False)
    product = job.product
    issue = QualityIssue.objects.create(
        product=product,
        batch=run.batch,
        code="invalid_row",
        scope=data_scope(job, run.window.target_start, run.window.target_end).serialize(),
    )
    query = {"product": str(product.pk), "scope": "synthetic", "batch": str(run.batch_id)}
    assert read_evidence(operator.pk, query)["issues"] == ["invalid_row"]

    wide_review = product.usegrant_set.get(operation="review")
    grant(operator, product, "review", fields=sorted(REQUIRED))
    revoke_grant(operator.pk, wide_review.pk, "F withdraw package review")
    evidence = read_evidence(operator.pk, query)
    assert len(evidence["receipts"]) == 2 and not evidence["issues"]
    assert QualityIssue.objects.filter(pk=issue.pk).exists()

    wide_retention = product.usegrant_set.get(operation="retain_normalized", fields=["*"])
    grant(operator, product, "retain_normalized", fields=sorted(REQUIRED | {"catalog"}))
    for code in ("F-01", "F-02"):
        grant(operator, product, "retain_normalized", object_code=code, fields=sorted(LEDGER))
    revoke_grant(operator.pk, wide_retention.pk, "F withdraw package retention")
    dispose_due(product.pk)
    assert not QualityIssue.objects.filter(pk=issue.pk).exists()
    assert Receipt.objects.filter(batch=run.batch).count() == run.items.count() == 2


def test_partial_retention_and_shared_catalog_then_total_disposal(station_job):
    operator, job, run, at = station_job(known=True)
    product = job.product
    sibling = Product.objects.create(
        source=product.source,
        synthetic=True,
        family="F",
        shape="S",
        level="station",
        code="station-city-hour",
        contract_version=product.contract_version,
        source_timezone=product.source_timezone,
    )
    minimum = grant(operator, product, "retain_normalized", fields=["request_ledger"])
    shared = grant(operator, sibling, "retain_normalized", fields=["catalog"])
    b_catalog = grant(
        operator, product, "retain_normalized", object_code="F-02", fields=["catalog"]
    )
    city_catalog = grant(
        operator, product, "retain_normalized", object_code="999999", fields=["catalog"]
    )
    b_data = grant(
        operator, product, "retain_normalized", object_code="F-02", fields=sorted(LEDGER)
    )
    broad = product.usegrant_set.get(operation="retain_normalized", fields=["*"])
    a_record = StationObservation.objects.get(object_code="F-01")
    b_record = StationObservation.objects.get(object_code="F-02")
    before = run.reserved_at
    assert read_window(operator.pk, run.window_id)["coverage"]["E"] == 2

    revoke_grant(operator.pk, broad.pk, "F withdraw first station and optional metadata")
    dispose_due(product.pk)
    job.refresh_from_db()
    run.refresh_from_db()
    assert job.disposed and not job.enabled and job.expected_codes is None
    assert job.selection == job.standard == job.backfill_policy == {}
    assert not job.roster_basis and job.next_due_at is None
    assert run.window.disposed and run.window.expected is None
    assert run.window.selection == {"mode": "include", "objects": {"F-02": ["AQI"]}}
    assert run.manifest_count is None and run.reserved_at == before and run.fetched_at is None
    assert not StationObservation.objects.filter(pk=a_record.pk).exists()
    assert (
        StationIdentifier.objects.count()
        == Station.objects.count()
        == SiteEpoch.objects.count()
        == 2
    )
    assert Receipt.objects.filter(batch=run.batch).count() == 1
    assert Receipt.objects.get(batch=run.batch).collection_metadata is None
    surviving = read_window(operator.pk, run.window_id)
    assert surviving["coverage"]["E"] is None and surviving["coverage"]["V"] == 1
    assert not surviving["runs"] and "F-01" not in json.dumps(surviving, default=str)
    params = dict(
        product=str(product.pk),
        scope="synthetic",
        site_epoch=str(b_record.site_epoch_id),
        indicator="AQI",
        observed_at=at.isoformat(),
        report_kind="hourly",
        window_definition=b_record.window_definition,
        standard_series=b_record.standard_series,
    )
    assert read_current(operator.pk, params)["results"][0]["value"] == "45.000000"
    with pytest.raises(Denied, match="job_disposed"):
        services.change_job(operator.pk, job.pk, "resume")
    with pytest.raises(IntegrityError), transaction.atomic():
        Job.objects.filter(pk=job.pk).update(expected_codes={"F-02": ["AQI"]})

    revoke_grant(operator.pk, shared.pk, "F withdraw shared catalogue")
    dispose_due(sibling.pk)
    assert list(StationIdentifier.objects.values_list("code", flat=True)) == ["F-02"]
    assert Station.objects.count() == SiteEpoch.objects.count() == 1
    assert StationObservation.objects.filter(pk=b_record.pk).exists()
    assert CityIdentifier.objects.count() == City.objects.count() == 1

    for item in (b_catalog, city_catalog, b_data):
        revoke_grant(operator.pk, item.pk, "F withdraw remaining source retention")
    dispose_due(product.pk)
    run.refresh_from_db()
    assert run.window.selection == {"mode": "include", "objects": {}}
    assert not run.items.exists() and run.batch_id is None
    assert run.reserved_at == before and Run.objects.count() == 1
    for model in (City, CityIdentifier, Station, SiteEpoch, StationIdentifier, StationObservation):
        assert not model.objects.exists()
    with pytest.raises(Denied):
        read_window(operator.pk, run.window_id)
    revoke_grant(operator.pk, minimum.pk, "F remaining request ledger withdrawn")
    dispose_due(product.pk)
    run.refresh_from_db()
    assert run.request_state == "disposed" and run.reserved_at is run.occupied_until is None


def test_worker_startup_sweeps_expired_catalogue_and_roster(station_job):
    operator, job, run, _ = station_job(known=True)
    product = job.product
    deadline = clock() + timedelta(hours=1)
    standing = product.usegrant_set.get(operation="retain_normalized", fields=["*"])
    expiring = grant(
        operator,
        product,
        "retain_normalized",
        fields=["*"],
        valid_until=deadline,
        expiry_mode="recoverable",
    )
    revoke_grant(operator.pk, standing.pk, "F replace retention with bounded grant")
    call_command("s02_dispose_due")
    assert read_window(operator.pk, run.window_id)["coverage"]["V"] == 2
    assert not Job.objects.get(pk=job.pk).disposed

    # Advance both the grant boundary and its durable cleanup obligation into the past.
    now = clock()
    type(expiring).objects.filter(pk=expiring.pk).update(valid_until=now)
    DispositionJob.objects.filter(product=product, due_at=deadline).update(due_at=now)
    with pytest.raises(Denied):
        read_window(operator.pk, run.window_id)
    call_command("s02_dispose_due")
    job.refresh_from_db()
    run.refresh_from_db()
    assert job.disposed and job.expected_codes is None and not job.roster_basis
    assert run.window.disposed and not run.items.exists() and run.batch_id is None
    assert not StationIdentifier.objects.exists() and not StationObservation.objects.exists()
    assert not DispositionJob.objects.filter(product=product, completed_at=None).exists()


def test_disposition_preserves_authorized_reused_station_code(station_job):
    from datetime import UTC, datetime

    from psycopg.types.range import Range

    operator, job, run, _ = station_job(known=True)
    product = job.product
    new = StationObservation.objects.get(object_code="F-01")
    current_ident = StationIdentifier.objects.get(code="F-01")
    old_start = datetime(2020, 1, 1, tzinfo=UTC)
    old_period = Range(old_start, current_ident.active_period.lower, "[)")
    old_station = Station.objects.create(source=product.source, name="F retired historical site")
    old_ident = StationIdentifier.objects.create(
        source=product.source,
        station=old_station,
        code="F-01",
        evidence="F old independent site",
        active_period=old_period,
    )
    old_epoch = SiteEpoch.objects.create(
        station=old_station,
        city=new.site_epoch.city,
        active_period=old_period,
        name="F retired historical site",
        location_evidence="F old independent site",
        certainty="documented",
    )
    old = StationObservation.objects.create(
        product=product,
        object_code=new.object_code,
        site_epoch=old_epoch,
        indicator=new.indicator,
        report_kind=new.report_kind,
        window_definition=new.window_definition,
        standard_series=new.standard_series,
        observed_at_utc=old_start,
        source_local_date=old_start.date(),
    )
    grant(operator, product, "retain_normalized", data_from=date(2024, 1, 1), fields=["*"])
    broad = product.usegrant_set.get(operation="retain_normalized", data_from=date.min)
    revoke_grant(operator.pk, broad.pk, "F old period withdrawn")
    dispose_due(product.pk)

    assert not StationObservation.objects.filter(pk=old.pk).exists()
    assert not StationIdentifier.objects.filter(pk=old_ident.pk).exists()
    assert not SiteEpoch.objects.filter(pk=old_epoch.pk).exists()
    assert StationObservation.objects.filter(pk=new.pk).exists()
    assert StationIdentifier.objects.filter(pk=current_ident.pk).exists()
    assert read_window(operator.pk, run.window_id)["coverage"]["V"] == 2
