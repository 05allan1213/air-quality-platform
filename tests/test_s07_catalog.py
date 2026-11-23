"""Start a real user session without product IDs. Fixtures carry independent field rights."""

from datetime import timedelta

import pytest
from observations.policy import revoke_grant
from s02_support import START, allow, candidate, create_product, create_site, ingest_fixture
from s05_http import loopback

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
    ingest_fixture(op.pk, p.pk, "discover-F", "F", [candidate(p, 1), candidate(p, 1, code="002")])
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
    from collection.admission import cell_key
    from collection.models import Job, Run, WorkWindow
    from django.utils import timezone

    op = django_user_model.objects.create_user("s07-status-op", is_superuser=True)
    user = django_user_model.objects.create_user("s07-status-user", password="s05-test-only")
    p = create_product()
    site = create_site(p)
    for use in ("process_local", "retain_normalized"):
        allow(op, p, use, fields=["*"])
    allow(op, p, "display")
    ingest_fixture(op.pk, p.pk, "status-F", "F", [candidate(p, 1)])
    job = Job.objects.create(
        product=p, city=site.city, executor=op, profile="F", cadence_seconds=3600
    )
    ck = cell_key(site.pk, START, "CO")
    window = WorkWindow.objects.create(
        product=p,
        scope_key="F",
        profile="F",
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
