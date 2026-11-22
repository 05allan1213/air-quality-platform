"""Cross-endpoint V09 noninterference and V11 detail limits against PostgreSQL."""

import secrets
from datetime import date, timedelta
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from django.core.cache import cache
from django.core.signing import loads
from observations.models import City, CityIdentifier, StationIdentifier
from observations.normalization import INDICATORS, STANDARD_2012
from observations.policy import revoke_grant
from psycopg.types.range import TimestamptzRange
from s02_support import (
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

pytestmark = pytest.mark.django_db(transaction=True)
FAVORITES = "/api/v1/me/favorites/"
DETAIL = "/api/v1/observations/query/"
HOUR = timedelta(hours=1)
SYNTHETIC = {"scope": "synthetic"}
PUBLIC_FIELDS = ["normalized", "provenance", "catalog", "source_time", "source_description"]


@pytest.fixture
def session(django_user_model, loopback):
    cache.clear()
    operator = django_user_model.objects.create_superuser("s04-boundary-op")
    password = secrets.token_urlsafe(24)
    reader = django_user_model.objects.create_user("s04-boundary-reader", password=password)
    with httpx.Client(base_url=loopback, trust_env=False) as web:
        yield operator, web, login(web, reader.username, password)


def scoped(product, **fields):
    return SYNTHETIC | {"product": product.pk, **fields}


def read(web, path, params, status=200):
    response = web.get(path, params=params)
    assert response.status_code == status, (path, response.text)
    assert {"private", "no-store"} <= {
        part.strip() for part in response.headers["Cache-Control"].split(",")
    }
    return response.json()


def snapshot(value):
    """Normalize request-clock fields and cursor signing time, retaining their payloads."""
    if isinstance(value, list):
        return [snapshot(item) for item in value]
    if not isinstance(value, dict):
        return value
    normalized = {}
    for key, item in value.items():
        if key in {"evaluated_at", "age_seconds"}:
            continue
        if key == "next_cursor" and item:
            item = loads(item, salt="s04.page.v1")
        if key == "link" and item:
            link = urlsplit(item)
            params = parse_qs(link.query)
            if link.path.startswith("/api/v1/stations/") and "as_of" in params:
                # Favorite station links use the clock from this request.
                params["as_of"] = ["request clock"]
            item = [link.path, params]
        normalized[key] = snapshot(item)
    return normalized


def add_city_code(city, code):
    CityIdentifier.objects.create(
        source=city.source,
        city=city,
        code=code,
        active_period=TimestamptzRange(START, None),
        evidence="F S04 comparison",
    )


def make_observation(product, code, at, value, token=1):
    if product.shape == "S":
        return candidate(product, token, value, at=at, code=code)
    return candidate(
        product,
        token,
        value,
        at=at,
        code=code,
        indicator="AQI",
        unit="1",
        standard_series=STANDARD_2012,
        standard_basis="source_stated",
        standard_evidence="F S04 comparison",
    )


def put(operator, product, label, *rows):
    ingest_fixture(operator.pk, product.pk, label, "s04-noninterference-v1", rows)


def hidden_rows(products):
    result = {}
    with observe_db() as pg:
        for product in products:
            if product.shape == "S":
                query = """
                    SELECT k.observed_at_utc, r.value FROM observations_stationobservation k
                    JOIN observations_stationrevision r ON r.key_id = k.id
                    WHERE k.product_id = %s AND k.object_code = '002' AND r.is_current
                    ORDER BY k.observed_at_utc
                """
            else:
                query = """
                    SELECT k.observed_at_utc, r.value FROM observations_cityobservation k
                    JOIN observations_cityrevision r ON r.key_id = k.id
                    WHERE k.product_id = %s AND k.object_code = '002' AND r.is_current
                    ORDER BY k.observed_at_utc
                """
            result[product.pk] = pg.execute(query, (product.pk,)).fetchall()
    return result


def test_v09_hidden_revisions_leave_other_routes_and_favorite_summaries_unchanged(session):
    operator, web, csrf = session
    stations = create_product("s04-invariance-S")
    city_product = create_product("s04-invariance-C", "C", stations.source)
    trend = create_product("s04-invariance-T", "T", stations.source)
    site = create_site(stations)
    period = TimestamptzRange(START, None)
    site.active_period = period
    site.save(update_fields=["active_period"])
    StationIdentifier.objects.filter(station=site.station).update(active_period=period)
    StationIdentifier.objects.create(
        source=stations.source,
        station=site.station,
        code="002",
        active_period=period,
        evidence="F S04 comparison",
    )
    add_city_code(site.city, "001")
    add_city_code(site.city, "002")
    products = (city_product, trend, stations)
    display = []
    terms = {"data_until": date.max}
    for product in products:
        for operation in ("process_local", "retain_normalized"):
            allow(operator, product, operation, fields=["*"], **terms)
        display.append(
            allow(operator, product, "display", object_code="001", fields=PUBLIC_FIELDS, **terms)
        )
        withdrawn = allow(
            operator, product, "display", object_code="002", fields=PUBLIC_FIELDS, **terms
        )
        put(
            operator,
            product,
            "before",
            make_observation(product, "001", START, "10"),
            make_observation(product, "001", START + HOUR, "20"),
            make_observation(product, "002", START + 2 * HOUR, "30"),
        )
        revoke_grant(operator.pk, withdrawn.pk, "V09 synthetic display withdrawal")

    intents = {}
    for kind, target, product in (
        ("city", site.city_id, city_product),
        ("station", site.station_id, stations),
    ):
        response = web.post(
            FAVORITES, json=scoped(product, kind=kind, target_id=target), headers=csrf
        )
        assert response.status_code == 201, response.text
        item = response.json()
        assert item["state"] == "ok" and item["results"]
        intents[kind] = item["id"]

    detail = scoped(
        stations,
        site_epochs=site.pk,
        indicators="CO",
        mode="window",
        report_kind="hourly",
        window_definition="hourly_label_unspecified",
        start=START.isoformat(),
        end=(START + 5 * HOUR).isoformat(),
        page_size=1,
    )
    city_path = f"/api/v1/cities/{site.city_id}/"
    endpoints = {
        "city": (city_path, scoped(city_product)),
        "trend": (city_path + "trend/", scoped(trend)),
        "station": (
            f"/api/v1/stations/{site.station_id}/",
            scoped(stations, site_epoch=site.pk, as_of=(START + 5 * HOUR).isoformat()),
        ),
        "detail": (DETAIL, detail),
    }
    private = {
        "favorite-list": (FAVORITES, SYNTHETIC | {"page_size": 1}),
        **{kind: (f"{FAVORITES}{item}/", SYNTHETIC) for kind, item in intents.items()},
    }
    queries = endpoints | {f"private-{kind}": args for kind, args in private.items()}
    before = {key: read(web, *args) for key, args in queries.items()}
    assert before["city"]["snapshot_at"].startswith("2024-01-01T01:00:00")
    assert before["trend"]["anchor"].startswith("2024-01-01T01:00:00")
    assert before["station"]["results"][0]["value"] == "20.000000"
    assert before["detail"]["results"][0]["value"] == "20.000000"
    detail_cursor = before["detail"]["page"]["next_cursor"]
    assert detail_cursor
    before_tail = read(web, DETAIL, detail | {"cursor": detail_cursor})
    assert before_tail["results"][0]["value"] == "10.000000"
    assert before_tail["page"]["next_cursor"] is None
    favorite_cursor = before["private-favorite-list"]["page"]["next_cursor"]
    assert favorite_cursor
    favorite_tail = SYNTHETIC | {"page_size": 1, "cursor": favorite_cursor}
    before_favorite_tail = read(web, FAVORITES, favorite_tail)
    assert before_favorite_tail["results"][0]["id"] == intents["city"]

    assert all(rows == [(START + 2 * HOUR, 30)] for rows in hidden_rows(products).values())
    for product in products:
        put(
            operator,
            product,
            "after",
            make_observation(product, "002", START + 2 * HOUR, "88", token=2),
            make_observation(product, "002", START + 3 * HOUR, "99"),
        )
    assert all(
        rows == [(START + 2 * HOUR, 88), (START + 3 * HOUR, 99)]
        for rows in hidden_rows(products).values()
    )
    for key, args in queries.items():
        assert snapshot(read(web, *args)) == snapshot(before[key]), key
    assert snapshot(read(web, DETAIL, detail | {"cursor": detail_cursor})) == snapshot(before_tail)
    assert snapshot(read(web, FAVORITES, favorite_tail)) == snapshot(before_favorite_tail)

    for grant in display:
        revoke_grant(operator.pk, grant.pk, "V09 synthetic product display withdrawal")
    for path, params in [*endpoints.values(), (DETAIL, detail | {"cursor": detail_cursor})]:
        assert read(web, path, params, 403) == {"code": "use_denied"}
    placeholders = [
        {"id": item, "kind": kind, "state": "unavailable"} for kind, item in intents.items()
    ]
    for item in placeholders:
        assert read(web, f"{FAVORITES}{item['id']}/", SYNTHETIC) == item
    assert read(web, FAVORITES, SYNTHETIC)["results"] == list(reversed(placeholders))
    assert all(len(rows) == 2 for rows in hidden_rows(products).values())
    for item in placeholders:
        assert (
            web.delete(f"{FAVORITES}{item['id']}/", params=SYNTHETIC, headers=csrf).status_code
            == 204
        )


def test_v11_detail_accepts_each_exact_limit_and_rejects_overflow(session):
    operator, web, _ = session
    product = create_product("s04-bounds-C", "C")
    cities = []
    for code in range(1, 7):
        city = City.objects.create(source=product.source, name=f"F {code}", geography="F")
        add_city_code(city, str(code))
        cities.append(str(city.pk))
    for operation in ("process_local", "retain_normalized", "display"):
        allow(operator, product, operation, data_until=(START + timedelta(days=31)).date())
    put(operator, product, "limits", make_observation(product, "1", START, "25"))
    day_30 = START + timedelta(days=30)
    q = scoped(
        product,
        cities=cities[0],
        indicators="AQI",
        mode="window",
        report_kind="hourly",
        window_definition="hourly_label_unspecified",
        start=START.isoformat(),
        end=day_30.isoformat(),
    )
    indicators = ",".join(INDICATORS["C"])
    one_hour = (START + HOUR).isoformat()
    five_cities = ",".join(cities[:5])
    for overrides in (
        {},  # Exactly 30 days for a single series.
        {"cities": five_cities},  # Exactly 5 * 1 * 720 = 3600 cells.
        {"cities": five_cities, "indicators": indicators, "end": (START + 102 * HOUR).isoformat()},
    ):
        payload = read(web, DETAIL, q | overrides)
        [row] = payload["results"]
        assert payload["state"] == "ok" and row["value"] == "25.000000"
    for overrides in (
        {"cities": ",".join(cities), "end": one_hour},  # Six objects.
        {"indicators": indicators + ",extra", "end": one_hour},  # Eight distinct indicators.
        {"end": (day_30 + timedelta(microseconds=1)).isoformat()},
        {"cities": five_cities, "indicators": indicators, "end": (START + 103 * HOUR).isoformat()},
    ):
        assert read(web, DETAIL, q | overrides, 400) == {"code": "query_too_large"}
