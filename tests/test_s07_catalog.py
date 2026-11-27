"""Start a real user session without product IDs. Fixtures carry independent field rights."""

import json
from datetime import timedelta
from uuid import uuid4

import pytest
from collection.adapters import cnemc
from collection.admission import cell_key
from collection.cnemc_jobs import PROFILE as CNEMC_PROFILE
from collection.models import Job, Run, RunItem, WorkWindow
from django.db import connection
from django.utils import timezone
from observations.models import Receipt
from observations.normalization import STANDARD_2012, STANDARD_2026
from observations.policy import revoke_grant
from s02_support import START, allow, create_product, create_site, ingest_fixture
from s05_http import loopback
from s05_support import candidate
from s07_contract import Witness

pytestmark = pytest.mark.django_db(transaction=True)


def test_catalog_discovers_only_authorized_historical_references(django_user_model):
    op = django_user_model.objects.create_user("s07-catalog-op", is_superuser=True)
    user = django_user_model.objects.create_user("s07-catalog-user", password="s05-test-only")
    p = create_product()
    visible = create_site(p)
    hidden = create_site(p, code="002")
    hidden.name = "unpublished F name"
    hidden.save(update_fields=["name"])
    for use in ("process_local", "retain_normalized"):
        allow(op, p, use, fields=["*"])
    show = allow(op, p, "display", object_code="001")
    ingest_fixture(
        op.pk, p.pk, "discover-F", "F", [candidate(p, token=1), candidate(p, "002", token=1)]
    )
    with loopback(user) as (web, _):
        route = "/api/v1/catalog/objects/"
        data = web.get(route, params={"scope": "synthetic", "page_size": 1}).json()
        assert data["page"]["next_cursor"] is None
        (one,) = data["results"]
        assert one["product"] == p.pk and one["site_epoch"] == visible.pk
        assert one["name"] is None and one["synthetic"]
        assert hidden.name not in str(data)
        assert web.get(one["link"]).json()["site_epoch"] == visible.pk
        assert web.get(one["history_link"]).json()["results"][0]["site_epoch"] == visible.pk
        revoke_grant(op.pk, show.pk, "F withdrawn")
        assert (
            web.get(route, params={"scope": "synthetic", "product": p.pk}).json()
            == web.get(route, params={"scope": "synthetic", "product": p.pk + 9999}).json()
        )


def test_coordinates_require_confirmed_crs_and_independent_field_permission(django_user_model):
    op = django_user_model.objects.create_user("s07-site-op", is_superuser=True)
    user = django_user_model.objects.create_user("s07-site-user", password="s05-test-only")
    p = create_product()
    epoch = create_site(p)
    epoch.longitude, epoch.latitude, epoch.crs = 7, 9, "EPSG:4326"
    epoch.save()
    for operation in ("display", "retain_normalized"):
        allow(op, p, operation, fields=["catalog"])
    with loopback(user) as (web, _):

        def point():
            response = web.get(
                f"/api/v1/cities/{epoch.city_id}/stations/",
                params={
                    "product": p.pk,
                    "scope": "synthetic",
                    "at": (START + timedelta(hours=1)).isoformat(),
                },
            )
            assert response.status_code == 200
            return response.json()["results"][0]["coordinates"]

        assert point() is None
        for op_name in ("retain_normalized", "display"):
            allow(op, p, op_name, fields=["coordinates"])
        assert point() == {"longitude": "7.000000", "latitude": "9.000000", "crs": "EPSG:4326"}
        for field, value in (
            ("crs", "unknown"),
            ("location_evidence", ""),
            ("certainty", "unverified"),
        ):
            saved = getattr(epoch, field)
            setattr(epoch, field, value)
            epoch.save()
            assert point() is None
            setattr(epoch, field, saved)


