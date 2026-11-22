"""Station windows against PostgreSQL with S02-validated F data."""

import secrets
from datetime import timedelta
from functools import partial

import httpx
import pytest
from collection.models import Run
from config.urls import urlpatterns as project_patterns
from django.urls import path
from observations.catalog import relocate
from observations.models import SiteEpoch, StationIdentifier, StationRevision
from observations.policy import revoke_grant
from observations.s04_http import read_view
from observations.s04_station import FixtureStatus, read_station
from psycopg.types.range import TimestamptzRange
from s02_support import (
    BOUNDARY,
    START,
    allow,
    candidate,
    create_product,
    create_site,
    ingest_fixture,
    observe_db,
)
from test_s04_accounts import login
from test_s04_accounts import loopback as loopback

urlpatterns = [
    path(
        "test/stale/<int:station>/",
        read_view(
            partial(
                read_station,
                fixture_status=FixtureStatus("upstream_unavailable", timedelta(hours=2)),
            )
        ),
    ),
    path(
        "test/unknown/<int:station>/",
        read_view(partial(read_station, fixture_status=FixtureStatus("upstream_unavailable"))),
    ),
    *project_patterns,
]
pytestmark = pytest.mark.django_db(transaction=True)
NS = {"scope": "synthetic"}
HOUR = timedelta(hours=1)


@pytest.fixture
def operator(django_user_model):
    return django_user_model.objects.create_user("s04-station-op", is_superuser=True)


@pytest.fixture
def reader(client, django_user_model):
    client.force_login(django_user_model.objects.create_user("s04-station-reader"))
    return client


def test_latest_is_per_indicator_and_history_is_exact(operator, reader, settings):
    product = create_product("s04-station")
    epoch = create_site(product)
    epoch = relocate(
        epoch.station,
        BOUNDARY,
        city=epoch.city,
        name="new epoch",
        location_evidence="synthetic test",
        certainty="confirmed",
    )
    older_epoch = epoch.station.siteepoch_set.exclude(pk=epoch.pk).get()
    allow(operator, product, "process_local", fields=["normalized"])
    allow(operator, product)
    allow(operator, product, "display", fields=["normalized", "provenance"])
    t1, t2, t3 = (BOUNDARY + i * HOUR for i in range(3))
    inputs = [
        candidate(product, value="0", at=t1),
        candidate(product, value="NA", at=t2),
        candidate(product, indicator="PM2.5", unit="µg/m³", value="3", at=t2),
        candidate(product, value="55", at=t3 - 2 * HOUR, window="hourly_right_label"),
    ]
    # The right-labelled CO sample at the relocation boundary belongs to the old epoch.
    ingest_fixture(operator.pk, product.pk, "station-values", "s04.station.fixture", inputs)
    query = NS | {
        "product": product.pk,
        "site_epoch": epoch.pk,
        "as_of": t3.isoformat(),
        "indicators": "PM2.5,CO",
    }
    route = f"/api/v1/stations/{epoch.station_id}/"
    response = reader.get(route, query)
    assert response.status_code == 200, response.json()
    data = response.json()
    rows = {row["indicator"]: row for row in data["results"]}
    assert rows["CO"]["value"] == "0.000000"
    assert rows["CO"]["age_seconds"] - rows["PM2.5"]["age_seconds"] == 3600
    assert rows["CO"]["freshness"] == "unknown"
    assert data["source_status"] == "unknown"
    assert data["identity"]["station_name"] is None and data["identity"]["period_start"] is None
    assert {row["site_epoch"] for row in rows.values()} == {epoch.pk}
    assert (
        rows["CO"]["revision"]
        == StationRevision.objects.get(
            key__site_epoch=epoch,
            key__observed_at_utc=t1,
            key__indicator="CO",
            is_current=True,
        ).pk
    )
    history = reader.get(data["history"][0])
    assert history.status_code == 200
    assert rows["CO"]["revision"] in {row["revision"] for row in history.json()["results"]}
    window = "/api/v1/observations/query/"
    exact = NS | {
        "product": product.pk,
        "site_epochs": epoch.pk,
        "mode": "window",
        "indicators": "CO",
        "report_kind": "hourly",
        "window_definition": "hourly_label_unspecified",
        "start": t2.isoformat(),
        "end": t3.isoformat(),
    }
    empty = reader.get(window, exact).json()
    assert empty["state"] == "no_data" and empty["results"] == []
    missing = reader.get(window, exact | {"quality": "missing"}).json()["results"][0]
    assert missing["quality"] == "missing" and missing["value"] is None
    assert (
        reader.get(
            window,
            exact
            | {
                "site_epochs": older_epoch.pk,
                "window_definition": "hourly_right_label",
                "start": t1.isoformat(),
                "end": t2.isoformat(),
            },
        ).json()["results"][0]["value"]
        == "55.000000"
    )

    settings.ROOT_URLCONF = __name__
    runs = Run.objects.count()
    stale = reader.get(f"/test/stale/{epoch.station_id}/", query).json()
    unknown = reader.get(f"/test/unknown/{epoch.station_id}/", query).json()
    for status_view, status in [(stale, "stale"), (unknown, "unknown")]:
        assert status_view["source_status"] == "upstream_unavailable"
        assert {r["source_status"] for r in status_view["results"]} == {"upstream_unavailable"}
        assert {r["freshness"] for r in status_view["results"]} == {status}
        assert {r["revision"] for r in status_view["results"]} == {
            r["revision"] for r in rows.values()
        }
    assert Run.objects.count() == runs
    assert reader.get(route, query | {"source_status": "upstream_unavailable"}).status_code == 400


