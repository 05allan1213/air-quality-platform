"""S08 research series discovery over real Session-authenticated HTTP."""

from datetime import timedelta

import pytest
from accounts.models import AccountState
from observations.policy import revoke_grant
from s05_http import loopback
from s05_support import START, authorize, candidate, city, grant, ingest, product, site
from s07_contract import check_response, specification

pytestmark = pytest.mark.django_db(transaction=True)

URL = "/api/v1/catalog/series/"
DAY = timedelta(days=1)


def test_series_openapi_declares_discovery_responses():
    responses = specification()["paths"][URL]["get"]["responses"]
    assert set(responses) == {"200", "400", "403", "405", "406", "409", "500", "503"}


@pytest.fixture
def actors(django_user_model):
    operator = django_user_model.objects.create_superuser("s08-series-operator")
    operator.set_password("s05-test-only")
    operator.save(update_fields=["password"])
    reader = django_user_model.objects.create_user("s08-series-reader", password="s05-test-only")
    AccountState.objects.create(user=operator)
    AccountState.objects.create(user=reader)
    return operator, reader


def query(start=START, end=START + DAY, **extra):
    values = {
        "purpose": "analysis",
        "method": "statistics",
        "start": start.isoformat(),
        "end": end.isoformat(),
        "scope": "synthetic",
    }
    return values | extra


def test_series_requires_derived_analysis_rights_and_returns_bounded_options(actors):
    operator, reader = actors
    p = product("series-city", shape="C")
    target = city(p)
    grants = authorize(operator, p, object_code="001")
    ingest(operator, p, [candidate(p, at=START + timedelta(hours=2), indicator="CO")])

    with loopback(reader) as (web, _):
        response = web.get(URL, params=query(city=target.pk, product=p.pk))
    body = check_response(response, URL, 200)
    assert body["page"] == {"page_size": 25, "next_cursor": None}
    assert body["results"] == [
        {
            "source": p.source.namespace,
            "product": p.pk,
            "shape": "C",
            "city": target.pk,
            "indicator": "CO",
            "unit": "mg/m³",
            "report_kind": "hourly",
            "window_definition": "hourly_label_unspecified",
            "standard_series": "not_applicable",
            "source_timezone": "UTC",
            "permitted_from": START.isoformat(),
            "permitted_until": (START + DAY).isoformat(),
        }
    ]
    with loopback(reader) as (web, _):
        maximum_page = web.get(URL, params=query(city=target.pk, product=p.pk, page_size="50"))
    assert len(check_response(maximum_page, URL, 200)["results"]) == 1

    revoke_grant(operator.pk, grants["analyze"].pk, "remove analysis permission")
    with loopback(reader) as (web, _):
        denied = web.get(URL, params=query(city=target.pk, product=p.pk))
    check_response(denied, URL, 403, code="use_denied")


def test_series_cursor_is_stable_under_unpermitted_catalog_changes(actors):
    operator, reader = actors
    p = product("series-cursor", shape="C")
    first_city = city(p)
    grants = authorize(operator, p, object_code="001")
    grant(operator, p, "process_local")
    grant(operator, p, "retain_normalized")
    ingest(
        operator,
        p,
        [
            candidate(p, "001", START + timedelta(hours=1), indicator="CO"),
            candidate(p, "001", START + timedelta(hours=2), indicator="NO2"),
        ],
    )
    with loopback(reader) as (web, _):
        first_page = web.get(
            URL,
            params=query(product=p.pk, page_size="1"),
        )
        cursor = check_response(first_page, URL, 200)["page"]["next_cursor"]
        assert cursor

        changed_method = web.get(
            URL,
            params=query(method="pearson", product=p.pk, page_size="1", cursor=cursor),
        )
        check_response(changed_method, URL, 400, code="invalid_cursor")
        changed_scope = web.get(
            URL,
            params=query(scope="formal", product=p.pk, page_size="1", cursor=cursor),
        )
        check_response(changed_scope, URL, 400, code="invalid_cursor")
        changed_size = web.get(
            URL,
            params=query(product=p.pk, page_size="2", cursor=cursor),
        )
        check_response(changed_size, URL, 400, code="invalid_cursor")

        city(p, "002", geography="F-other-city", name="Unpermitted")
        ingest(
            operator,
            p,
            [candidate(p, "002", START + timedelta(hours=3), indicator="O3")],
        )
        next_page = web.get(
            URL,
            params=query(
                product=p.pk,
                page_size="1",
                cursor=cursor,
            ),
        )

    result = check_response(next_page, URL, 200)["results"]
    assert len(result) == 1
    assert result[0]["city"] == first_city.pk
    assert result[0]["indicator"] == "NO2"

    ingest(
        operator,
        p,
        [candidate(p, "001", START + timedelta(hours=3), indicator="PM2.5")],
    )
    with loopback(reader) as (web, _):
        changed_data = web.get(URL, params=query(product=p.pk, page_size="1", cursor=cursor))
    check_response(changed_data, URL, 409, code="page_changed")

    with loopback(operator) as (web, _):
        other_user = web.get(URL, params=query(product=p.pk, page_size="1", cursor=cursor))
    check_response(other_user, URL, 400, code="invalid_cursor")

    revoke_grant(operator.pk, grants["analyze"].pk, "remove analysis permission")
    with loopback(reader) as (web, _):
        revoked = web.get(URL, params=query(product=p.pk, page_size="1", cursor=cursor))
    check_response(revoked, URL, 403, code="use_denied")


def test_series_can_return_empty_for_an_unsupported_indicator(actors):
    operator, reader = actors
    p = product("series-history", shape="H")
    target = site(p)
    authorize(operator, p, object_code="001")
    with loopback(reader) as (web, _):
        absent = web.get(URL, params=query(product=p.pk, site_epoch=target.pk, indicator="AQI"))
        denied = web.get(URL, params=query(product=p.pk, site_epoch=target.pk + 1))
    assert check_response(absent, URL, 200) == {
        "results": [],
        "page": {"page_size": 25, "next_cursor": None},
    }
    check_response(denied, URL, 403, code="use_denied")


def test_series_rejects_invalid_use_shape_and_oversized_page(actors):
    _, reader = actors
    with loopback(reader) as (web, _):
        bad_method = web.get(URL, params=query(method="details"))
        oversized = web.get(URL, params=query(page_size="51"))
        duplicate = web.get(
            URL
            + "?"
            + "&".join([f"{key}={value}" for key, value in query().items()] + ["method=compare"])
        )
    assert bad_method.status_code == 400
    assert bad_method.json() == {"code": "invalid_request"}
    assert oversized.status_code == 400
    assert oversized.json() == {"code": "query_too_large"}
    assert duplicate.status_code == 400
    assert duplicate.json() == {"code": "invalid_request"}