def test_source_status_follows_only_the_visible_window_cell(django_user_model):
    op = django_user_model.objects.create_user("s07-status-op", is_superuser=True)
    user = django_user_model.objects.create_user("s07-status-user", password="s05-test-only")
    p = create_product()
    site = create_site(p)
    for use in ("process_local", "retain_normalized"):
        allow(op, p, use, fields=["*"])
    allow(op, p, "display")
    ingest_fixture(op.pk, p.pk, "status-F", "F", [candidate(p, token=1)])
    job = Job.objects.create(
        product=p, city=site.city, executor=op, profile="f-exact-v1", cadence_seconds=3600
    )
    ck = cell_key(site.pk, START, "CO")
    window = WorkWindow.objects.create(
        product=p,
        scope_key="F",
        profile=job.profile,
        target_start=START,
        target_end=START + timedelta(hours=1),
        expected={ck: ["001", START.isoformat(), "CO"]},
    )
    now = timezone.now()
    run = Run.objects.create(
        job=job,
        window=window,
        actor=op,
        executor=op,
        reason="manual",
        status="failed",
        error_code="upstream_transient",
        request_state="possible",
        reserved_at=now,
        started_at=now,
        finished_at=now,
    )
    with loopback(user) as (web, _):

        def get():
            response = web.get(
                f"/api/v1/stations/{site.station_id}/",
                params={
                    "product": p.pk,
                    "scope": "synthetic",
                    "site_epoch": site.pk,
                    "indicators": "CO",
                    "as_of": START.isoformat(),
                },
            )
            assert response.status_code == 200
            return response.json()["results"][0]

        assert get()["source_event_at"] is None
        authorized = allow(op, p, "display", fields=["source_status"])
        row = get()
        assert (
            row["source_status"] == "upstream_unavailable"
            and row["source_event_at"]
            and row["observed_at"].startswith("2024-01-01")
        )
        Run.objects.filter(pk=run.pk).update(
            finished_at=now - timedelta(hours=2), started_at=now - timedelta(hours=2)
        )
        row = get()
        assert row["source_status"] == "unknown" and row["source_event"] == "upstream_unavailable"
        WorkWindow.objects.filter(pk=window.pk).update(
            selection={"mode": "include", "objects": {"other": ["CO"]}}
        )
        assert get()["source_event_at"] is None
        WorkWindow.objects.filter(pk=window.pk).update(selection={})
        Run.objects.filter(pk=run.pk).update(
            status="pending",
            request_state="unsent",
            error_code="budget_exhausted",
            started_at=now,
            reserved_at=None,
            finished_at=None,
            next_retry_at=now + timedelta(minutes=5),
        )
        pending = get()
        assert pending["source_status"] == "quota_wait" and pending["source_status_until"]
        assert pending["source_event_at"] is None  # the ledger has no quota-decision timestamp
        revoke_grant(op.pk, authorized.pk, "F status withdrawn")
        assert get()["source_event_at"] is None


def status_row(web, product, site, when, indicator, window, report, series):
    response = web.get(
        "/api/v1/observations/query/",
        params={
            "scope": "synthetic",
            "product": product.pk,
            "site_epochs": site.pk,
            "start": when.isoformat(),
            "end": (when + timedelta(hours=1)).isoformat(),
            "mode": "window",
            "indicators": indicator,
            "window_definition": window,
            "report_kind": report,
            "standard_series": series,
        },
    )
    assert response.status_code == 200, response.json()
    (row,) = response.json()["results"]
    assert (row["window_definition"], row["standard_series"]) == (window, series)
    return row["source_status"], row["source_event"]


@pytest.fixture
def status_series(django_user_model, request):
    assert connection.vendor == "postgresql"
    actor = django_user_model.objects.create_user("status-admin-F", is_superuser=True)
    reader = django_user_model.objects.create_user("status-reader-F", password="s05-test-only")
    product = create_product()
    site = create_site(product)
    for use in ("process_local", "retain_normalized"):
        allow(actor, product, use, fields=["*"])
    allow(actor, product, "display")
    right = allow(actor, product, "display", fields=["source_status"])
    witness = Witness()
    yield actor, reader, product, site, right, START + timedelta(hours=12), witness
    request.node.user_properties.append(("s07_http_contract", json.dumps(witness.receipt())))


def collection_window(actor, product, site, at, indicator, *, profile="f-exact-v1", standard=None):
    standard = standard or {}
    key = cell_key(site.pk, at, indicator)
    job = Job.objects.create(
        product=product, city=site.city, executor=actor, profile=profile, standard=standard
    )
    window = WorkWindow.objects.create(
        product=product,
        scope_key=f"city:{site.city_id}",
        profile=profile,
        target_start=at,
        target_end=at + timedelta(hours=1),
        expected={key: ["001", at.isoformat(), indicator]},
        standard=standard,
    )
    return job, window, key


def test_source_status_uses_the_receipt_key_before_ordering_events(status_series):
    op, user, p, site, grant, at, witness = status_series
    job, window, ck = collection_window(op, p, site, at, "O3")
    now = timezone.now()
    for definition, status, age in [
        ("hourly_right_label", "accepted", 20),
        ("rolling_8h_right_label", "reread", 10),
    ]:
        data = candidate(p, at=at, indicator="O3", unit="µg/m³", value="35", window=definition)
        ingest_fixture(op.pk, p.pk, definition, "F", [data])
        receipts = Receipt.objects.filter(station_revision__key__window_definition=definition)
        if status == "reread":
            ingest_fixture(op.pk, p.pk, definition + "-again", "F", [data])
        receipt = receipts.latest("pk")
        time = now - timedelta(seconds=age)
        run = Run.objects.create(
            job=job,
            window=window,
            reason="manual",
            actor=op,
            executor=op,
            status="finished",
            request_state="responded",
            reserved_at=time,
            started_at=time,
            finished_at=time,
            batch_id=receipt.batch_id,
        )
        receipt.collection_item = RunItem.objects.create(
            run=run,
            window=window,
            item_key=ck,
            object_code="001",
            indicator="O3",
            observed_at=at,
            status=status,
            accepted=status == "accepted",
            owner=uuid4(),
            generation=1,
        )
        receipt.save(update_fields=["collection_item"])

    with loopback(user) as (web, _):
        web.event_hooks["response"].append(witness.inspect)

        def results():
            selections = [
                ("hourly_right_label", "hourly"),
                ("rolling_8h_right_label", "rolling_8h"),
            ]
            return [
                status_row(web, p, site, at, "O3", definition, report, "not_applicable")
                for definition, report in selections
            ]

        assert results() == [("responded", "responded"), ("no_new", "no_new")]
        revoke_grant(op.pk, grant.pk, "F status withdrawn")
        assert results() == [("unknown", None), ("unknown", None)]
    assert Run.objects.count() == 2


