"""C and T city views with distinct products, source rights, and original receipts."""

from datetime import timedelta, timezone

import pytest
from observations.models import CityIdentifier, Receipt
from observations.normalization import CONCENTRATIONS, STANDARD_2012, STANDARD_2026
from observations.policy import revoke_grant
from psycopg.types.range import TimestamptzRange
from s02_support import END, START, allow, candidate, create_product, create_site, ingest_fixture

pytestmark = pytest.mark.django_db(transaction=True)
HOUR = timedelta(hours=1)
NS = {"scope": "synthetic"}
BASE = "/api/v1/"
BASE_FIELDS = ["normalized", "provenance"]


def aqi(product, value, at, token=2, standard=STANDARD_2012):
    return candidate(
        product,
        token=token,
        value=value,
        at=at,
        indicator="AQI",
        unit="1",
        standard_series=standard,
        standard_basis="source_stated",
        standard_evidence="F declaration",
    )


def write(actor, product, label, *entries):
    return ingest_fixture(actor.pk, product.pk, label, "s04.city.fixture", entries)[0]


def slot_rows(panel, indicator):
    return next(slot["results"] for slot in panel["slots"] if slot["indicator"] == indicator)


@pytest.fixture
def city_lab(django_user_model, client):
    operator = django_user_model.objects.create_user("s04-city-op", is_superuser=True)
    client.force_login(django_user_model.objects.create_user("s04-city-reader"))
    c = create_product("s04-city-card", shape="C")
    list_product = create_product("s04-city-list", shape="L", source=c.source)
    t = create_product("s04-city-trend", shape="T", source=c.source)
    s = create_product("s04-city-station", source=c.source)
    hidden_t = create_product("s04-city-trend-hidden", shape="T", source=c.source)
    epoch = create_site(s, code="S01")
    city = epoch.city
    CityIdentifier.objects.create(
        source=c.source,
        city=city,
        code="001",
        active_period=TimestamptzRange(START, END),
        evidence="F",
    )
    for product in (c, list_product, t, s, hidden_t):
        allow(operator, product, "process_local", fields=["normalized"])
        allow(operator, product, fields=BASE_FIELDS)
        if product != hidden_t:
            allow(operator, product, "display", fields=BASE_FIELDS)
    for operation in ("retain_normalized", "display"):
        allow(operator, s, operation, fields=BASE_FIELDS + ["catalog"])
    fields = BASE_FIELDS + [
        "primary_pollutant",
        "source_grade",
        "source_description",
        "source_time",
    ]
    allow(operator, c, fields=fields, indicator="AQI")
    metadata_grant = allow(operator, c, "display", fields=fields, indicator="AQI")
    return (
        operator,
        client,
        city,
        epoch,
        {p.shape: p for p in (c, list_product, t, s)},
        hidden_t,
        metadata_grant,
    )