def test_directory_requires_catalog_and_keeps_identity_across_relocation(operator, reader):
    regional = create_product("s04-regional")
    national = create_product("s04-national", source=regional.source)
    epoch = create_site(regional, code="A")
    other = create_site(regional, code="B")
    other.city = epoch.city
    other.save(update_fields=["city"])
    successor = relocate(
        epoch.station,
        BOUNDARY,
        city=epoch.city,
        name="new place",
        location_evidence="synthetic test",
        certainty="confirmed",
    )
    different = create_site(create_product("s04-other"))
    allow(operator, regional)
    allow(operator, regional, "display")
    retain_catalog = allow(operator, regional, fields=["normalized", "provenance", "catalog"])
    show_catalog = allow(
        operator, regional, "display", fields=["normalized", "provenance", "catalog"]
    )
    directory = f"/api/v1/cities/{epoch.city_id}/stations/"
    query = NS | {"product": regional.pk, "at": BOUNDARY.isoformat(), "page_size": 1}
    front = reader.get(directory, query)
    assert front.status_code == 200, front.json()
    assert front.json()["results"][0]["site_epoch"] == successor.pk
    assert front.json()["results"][0]["station"] == epoch.station_id
    assert front.json()["results"][0]["coordinates"] is None
    assert front.json()["results"][0]["codes"] == ["A"]
    cursor = front.json()["page"]["next_cursor"]
    assert cursor
    next_rows = reader.get(directory, query | {"cursor": cursor}).json()["results"]
    assert [row["site_epoch"] for row in next_rows] == [other.pk]
    route = f"/api/v1/stations/{epoch.station_id}/"
    station = reader.get(route, NS | {"product": regional.pk}).json()
    assert [row["site_epoch"] for row in station["results"]] == [epoch.pk, successor.pk]
    assert {row["status"] for row in station["results"]} == {"historical"}
    assert reader.get(directory, query | {"product": national.pk}).json() == {"code": "use_denied"}
    assert reader.get(route, NS | {"product": regional.pk, "site_epoch": different.pk}).json() == {
        "code": "use_denied",
    }
    assert reader.get(directory, query | {"cursor": cursor, "page_size": 2}).json() == {
        "code": "invalid_cursor",
    }
    revoke_grant(operator.pk, show_catalog.pk, "catalog display withdrawn")
    assert reader.get(directory, query).json() == {"code": "use_denied"}
    assert reader.get(route, NS | {"product": regional.pk}).json() == {"code": "use_denied"}
    assert retain_catalog.revoked_at is None
    empty = reader.get(route, NS | {"product": regional.pk, "site_epoch": successor.pk}).json()
    assert empty["state"] == "no_data" and empty["identity"]["period_end"] is None


