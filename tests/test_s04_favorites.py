"""Private intent, competing PostgreSQL transactions, and directory disposition."""

import secrets
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from queue import Queue
from time import monotonic, sleep
from uuid import uuid4

import httpx
import pytest
from accounts.favorites import create_item, get_item, list_items
from accounts.models import Favorite
from collection.models import Job, WorkWindow
from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.db import connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import Client
from observations.catalog import relocate
from observations.disposition import dispose_due
from observations.models import City, CityIdentifier, SiteEpoch, Source, Station, StationIdentifier
from observations.policy import revoke_grant
from observations.queries import read_current
from psycopg.types.range import TimestamptzRange
from s02_support import (
    END,
    START,
    allow,
    candidate,
    create_product,
    create_site,
    in_db_session,
    ingest_fixture,
    observe_db,
    selector,
)
from test_s04_accounts import login
from test_s04_accounts import loopback as loopback

FAVORITES = "/api/v1/me/favorites/"
SYNTHETIC = "?scope=synthetic"
FIELDS = ["normalized", "provenance", "catalog"]
pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def setup():
    password = secrets.token_urlsafe(24)
    users = get_user_model().objects
    owner = users.create_user("intent-owner", password=password)
    other = users.create_user("other-owner", password=password)
    operator = users.create_user("intent-operator", password=password, is_superuser=True)
    return owner, other, operator, password


def register_product(operator, product, **terms):
    keep = allow(operator, product, fields=FIELDS, **terms)
    display = allow(operator, product, "display", fields=FIELDS, **terms)
    return keep, display


def city_in_product(product, *, period=(START, None), code="CITY"):
    city = City.objects.create(source=product.source, name="F City", geography="F")
    CityIdentifier.objects.create(
        city=city,
        source=city.source,
        code=code,
        active_period=TimestamptzRange(*period),
        evidence="F",
    )
    return city