def test_city_and_trend_records_are_separate_with_receipt_lineage(city_lab):
    actor, reader, city, epoch, products, denied_t, metadata_grant = city_lab
    c, list_product, t, s = (products[k] for k in "CLTS")
    initial = write(
        actor,
        c,
        "c-initial",
        aqi(c, "70", START),
        *[
            candidate(c, value="5", indicator=ind, unit="mg/m³" if ind == "CO" else "µg/m³")
            for ind in CONCENTRATIONS
        ],
    )
    known = {"primary_pollutant": "PM2.5", "source_grade": 2, "source_description": "F initial"}
    contribution = initial.receipts.get(city_revision__key__indicator="AQI")
    # Synthetic evidence for S03's independently retained, optional fields.
    contribution.collection_metadata = known
    contribution.save(update_fields=["collection_metadata"])
    write(
        actor, c, "c-sparse", aqi(c, "80", START + HOUR), candidate(c, value="7", at=START + HOUR)
    )
    write(actor, list_product, "list_product-initial", aqi(list_product, "40", START))
    write(actor, s, "s-initial", candidate(s, value="110", code="S01", indicator="AQI", unit="1"))
    write(actor, t, "t-initial", aqi(t, "90", START), aqi(t, "91", START + 2 * HOUR))
    write(actor, t, "t-overlap", aqi(t, "91", START + 2 * HOUR), aqi(t, "92", START + 3 * HOUR))
    write(actor, denied_t, "t-hidden", aqi(denied_t, "400", START + 5 * HOUR))
    city_path = BASE + f"cities/{city.pk}/"
    params = NS | {"product": c.pk, "at": START.isoformat()}
    initial_card = reader.get(city_path, params)
    assert initial_card.status_code == 200, initial_card.json()
    initial_panel = initial_card.json()
    assert len(initial_panel["slots"]) == 7
    assert all(len(slot["results"]) == 1 for slot in initial_panel["slots"])
    assert {row["product"] for slot in initial_panel["slots"] for row in slot["results"]} == {c.pk}
    aqi_row = slot_rows(initial_panel, "AQI")[0]
    assert aqi_row["value"] == "70.000000"
    assert aqi_row["revision"] == contribution.city_revision_id
    assert aqi_row["primary_pollutant"] == "PM2.5"
    assert aqi_row["source_description"] == "F initial"
    assert aqi_row["fetched_at"] == initial.fetched_at.isoformat().replace("+00:00", "Z")
    assert initial_panel["city_name"] is None

    late_batch = write(actor, c, "c-late", aqi(c, "67", START, token=1))
    late = late_batch.receipts.get()
    assert late.outcome == "late" and late.city_revision_id == contribution.city_revision_id
    late.collection_metadata = {"primary_pollutant": "SO2", "source_description": "F late"}
    late.save(update_fields=["collection_metadata"])
    current = slot_rows(reader.get(city_path, params).json(), "AQI")[0]
    lineage_fields = (*known.keys(), "fetched_at", "revision")
    assert {k: current[k] for k in lineage_fields} == {k: aqi_row[k] for k in lineage_fields}

    sparse = reader.get(city_path, NS | {"product": c.pk}).json()
    assert sparse["snapshot_at"] == (START + HOUR).isoformat().replace("+00:00", "Z")
    assert {slot["indicator"] for slot in sparse["slots"] if slot["state"] == "ok"} == {"AQI", "CO"}
    assert reader.get(city_path, params | {"as_of": START.isoformat()}).json() == {
        "code": "invalid_request"
    }
    assert reader.get(city_path, params | {"product": t.pk}).json() == {"code": "use_denied"}
    assert (
        reader.get(city_path, params | {"at": (START + 5 * HOUR).isoformat()}).json()["state"]
        == "no_data"
    )

    links = initial_panel["links"]
    assert [entry["product"] for entry in links["trend"]] == [t.pk]
    assert [entry["product"] for entry in links["stations"]] == [s.pk]
    station = reader.get(links["stations"][0]["href"]).json()["results"][0]
    assert station["site_epoch"] == epoch.pk
    assert reader.get(station["link"]).json()["results"][0]["value"] == "110.000000"
    trend = reader.get(links["trend"][0]["href"]).json()
    assert trend["anchor_basis"] == "explicit" and len(trend["points"]) == 24
    assert trend["points"][-1]["results"][0]["value"] == "90.000000"
    auto = reader.get(city_path + "trend/", NS | {"product": t.pk}).json()
    assert auto["anchor_basis"] == "latest_visible"
    assert [
        (row["value"], row["product"]) for point in auto["points"] for row in point["results"]
    ] == [
        ("90.000000", t.pk),
        ("91.000000", t.pk),
        ("92.000000", t.pk),
    ]
    assert auto["points"][-3]["state"] == "no_data"
    history = reader.get(links["history"][0]).json()["results"]
    assert {r["product"] for r in history} == {c.pk}
    assert aqi_row["revision"] in {r["revision"] for r in history}

    revoke_grant(actor.pk, metadata_grant.pk, "optional display fields withdrawn")
    restricted = slot_rows(reader.get(city_path, params).json(), "AQI")[0]
    assert restricted["primary_pollutant"] is None and restricted["source_description"] is None
    assert restricted["source_grade"] is None and restricted["source_time"] is None
    assert restricted["grade"] == 2 and restricted["fetched_at"] == aqi_row["fetched_at"]
    assert Receipt.objects.filter(
        pk=contribution.pk, collection_metadata__primary_pollutant="PM2.5"
    ).exists()


def test_city_standard_filters_only_aqi_and_does_not_duplicate_concentrations(city_lab):
    actor, reader, city, _, products, _, _ = city_lab
    product = products["C"]
    write(
        actor,
        product,
        "c-multi-standard",
        aqi(product, "70", START),
        aqi(product, "69", START, standard=STANDARD_2026),
        candidate(product, value="0"),
    )
    route = BASE + f"cities/{city.pk}/"
    query = NS | {"product": product.pk, "at": START.isoformat()}
    combined = reader.get(route, query).json()
    assert {r["standard_series"] for r in slot_rows(combined, "AQI")} == {
        STANDARD_2012,
        STANDARD_2026,
    }
    assert len(slot_rows(combined, "CO")) == 1
    filtered = reader.get(route, query | {"standard_series": STANDARD_2026}).json()
    assert [row["value"] for row in slot_rows(filtered, "AQI")] == ["69.000000"]
    assert slot_rows(filtered, "CO")[0]["value"] == "0.000000"
    # Different offsets for the same instant share the same exact snapshot.
    same = reader.get(
        route, query | {"at": START.astimezone(timezone(8 * HOUR)).isoformat()}
    ).json()
    assert slot_rows(same, "AQI")[0]["revision"] == slot_rows(combined, "AQI")[0]["revision"]
