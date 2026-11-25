"""S08 national home projections use a coherent authorized L snapshot."""

import pytest
from accounts.models import AccountState
from django.db import transaction
from django.test import Client
from observations import s04_national
from observations.models import CityRevision, CitySearchAlias
from observations.normalization import STANDARD_2012
from observations.policy import REQUIRED, revoke_grant
from s02_support import allow, create_product
from test_s04_national import (
    CATALOG,
    DERIVED,
    URL,
    call,
    city,
    http_client,
    put,
    sample,
    summary_grants,
)

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def national(django_user_model):
    operator = django_user_model.objects.create_superuser("s08-national-operator")
    reader = django_user_model.objects.create_user("s08-national-reader")
    product = create_product("s08-national-home", "L")
    for operation in ("process_local", "retain_normalized"):
        allow(operator, product, operation, fields=sorted(REQUIRED))
    return operator, reader, product


def grant_home(operator, product):
    fields = sorted(set(REQUIRED) | set(DERIVED) | set(CATALOG))
    allow(operator, product, "retain_normalized", fields=fields)
    allow(operator, product, "process_local", fields=fields)
    return allow(operator, product, "display", fields=fields)


def get_home(client, product, status=200, **params):
    response = client.get(URL, {"product": product.pk, "scope": "synthetic", **params})
    assert response.status_code == status, response.json()
    assert {"private", "no-store"} <= {
        value.strip() for value in response["Cache-Control"].split(",")
    }
    return response.json()


def second_session(user):
    client = Client(enforce_csrf_checks=True)
    csrf = client.get("/api/v1/auth/csrf/").json()["csrf_token"]
    assert (
        client.post(
            "/api/v1/auth/login/",
            {"username": user.username, "password": "This-is-S04-Fixture-9N6"},
            content_type="application/json",
            headers={"X-CSRFToken": csrf},
        ).status_code
        == 200
    )
    return client


def test_home_low_ranking_focus_map_and_stable_cursor(national):
    operator, reader, product = national
    cities = [city(product, f"{index:03}", f"City {index}") for index in range(1, 4)]
    grant_home(operator, product)
    put(
        operator,
        product,
        "home rank values including zero tie",
        sample(product, "001", 0),
        sample(product, "002", 0),
        sample(product, "003", 90),
    )
    client = http_client(reader)
    params = {
        "home": "1",
        "standard_series": STANDARD_2012,
        "order": "aqi_asc",
        "rank_preview": "aqi_asc",
        "focus_city": str(cities[2].pk),
        "map_limit": "10",
        "page_size": "1",
    }

    first = get_home(client, product, **params)
    assert first["selection"]["home"] == 1
    assert first["results"][0]["city"] == cities[0].pk
    ranking = first["ranking"]
    assert ranking["state"] == "ok"
    rows = ranking["rows"]
    assert [row["city"] for row in rows] == [city.pk for city in cities]
    assert [row["aqi"] for row in rows] == ["0.000000", "0.000000", "90.000000"]
    assert [row["grade"] for row in rows] == [1, 1, 2]
    assert [row["rank"] for row in rows] == [1, 1, 2]
    assert [row["name"] for row in rows] == ["City 1", "City 2", "City 3"]
    assert [row["geography"] for row in rows] == ["001", "002", "003"]
    assert first["focused"]["city"] == cities[2].pk
    assert first["map"] == {"state": "unavailable", "items": [], "has_more": False}
    cursor = first["page"]["next_cursor"]
    assert cursor

    next_page = get_home(
        client,
        product,
        home="1",
        standard_series=STANDARD_2012,
        order="aqi_asc",
        page_size="1",
        cursor=cursor,
        rank_preview="aqi_desc",
        focus_city=str(cities[0].pk),
    )
    assert next_page["results"][0]["city"] == cities[1].pk
    assert next_page["focused"]["city"] == cities[0].pk
    assert next_page["ranking"]["rows"][0]["city"] == cities[2].pk

    put(
        operator,
        product,
        "non-tail revision changes whole L projection",
        sample(product, "003", 80, token=2),
    )
    changed = get_home(
        client,
        product,
        409,
        home="1",
        standard_series=STANDARD_2012,
        order="aqi_asc",
        page_size="1",
        cursor=cursor,
    )
    assert changed == {"code": "page_changed"}
    assert CityRevision.objects.filter(key__product=product, is_current=True).count() == 3