def test_favorite_http_owner_domains_pagination_and_preference(setup, loopback):
    owner, other, operator, password = setup
    product = create_product("favorite-C", shape="C")
    alternative = create_product("favorite-C-alt", shape="C", source=product.source)
    foreign = create_product("favorite-foreign", shape="C")
    same_name = create_product("favorite-S")
    other_source = create_product("favorite-S-other")
    city = city_in_product(product)
    site = create_site(same_name)
    other_site = create_site(other_source)
    for item in (product, alternative, foreign, same_name, other_source):
        register_product(operator, item, data_until=datetime.max.date())

    def body(kind, target, selected):
        return {"kind": kind, "target_id": target, "product": selected, "scope": "synthetic"}

    with (
        httpx.Client(base_url=loopback, trust_env=False) as a,
        httpx.Client(base_url=loopback, trust_env=False) as b,
        httpx.Client(base_url=loopback, trust_env=False) as admin,
    ):
        denied = a.get(FAVORITES)
        assert denied.json() == {"code": "not_authenticated"}
        assert (
            "private" in denied.headers["Cache-Control"]
            and "no-store" in denied.headers["Cache-Control"]
        )
        a_token, b_token = login(a, owner.username, password), login(b, other.username, password)
        admin_token = login(admin, operator.username, password)
        chosen = body("city", city.pk, product.pk)
        assert a.post(FAVORITES, json=chosen).json() == {"code": "csrf_failed"}
        assert (
            a.post(FAVORITES, json=chosen | {"owner": other.pk}, headers=a_token).status_code == 400
        )
        assert a.post(FAVORITES, json=chosen | {"product": foreign.pk}, headers=a_token).json() == {
            "code": "use_denied"
        }
        first = a.post(FAVORITES, json=chosen, headers=a_token)
        assert first.status_code == 201
        assert first.json()["name"] == city.name
        intent = first.json()["id"]
        path_ = f"{FAVORITES}{intent}/"
        assert a.post(FAVORITES, json=chosen, headers=a_token).status_code == 200
        assert a.post(
            FAVORITES, json=chosen | {"product": alternative.pk}, headers=a_token
        ).json() == {"code": "favorite_exists"}
        assert a.get(path_).status_code == 404
        b_intent = b.post(FAVORITES, json=chosen, headers=b_token).json()["id"]
        assert b_intent != intent
        for stranger, token in ((b, b_token), (admin, admin_token)):
            assert stranger.get(FAVORITES + f"?scope=synthetic&owner={owner.pk}").status_code == 400
            assert stranger.get(path_ + SYNTHETIC).json() == {"code": "not_found"}
            assert (
                stranger.patch(
                    path_ + SYNTHETIC, json={"product": alternative.pk}, headers=token
                ).status_code
                == 404
            )
            assert stranger.delete(path_ + SYNTHETIC, headers=token).status_code == 404
        assert (
            a.patch(path_ + SYNTHETIC, json={"owner": other.pk}, headers=a_token).status_code == 400
        )
        changed = a.patch(path_ + SYNTHETIC, json={"product": alternative.pk}, headers=a_token)
        assert changed.status_code == 200 and changed.json()["product"] == alternative.pk
        for epoch, selected in ((site, same_name), (other_site, other_source)):
            created = a.post(
                FAVORITES, json=body("station", epoch.station_id, selected.pk), headers=a_token
            )
            assert created.status_code == 201, created.json()
        assert a.get(FAVORITES).json()["results"] == []
        page_args = {"scope": "synthetic", "page_size": 1}
        page = a.get(FAVORITES, params=page_args).json()
        rows, cursor = page["results"], page["page"]["next_cursor"]
        assert b.get(FAVORITES, params=page_args | {"cursor": cursor}).json() == {
            "code": "invalid_cursor"
        }
        while cursor:
            page = a.get(FAVORITES, params=page_args | {"cursor": cursor}).json()
            rows += page["results"]
            cursor = page["page"]["next_cursor"]
        assert [row["target_id"] for row in rows] == [
            other_site.station_id,
            site.station_id,
            city.pk,
        ]
        with observe_db() as pg:
            assert pg.execute(
                "SELECT owner_id, preferred_product_id FROM accounts_favorite WHERE city_id=%s ORDER BY owner_id",
                (city.pk,),
            ).fetchall() == [(owner.pk, alternative.pk), (other.pk, product.pk)]
        assert a.delete(path_ + SYNTHETIC, headers=a_token).status_code == 204
        assert b.get(f"{FAVORITES}{b_intent}/" + SYNTHETIC).status_code == 200


def test_competing_creates_one_intent(setup):
    owner, _, operator, _ = setup
    product = create_product("competing-favorites", shape="C")
    city = city_in_product(product)
    register_product(operator, product, data_until=datetime.max.date())
    pids = Queue()

    def create():
        return create_item(owner.pk, True, "city", city.pk, product.pk)

    with ThreadPoolExecutor(max_workers=2) as pool:
        with transaction.atomic():
            from observations.models import Product

            Product.objects.select_for_update().get(pk=product.pk)
            attempts = [pool.submit(in_db_session, pids, create) for _ in range(2)]
            blocked = [pids.get(timeout=5) for _ in attempts]
            deadline = monotonic() + 5
            with connection.cursor() as cursor:
                while monotonic() < deadline:
                    cursor.execute("SELECT pg_blocking_pids(%s), pg_blocking_pids(%s)", blocked)
                    if all(cursor.fetchone()):
                        break
                    sleep(0.02)
                else:
                    pytest.fail("both PostgreSQL sessions must wait for the product lock")
        results = [attempt.result(timeout=6) for attempt in attempts]
    assert {status for _, status in results} == {200, 201}
    assert len({row["id"] for row, _ in results}) == 1
    with observe_db() as pg:
        assert pg.execute(
            "SELECT count(*) FROM accounts_favorite WHERE owner_id=%s AND city_id=%s",
            (owner.pk, city.pk),
        ).fetchone() == (1,)