@pytest.mark.parametrize("indicator", ["O3", "AQI"])
def test_source_status_without_receipt_requires_frozen_series(status_series, indicator):
    op, user, p, site, grant, at, witness = status_series
    hourly = "hourly_label_unspecified"
    endpoint = ""
    if indicator == "O3":
        selections = [
            (hourly, "hourly", "not_applicable"),
            ("rolling_8h_right_label", "rolling_8h", "not_applicable"),
        ]
        rows = [
            candidate(p, at=at, indicator=indicator, value="40", unit="µg/m³", window=w)
            for w, _, _ in selections
        ]
        job, window, _ = collection_window(op, p, site, at, indicator)
    else:
        p.code = "station-city-hour"
        p.contract_version = cnemc.VERSION
        p.source_timezone = cnemc.TIMEZONE
        p.save(update_fields=["code", "contract_version", "source_timezone"])
        endpoint = cnemc.PRODUCTS[p.code]
        selections = [(hourly, "hourly", series) for series in (STANDARD_2012, STANDARD_2026)]
        rows = [
            candidate(
                p,
                at=at,
                indicator=indicator,
                value="40",
                unit="1",
                window=w,
                standard_series=series,
                standard_basis="source_stated",
                standard_evidence="F",
            )
            for w, _, series in selections
        ]
        job, window, _ = collection_window(
            op,
            p,
            site,
            at,
            indicator,
            profile=CNEMC_PROFILE,
            standard={
                "series": STANDARD_2012,
                "basis": "source_stated",
                "evidence": "F",
                "from": at.date().isoformat(),
                "until": (at.date() + timedelta(days=1)).isoformat(),
            },
        )
        Job.objects.filter(pk=job.pk).update(standard=window.standard | {"series": STANDARD_2026})
    ingest_fixture(op.pk, p.pk, "status-fault-F", "F", rows)
    now = timezone.now()
    Run.objects.create(
        job=job,
        window=window,
        endpoint=endpoint,
        reason="manual",
        actor=op,
        executor=op,
        status="failed",
        error_code="upstream_transient",
        request_state="possible",
        started_at=now,
        finished_at=now,
        reserved_at=now,
    )
    assert not RunItem.objects.exists()
    with loopback(user) as (web, _):
        web.event_hooks["response"].append(witness.inspect)

        def results():
            return [status_row(web, p, site, at, indicator, *args) for args in selections]

        assert results() == [("upstream_unavailable", "upstream_unavailable"), ("unknown", None)]
        if indicator == "AQI":
            invalid = window.standard | {"evidence": 42}
            WorkWindow.objects.filter(pk=window.pk).update(standard=invalid)
            assert results() == [("unknown", None), ("unknown", None)]
            WorkWindow.objects.filter(pk=window.pk).update(standard=window.standard)
        revoke_grant(op.pk, grant.pk, "F status withdrawn")
        assert results() == [("unknown", None), ("unknown", None)]
    assert Run.objects.count() == 1


def test_city_station_discovery_uses_catalog_rights_without_observations(django_user_model):
    from observations.models import City

    op = django_user_model.objects.create_superuser("station-directory-operator")
    reader = django_user_model.objects.create_user(
        "station-directory-reader", password="s05-test-only"
    )
    p = create_product()
    epoch = create_site(p)
    empty = City.objects.create(source=p.source, name="F empty", geography="B")
    grant = allow(op, p, "display", fields=["catalog"], object_code="001")
    allow(op, p, "retain_normalized", fields=["catalog"])
    with loopback(reader) as (web, _):
        route = "/api/v1/catalog/objects/"
        query = {"scope": "synthetic", "at": START.isoformat()}

        def get(city):
            response = web.get(route, params=query | {"city": city})
            assert response.status_code == 200
            return response.json()["results"]

        assert web.get(route, params=query).json()["results"] == []
        (row,) = get(epoch.city_id)
        assert set(row) == {"city", "product", "station_directory", "synthetic"}
        assert row["product"] == p.pk and row["synthetic"]
        assert web.get(row["station_directory"]).json()["results"][0]["site_epoch"] == epoch.pk
        assert get(empty.pk) == get(empty.pk + 999) == []
        revoke_grant(op.pk, grant.pk, "F directory withdrawn")
        assert get(epoch.city_id) == []
        assert web.get(row["station_directory"]).status_code == 403
        allow(op, p, "display", fields=["catalog"], geography="B")
        (empty_row,) = get(empty.pk)
        assert web.get(empty_row["station_directory"]).json()["state"] == "no_data"
