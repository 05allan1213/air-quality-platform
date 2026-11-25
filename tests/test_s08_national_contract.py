"""HTTP responses for the S08 national projection match the closed API DTOs."""

from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from observations.normalization import STANDARD_2012, STANDARD_2026, UNKNOWN_STANDARD
from observations.policy import REQUIRED, revoke_grant
from s02_support import START, allow, candidate, create_product
from s07_contract import check_response, validate_schema
from test_s04_national import URL, call, city, http_client, put, sample

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize(
    ("window", "standard"),
    [("hourly_left_label", STANDARD_2012), ("hourly_right_label", UNKNOWN_STANDARD)],
)
def test_catalog_link_reopens_the_same_l_snapshot(django_user_model, window, standard):
    operator = django_user_model.objects.create_superuser("s08-catalog-operator")
    reader = django_user_model.objects.create_user("s08-catalog-reader")
    product = create_product("s08-national-discovery", "L")
    entity = city(product, "001")
    for operation in ("process_local", "retain_normalized", "display"):
        allow(operator, product, operation, fields=sorted(REQUIRED))
    at = START + timedelta(hours=3)
    evidence = (
        {"standard_basis": "source_stated", "standard_evidence": "F"}
        if standard == STANDARD_2012
        else {}
    )
    put(
        operator,
        product,
        "F catalog L anchor",
        candidate(
            product,
            indicator="AQI",
            unit="1",
            at=at,
            window=window,
            standard_series=standard,
            **evidence,
        ),
    )
    client = http_client(reader)
    directory = client.get(
        "/api/v1/catalog/objects/", {"scope": "synthetic", "product": product.pk}
    )
    (entry,) = directory.json()["results"]
    args = parse_qs(urlsplit(entry["link"]).query)
    assert args["window_definition"] == [window]
    assert args.get("standard_series") == ([standard] if standard == STANDARD_2012 else None)
    response = client.get(entry["link"])
    assert response.status_code == 200
    snapshot = response.json()
    (result,) = snapshot["results"]
    assert result["city"] == entity.pk and snapshot["snapshot_at"] == entry["observed_at"]
    assert result["series"][0]["standard_series"] == standard


@pytest.mark.parametrize("grade_only", [False, True])
def test_l_catalog_links_to_the_readable_indicator_and_its_standard(django_user_model, grade_only):
    operator = django_user_model.objects.create_superuser("s08-mixed-l-operator")
    reader = django_user_model.objects.create_user("s08-mixed-l-reader")
    product = create_product("s08-mixed-l", "L")
    entity = city(product, "001")
    for operation in ("process_local", "retain_normalized"):
        allow(operator, product, operation, fields=sorted(REQUIRED))
    allow(operator, product, "display", fields=sorted(REQUIRED), indicator="COLevel")
    if not grade_only:
        allow(operator, product, "display", fields=sorted(REQUIRED), indicator="AQI")
    put(operator, product, "catalog AQI first", sample(product, "001", 28))
    put(
        operator,
        product,
        "catalog same-stamp grade from another series",
        sample(product, "001", 2, indicator="COLevel", standard=STANDARD_2026),
    )
    client = http_client(reader)
    directory = client.get(
        "/api/v1/catalog/objects/", {"scope": "synthetic", "product": product.pk}
    )
    assert directory.status_code == 200
    (row,) = directory.json()["results"]
    assert row["city"] == entity.pk
    response = client.get(row["link"])
    assert response.status_code == 200
    (result,) = response.json()["results"]
    if grade_only:
        assert row["link"] == row["history_link"]
        value = result
        assert value["indicator"] == row["indicator"] == "COLevel"
        assert value["standard_series"] == STANDARD_2026
    else:
        assert urlsplit(row["link"]).path == URL
        (value,) = result["series"]
        assert value["indicator"] == row["indicator"] == "AQI"
        assert value["standard_series"] == STANDARD_2012
    assert value["city"] == entity.pk and value["observed_at"] == row["observed_at"]


def test_legacy_and_home_national_responses_match_openapi(django_user_model):
    operator = django_user_model.objects.create_superuser("s08-national-contract-operator")
    reader = django_user_model.objects.create_user("s08-national-contract-reader")
    product = create_product("s08-national-contract", "L")
    for operation in ("process_local", "retain_normalized"):
        allow(operator, product, operation, fields=sorted(REQUIRED))

    cities = [city(product, code, f"Contract {code}", code) for code in ("001", "002")]
    fields = sorted(set(REQUIRED) | {"catalog", "overview_summary"})
    for operation in ("retain_normalized", "process_local"):
        allow(operator, product, operation, fields=fields)
    display = allow(operator, product, "display", fields=fields)
    put(
        operator,
        product,
        "national response contract",
        sample(product, "001", 10),
        sample(product, "002", 20),
    )

    client = http_client(reader)
    legacy = call(client, product, standard_series=STANDARD_2012)
    validate_schema(legacy, {"$ref": "#/components/schemas/NationalResult"})
    assert "ranking" not in legacy and "focused" not in legacy and "map" not in legacy
    assert legacy["selection"].get("home") is None

    home = call(
        client,
        product,
        home="1",
        standard_series=STANDARD_2012,
        focus_city=str(cities[0].pk),
        map_limit="10",
    )
    validate_schema(home, {"$ref": "#/components/schemas/NationalResult"})
    assert home["focused"]["city"] == cities[0].pk
    assert home["map"] == {"state": "unavailable", "items": [], "has_more": False}
    assert all(
        "coordinates" not in item and "latitude" not in item for item in home["map"]["items"]
    )
    leaked = home.copy()
    leaked["results"] = [row.copy() for row in home["results"]]
    leaked["results"][0]["rights_version"] = 1
    with pytest.raises(AssertionError):
        validate_schema(leaked, {"$ref": "#/components/schemas/NationalResult"})

    revoke_grant(operator.pk, display.pk, "S08 contract summary-right boundary")
    allow(operator, product, "display", fields=sorted(set(REQUIRED) | {"catalog"}))
    limited = call(client, product, home="1", standard_series=STANDARD_2012)
    validate_schema(limited, {"$ref": "#/components/schemas/NationalResult"})
    assert limited["summary"] == {"state": "unavailable"}
    assert limited["ranking"] == {"state": "unavailable", "rows": []}

    denied = client.get(
        URL,
        {
            "product": product.pk,
            "scope": "synthetic",
            "home": "1",
            "standard_series": STANDARD_2012,
            "order": "aqi_asc",
        },
    )
    check_response(denied, URL, 403, method="GET")

    rejected = client.get(
        URL, {"product": product.pk, "scope": "synthetic", "rank_preview": "aqi_desc"}
    )
    check_response(rejected, URL, 400, method="GET")