def test_station_revisit_and_actual_source_disposition(setup):
    owner, _, operator, _ = setup
    source = Source.objects.create(namespace="cnemc.fixture", publisher="Self-made F")
    stations = create_product("station-city-hour", source=source)
    cities = create_product("city-live-hour", "C", source=source)
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    start = min(START, now - timedelta(days=2))
    city = city_in_product(cities, period=(start, None))
    job = Job.objects.create(product=cities, city=city, executor=operator, profile="s04-f")
    station = Station.objects.create(source=source, name="Original station")
    old = SiteEpoch.objects.create(
        station=station,
        city=city,
        name="Original site",
        certainty="confirmed",
        location_evidence="F",
        active_period=TimestamptzRange(start, None),
    )
    code = StationIdentifier.objects.create(
        source=source,
        station=station,
        code="001",
        evidence="F",
        active_period=TimestamptzRange(start, None),
    )
    keep, display = register_product(operator, stations, data_until=datetime.max.date())
    city_keep, city_display = register_product(operator, cities, data_until=datetime.max.date())
    allow(operator, stations, "process_local")
    ingest_fixture(operator.pk, stations.pk, "favorites", "F", [candidate(stations)])
    row, _ = create_item(owner.pk, True, "station", station.pk, stations.pk)
    key = row["results"][0]["key"]
    city_row, _ = create_item(owner.pk, True, "city", city.pk, cities.pk)
    Station.objects.filter(pk=station.pk).update(name="Renamed station")
    assert get_item(owner.pk, row["id"], True)["name"] == "Renamed station"
    new = relocate(
        station,
        now - timedelta(hours=1),
        city=city,
        name="Moved site",
        certainty="confirmed",
        location_evidence="F",
    )
    follow = get_item(owner.pk, row["id"], True)
    assert (
        follow["state"] == "no_data" and follow["site_epoch"] == new.pk and follow["results"] == []
    )
    assert {epoch["site_epoch"] for epoch in follow["epochs"]} == {old.pk, new.pk}
    close = now - timedelta(minutes=30)
    SiteEpoch.objects.filter(pk=new.pk).update(
        active_period=TimestamptzRange(new.active_period.lower, close)
    )
    StationIdentifier.objects.filter(pk=code.pk).update(
        active_period=TimestamptzRange(start, close)
    )
    replacement = Station.objects.create(source=source, name=station.name)
    StationIdentifier.objects.create(
        source=source,
        station=replacement,
        code="001",
        evidence="F",
        active_period=TimestamptzRange(close, None),
    )
    follow = get_item(owner.pk, row["id"], True)
    assert follow["target_id"] == station.pk and follow["state"] == "select_epoch"
    assert key is not None and follow["results"] == []
    revoke_grant(operator.pk, display.pk, "F display withdrawn")
    dispose_due(stations.pk)
    assert get_item(owner.pk, row["id"], True) == {
        "id": row["id"],
        "kind": "station",
        "state": "unavailable",
    }
    assert Station.objects.filter(pk=station.pk).exists()
    revoke_grant(operator.pk, keep.pk, "F retention withdrawn")
    revoke_grant(operator.pk, city_keep.pk, "F retention withdrawn")
    revoke_grant(operator.pk, city_display.pk, "F display withdrawn")
    dispose_due(stations.pk)
    assert not Station.objects.filter(pk=station.pk).exists()
    assert Favorite.objects.get(pk=row["id"]).station_id is None
    city.refresh_from_db()
    assert Job.objects.get(pk=job.pk).city_id == city.pk
    assert (city.name, city.geography) == ("", "")
    assert get_item(owner.pk, city_row["id"], True) == {
        "id": city_row["id"],
        "kind": "city",
        "state": "unavailable",
    }
    assert list_items(owner.pk, {})["results"] == []