def test_epoch_choices_disclose_catalog_at_the_represented_instant(operator, reader):
    product = create_product("s04-limited-directory")
    epoch = create_site(product)
    cutoff = BOUNDARY.date()
    for operation in ("retain_normalized", "display"):
        allow(operator, product, operation, fields=["catalog"], data_until=cutoff)
    route = f"/api/v1/stations/{epoch.station_id}/"
    query = NS | {"product": product.pk}
    before = reader.get(route, query | {"as_of": (BOUNDARY - HOUR).isoformat()}).json()
    assert before["results"][0]["codes"] == ["001"]
    after = reader.get(route, query | {"as_of": BOUNDARY.isoformat()})
    assert after.status_code == 200
    row = after.json()["results"][0]
    assert row["site_epoch"] == epoch.pk and row["station"] == epoch.station_id
    assert row["codes"] == [] and row["status"] == "unknown"
    assert all(
        row[k] is None
        for k in ("city", "station_name", "city_name", "site_name", "period_start", "period_end")
    )


def test_epoch_pages_sort_only_by_disclosed_starts(operator, django_user_model, loopback):
    product = create_product("s04-epoch-order")
    day = timedelta(days=1)
    latest = create_site(product)
    latest.active_period = TimestamptzRange(START + 4 * day, START + 6 * day)
    latest.save(update_fields=["active_period"])
    StationIdentifier.objects.filter(station=latest.station).update(
        active_period=TimestamptzRange(START, START + 6 * day)
    )
    middle, earliest = (
        SiteEpoch.objects.create(
            station=latest.station,
            city=latest.city,
            active_period=TimestamptzRange(START + n * day, START + (n + 2) * day),
        )
        for n in (2, 0)
    )

    def grant_day(n):
        dates = {
            "data_from": (START + n * day).date(),
            "data_until": (START + (n + 1) * day).date(),
        }
        allow(operator, product, "retain_normalized", fields=["catalog"], **dates)
        return allow(operator, product, "display", fields=["catalog"], **dates)

    for n in (0, 2, 4):
        grant_day(n)
    with observe_db() as pg:
        ids = [
            row[0]
            for row in pg.execute(
                "SELECT id FROM observations_siteepoch WHERE station_id=%s ORDER BY id",
                (latest.station_id,),
            )
        ]
    assert ids == [latest.pk, middle.pk, earliest.pk]
    password = secrets.token_urlsafe(24)
    user = django_user_model.objects.create_user("s04-epoch-order-reader", password=password)
    route = f"/api/v1/stations/{latest.station_id}/"
    query = NS | {"product": product.pk, "as_of": (START + 6 * day).isoformat(), "page_size": 1}
    with httpx.Client(base_url=loopback, trust_env=False) as web:
        login(web, user.username, password)

        def all_pages():
            rows = []
            tokens = []
            options = query
            while True:
                response = web.get(route, params=options)
                assert (
                    response.status_code == 200 and "no-store" in response.headers["Cache-Control"]
                )
                page = response.json()
                assert len(page["results"]) == 1
                rows.extend(page["results"])
                token = page["page"]["next_cursor"]
                if token is None:
                    return rows, tokens
                assert len(tokens) < len(ids) and token not in tokens
                tokens.append(token)
                options = query | {"cursor": token}

        hidden, _ = all_pages()
        assert all(row["period_start"] is None and row["status"] == "unknown" for row in hidden)
        assert [row["site_epoch"] for row in hidden] == ids
        first_day = grant_day(1)
        grant_day(3)
        mixed, tokens = all_pages()
        assert [row["site_epoch"] for row in mixed] == [earliest.pk, middle.pk, latest.pk]
        assert [row["period_start"] is not None for row in mixed] == [True, True, False]
        revoke_grant(operator.pk, first_day.pk, "F page anchor loses its public ordering field")
        changed = web.get(route, params=query | {"cursor": tokens[0]})
        assert changed.status_code == 409 and changed.json() == {"code": "page_changed"}