def test_home_grade_filter_requires_known_series_and_keeps_denominator_unknown(national):
    operator, reader, product = national
    first_city = city(product, "001")
    city(product, "002")
    grant_home(operator, product)
    put(
        operator,
        product,
        "home grade filter",
        sample(product, "001", 20),
        sample(product, "002", 90),
    )
    client = http_client(reader)

    invalid = get_home(client, product, 400, home="1", grade="1")
    assert invalid == {"code": "invalid_request"}
    selected = get_home(
        client,
        product,
        home="1",
        standard_series=STANDARD_2012,
        grade="1",
    )
    assert [row["city"] for row in selected["results"]] == [first_city.pk]
    assert selected["summary"]["expected"] is None
    assert selected["summary"]["completeness"] == "unknown"
    assert selected["summary"]["groups"][STANDARD_2012]["ranked_city_count"] == 1


def test_home_alias_matches_only_authorized_catalog_and_never_replaces_name(national):
    operator, reader, product = national
    official = city(product, "001", "Official name")
    city(product, "002", "Other city")
    CitySearchAlias.objects.create(product=product, city=official, search_alias="Local alias")
    grant_home(operator, product)
    put(
        operator,
        product,
        "home alias search",
        sample(product, "001", 15),
        sample(product, "002", 25),
    )
    client = http_client(reader)

    home = get_home(
        client,
        product,
        home="1",
        standard_series=STANDARD_2012,
        q="Local alias",
    )
    assert [row["city"] for row in home["results"]] == [official.pk]
    assert home["results"][0]["name"] == "Official name"

    legacy = call(client, product, standard_series=STANDARD_2012, q="Local alias")
    assert legacy["results"] == []
    assert legacy["state"] == "no_data"


def test_home_only_ranks_permitted_subset_and_ignores_hidden_revision(national):
    operator, reader, product = national
    cities = [city(product, f"{index:03}") for index in range(1, 9)]
    allow(operator, product, "retain_normalized", fields=CATALOG)
    allow(operator, product, "display", fields=CATALOG)
    allow(operator, product, "display", effect="deny", object_code="008", fields=["*"])
    put(
        operator,
        product,
        "home partial summary",
        *(sample(product, f"{index:03}", index * 10) for index in range(1, 8)),
    )
    client = http_client(reader)
    params = {"home": "1", "standard_series": STANDARD_2012, "page_size": "2"}
    ordinary = get_home(client, product, **params, focus_city=str(cities[6].pk))
    assert ordinary["summary"] == {"state": "unavailable"}
    assert ordinary["ranking"] == {"state": "unavailable", "rows": []}
    assert ordinary["focused"]["city"] == cities[6].pk
    assert "rank" not in ordinary["focused"]["series"][0]
    for ranked_request in ({"grade": "1"}, {"order": "aqi_asc"}):
        assert get_home(client, product, 403, **params, **ranked_request) == {"code": "use_denied"}

    for index in range(1, 7):
        summary_grants(operator, product, object_code=f"{index:03}")
    first = get_home(client, product, **params, focus_city=str(cities[7].pk))
    assert first["focused"] is None
    assert [row["city"] for row in first["ranking"]["rows"]] == [
        row.pk for row in reversed(cities[1:6])
    ]
    assert first["summary"]["covered_city_count"] is None
    assert first["summary"]["groups"][STANDARD_2012]["ranked_city_count"] == 6
    token = first["page"]["next_cursor"]
    assert token
    other_session = second_session(reader)
    assert get_home(other_session, product, 400, **params, cursor=token) == {
        "code": "invalid_cursor"
    }

    put(operator, product, "home hidden first and revised value", sample(product, "008", 200))
    put(operator, product, "home hidden revision", sample(product, "008", 300, token=2))
    continued = get_home(client, product, **params, cursor=token)
    assert continued["ranking"] == first["ranking"]
    assert continued["summary"] == first["summary"]
    assert continued["snapshot_at"] == first["snapshot_at"]
    assert [row["city"] for row in continued["results"]] == [row.pk for row in cities[2:4]]