def test_favorite_migration_preserves_existing_session_observations_and_window(setup):
    owner, _, operator, _ = setup
    rollback = [("accounts", None)]
    executor = MigrationExecutor(connection)
    leaves = executor.loader.graph.leaf_nodes()
    try:
        executor.migrate(rollback)
        product = create_product("favorite-migration")
        site = create_site(product)
        allow(operator, product)
        allow(operator, product, "display")
        allow(operator, product, "process_local")
        ingest_fixture(operator.pk, product.pk, "favorites-migration", "F", [candidate(product)])
        job = Job.objects.create(product=product, executor=operator, profile="s04-upgrade")
        window = WorkWindow.objects.create(
            product=product,
            target_start=START,
            target_end=END,
            profile=job.profile,
            scope_key=str(uuid4()),
        )
        client = Client(HTTP_HOST="127.0.0.1")
        client.force_login(owner)
        cookie = client.cookies["sessionid"].value
        selection = selector(product, site)
        before = read_current(owner.pk, selection)
        assert before["count"] == 1
        assert Session.objects.filter(pk=cookie).exists()
        MigrationExecutor(connection).migrate(leaves)
        assert MigrationExecutor(connection).migration_plan(leaves) == []
        assert not Favorite.objects.exists()
        assert Session.objects.filter(pk=cookie).exists()
        assert read_current(owner.pk, selection) == before
        assert WorkWindow.objects.get(pk=window.pk).result == "pending"
        response = client.get("/api/v1/observations/current/", selection)
        assert response.status_code == 200 and response.json()["count"] == 1
    finally:
        MigrationExecutor(connection).migrate(leaves)


def test_city_summaries_respect_hourly_and_l_aqi_anchors(setup):
    owner, _, operator, _ = setup
    city_product = create_product("favorite-hourly", shape="C")
    national = create_product("favorite-levels", shape="L", source=city_product.source)
    city = city_in_product(city_product)
    for product in (city_product, national):
        register_product(operator, product, data_until=datetime.max.date())
        allow(operator, product, "process_local")
    late = START + timedelta(hours=12)
    ingest_fixture(
        operator.pk,
        city_product.pk,
        "hourly-c",
        "F",
        [
            candidate(city_product, code="CITY", indicator="CO", value="1"),
            candidate(
                city_product,
                code="CITY",
                at=late,
                indicator="O3",
                unit="µg/m³",
                value="100",
                window="rolling_8h_right_label",
            ),
        ],
    )
    from observations.s04_city import read_city

    city_summary, _ = create_item(owner.pk, True, "city", city.pk, city_product.pk)
    card = read_city(owner.pk, {"product": str(city_product.pk), "scope": "synthetic"}, city.pk)
    assert card["snapshot_at"] == city_summary["snapshot_at"] == START
    assert {row["key"] for slot in card["slots"] for row in slot["results"]} == {
        row["key"] for row in city_summary["results"]
    }
    levels = [
        candidate(national, code="CITY", indicator="PM2.5Level", unit="grade", value="2", at=at)
        for at in (START, late)
    ]
    ingest_fixture(operator.pk, national.pk, "levels-without-aqi", "F", levels)
    patched_intent = Favorite.objects.get(pk=city_summary["id"])
    patched_intent.preferred_product = national
    patched_intent.save(update_fields=["preferred_product"])
    before = get_item(owner.pk, city_summary["id"], True)
    assert before["snapshot_at"] is None and before["results"] == [] and before["link"] is None
    ingest_fixture(
        operator.pk,
        national.pk,
        "national-favorite-aqi",
        "F",
        [candidate(national, code="CITY", indicator="AQI", unit="1", value="40")],
    )
    after = get_item(owner.pk, city_summary["id"], True)
    assert after["snapshot_at"] == START
    assert [(row["indicator"], row["observed_at"]) for row in after["results"]] == [
        ("AQI", START),
        ("PM2.5Level", START),
    ]
    client = Client(HTTP_HOST="127.0.0.1")
    client.force_login(owner)
    detail = client.get(after["link"])
    assert detail.status_code == 200
    assert [(row["city"], row["indicator"]) for row in detail.json()["results"]] == [
        (city.pk, "AQI")
    ]


def test_station_favorite_requires_catalog_to_select_current_epoch(setup, loopback):
    owner, _, operator, password = setup
    product = create_product("favorite-station-without-catalog")
    current = create_site(product, code="CURRENT")
    closed = create_site(product, code="CLOSED")
    SiteEpoch.objects.filter(pk=current.pk).update(active_period=TimestamptzRange(START, None))
    StationIdentifier.objects.filter(station=current.station).update(
        active_period=TimestamptzRange(START, None)
    )
    for operation in ("retain_normalized", "display", "process_local"):
        allow(operator, product, operation, fields=FIELDS[:2])
    ingest_fixture(
        operator.pk,
        product.pk,
        "station-without-catalog",
        "F",
        [candidate(product, code=code) for code in ("CURRENT", "CLOSED")],
    )

    with httpx.Client(base_url=loopback, trust_env=False) as client:
        headers = login(client, owner.username, password)
        favorites = []
        for site in (current, closed):
            added = client.post(
                FAVORITES + SYNTHETIC,
                json={"kind": "station", "target_id": site.station_id, "product": product.pk},
                headers=headers,
            )
            assert added.status_code == 201
            summary = added.json()
            favorite = f"{FAVORITES}{summary['id']}/{SYNTHETIC}"
            assert client.get(favorite).json()["site_epoch"] == summary["site_epoch"]
            favorites.append(summary)
            entry = summary["epochs"][0]
            assert entry["site_epoch"] == site.pk and entry["status"] == "unknown"
            assert all(entry[field] is None for field in ("name", "period_start", "period_end"))
            detail = client.get(entry["link"])
            assert detail.status_code == 200
            assert [row["site_epoch"] for row in detail.json()["results"]] == [site.pk]

        projected = ("state", "site_epoch", "name", "results", "link")
        response = client.get(FAVORITES + SYNTHETIC)
        assert response.status_code == 200
        listed = {row["id"]: row for row in response.json()["results"]}
        for row in favorites:
            expected = ("select_epoch", None, None, [], None)
            assert tuple(row[field] for field in projected) == expected
            assert tuple(listed[row["id"]][field] for field in projected) == expected


def test_city_favorite_uses_authorized_non_anchor_readings_as_identity(setup, loopback):
    owner, _, operator, password = setup
    national = create_product("favorite-only-level", shape="L")
    local = create_product("favorite-only-rolling", shape="C", source=national.source)
    withhold = create_product("favorite-without-identity", shape="L", source=national.source)
    city = city_in_product(national)
    hidden = city_in_product(national, code="HIDDEN")
    for product, indicator, candidate_fields in (
        (national, "PM2.5Level", {"unit": "grade", "value": "2"}),
        (
            local,
            "O3",
            {
                "unit": "µg/m³",
                "value": "100",
                "window": "rolling_8h_right_label",
                "at": START + timedelta(hours=12),
            },
        ),
        (withhold, "PM2.5Level", {"unit": "grade", "value": "2"}),
    ):
        for operation in ("retain_normalized", "process_local"):
            allow(operator, product, operation, fields=FIELDS[:2], indicator=indicator)
        if product != withhold:
            allow(
                operator,
                product,
                "display",
                fields=FIELDS[:2],
                indicator=indicator,
                object_code="CITY",
            )
        ingest_fixture(
            operator.pk,
            product.pk,
            "non-anchor",
            "F",
            [
                candidate(product, code=code, indicator=indicator, **candidate_fields)
                for code in ("CITY", "HIDDEN")
            ],
        )

    with httpx.Client(base_url=loopback, trust_env=False) as client:
        headers = login(client, owner.username, password)
        body = {"kind": "city", "target_id": city.pk, "product": national.pk}

        def add(**changes):
            return client.post(FAVORITES + SYNTHETIC, json=body | changes, headers=headers)

        assert add(target_id=hidden.pk).json() == {"code": "use_denied"}
        assert add(product=withhold.pk).json() == {"code": "use_denied"}
        created = add()
        assert created.status_code == 201
        summary = created.json()
        assert summary["state"] == "no_data"
        assert summary["results"] == []
        assert all(summary[field] is None for field in ("name", "snapshot_at", "link"))
        assert add().status_code == 200
        detail = f"{FAVORITES}{summary['id']}/{SYNTHETIC}"
        assert (
            client.patch(detail, json={"product": withhold.pk}, headers=headers).status_code == 403
        )
        changed = client.patch(detail, json={"product": local.pk}, headers=headers)
        assert changed.status_code == 200
        summary = changed.json()
        assert summary["target_id"] == city.pk and summary["product"] == local.pk
        assert summary["snapshot_at"] is None and summary["results"] == []
        assert client.get(summary["link"]).json()["state"] == "no_data"
        assert (
            client.patch(detail, json={"product": national.pk}, headers=headers).status_code == 200
        )