@pytest.mark.parametrize("change", ["session", "display"])
def test_home_rechecks_access_after_snapshot_before_releasing_response(
    national, monkeypatch, change
):
    operator, reader, product = national
    city(product, "001")
    display = grant_home(operator, product)
    state = AccountState.objects.create(user=reader)
    put(operator, product, "home post-projection revocation", sample(product, "001", 20))
    client = http_client(reader)
    read = s04_national._read_national

    def invalidate():
        if change == "session":
            AccountState.objects.filter(pk=state.pk).update(session_generation=1)
        else:
            revoke_grant(operator.pk, display.pk, "home F post-snapshot recheck")

    def project_then_invalidate(*args, **kwargs):
        result, context = read(*args, **kwargs)
        assert result["results"]
        # Release the real PostgreSQL snapshot and product locks, then let the
        # rights or session change commit before the response's final check.
        transaction.on_commit(invalidate)
        return result, context

    monkeypatch.setattr(s04_national, "_read_national", project_then_invalidate)
    code = "not_authenticated" if change == "session" else "use_denied"
    assert get_home(client, product, 403, home="1", standard_series=STANDARD_2012) == {"code": code}


@pytest.mark.parametrize("focus_last", [False, True])
def test_only_disclosed_trend_links_depend_on_trend_rights(national, monkeypatch, focus_last):
    operator, reader, product = national
    first = city(product, "001")
    last = city(product, "002")
    grant_home(operator, product)
    put(operator, product, "two home pages", sample(product, "001", 5), sample(product, "002", 8))
    trend = create_product("trend", "T", source=product.source)
    allow(operator, trend, "retain_normalized", fields=sorted(REQUIRED))
    display = allow(operator, trend, "display", fields=sorted(REQUIRED), object_code="002")
    client = http_client(reader)
    options = {"home": "1", "standard_series": STANDARD_2012, "page_size": "1"}
    if focus_last:
        options["focus_city"] = str(last.pk)
    original = get_home(client, product, **options)
    assert original["results"][0]["city"] == first.pk
    assert "trend" not in original["results"][0]["links"]
    if focus_last:
        assert original["focused"]["links"]["trend"][0]["product"] == trend.pk
    else:
        following = get_home(client, product, **options, cursor=original["page"]["next_cursor"])
        assert following["results"][0]["links"]["trend"][0]["product"] == trend.pk

    read = s04_national._read_national

    def revoke_after_snapshot(*args, **kwargs):
        result = read(*args, **kwargs)
        transaction.on_commit(lambda: revoke_grant(operator.pk, display.pk, "F trend withdrawn"))
        return result

    with monkeypatch.context() as patch:
        patch.setattr(s04_national, "_read_national", revoke_after_snapshot)
        response = get_home(client, product, 403 if focus_last else 200, **options)
    if focus_last:
        assert response == {"code": "use_denied"}
    else:
        for field in ("results", "ranking", "summary"):
            if field == "results":
                assert [row["city"] for row in response[field]] == [first.pk]
            else:
                assert response[field] == original[field]
        following = get_home(client, product, **options, cursor=original["page"]["next_cursor"])
        assert following["results"][0]["city"] == last.pk
        assert "trend" not in following["results"][0]["links"]
