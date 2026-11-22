"""S04 L snapshots and derived releases on an isolated PostgreSQL test database."""

from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from collection.admission import cell_key
from collection.models import WorkWindow
from django.conf import settings
from django.core.cache import cache
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from observations.models import City, CityIdentifier, CityObservation, CityRevision
from observations.normalization import STANDARD_2012, STANDARD_2026, UNKNOWN_STANDARD
from observations.policy import REQUIRED, revoke_grant
from observations.s04_national import HOURLY, _hour_rows, _rankable, _selection
from observations.selection import TICK, ReadContext
from psycopg.types.range import TimestamptzRange
from s02_support import START, allow, candidate, create_product, ingest_fixture, observe_db

pytestmark = pytest.mark.django_db(transaction=True)
URL = "/api/v1/overview/national/"
HOUR = timedelta(hours=1)
DERIVED = sorted(REQUIRED | {"overview_summary"})
CATALOG = sorted(REQUIRED | {"catalog"})


def city(product, code, name=None, geography=None):
    row = City.objects.create(
        source=product.source, name=name or f"Test {code}", geography=geography or str(code)
    )
    CityIdentifier.objects.create(
        source=product.source,
        city=row,
        code=code,
        active_period=TimestamptzRange(START, START + 12 * timedelta(days=1)),
        evidence="S04 F roster",
    )
    return row


def sample(product, code, value, at=START, standard=STANDARD_2012, token=1, indicator="AQI"):
    standard_kwargs = (
        {}
        if standard == UNKNOWN_STANDARD
        else dict(standard_series=standard, standard_basis="source_stated", standard_evidence="F")
    )
    return candidate(
        product,
        code=code,
        value=str(value),
        token=token,
        at=at,
        indicator=indicator,
        unit="1" if indicator == "AQI" else "grade",
        **standard_kwargs,
    )


def put(operator, product, label, *rows):
    ingest_fixture(operator.pk, product.pk, label, "f-national-v1", rows)


def summary_grants(operator, product, **scope):
    return [
        allow(operator, product, op, fields=DERIVED, **scope) for op in ("display", "process_local")
    ]


def http_client(user):
    # Log in through real request middleware, then use the database-backed session cookie.
    cache.clear()
    user.set_password("This-is-S04-Fixture-9N6")
    user.save(update_fields=["password"])
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
    assert settings.SESSION_COOKIE_NAME in client.cookies
    return client


def call(client, product, status=200, **options):
    response = client.get(URL, {"product": product.pk, "scope": "synthetic", **options})
    data = response.json()
    assert response.status_code == status, data
    assert {"private", "no-store"} <= {
        part.strip() for part in response["Cache-Control"].split(",")
    }
    return data


def ranked(client, product, status=200, **options):
    return call(client, product, status, order="aqi_desc", standard_series=STANDARD_2012, **options)


def assert_counts(payload, covered, eligible, ranked_count):
    data = payload["summary"]
    assert data["covered_city_count"] == covered
    assert data["summary_eligible_city_count"] == eligible
    group = data["groups"][STANDARD_2012]
    assert group["ranked_city_count"] == ranked_count
    assert sum(group["distribution"].values()) == ranked_count


@pytest.fixture
def national(django_user_model):
    operator = django_user_model.objects.create_superuser("s04-national-operator")
    reader = django_user_model.objects.create_user("s04-national-reader")
    product = create_product("s04-national", "L")
    for op in ("process_local", "retain_normalized"):
        allow(operator, product, op, fields=sorted(REQUIRED))
    return operator, reader, product


def test_v07_overview_requires_explicit_full_grants_in_both_operations(national):
    operator, reader, product = national
    city(product, "001")
    put(operator, product, "one", sample(product, "001", 90))
    client = http_client(reader)
    allow(operator, product, "display", fields=["*"])
    allow(operator, product, "process_local", fields=["*"])

    def unavailable():
        payload = call(client, product)
        assert payload["summary"] == {"state": "unavailable"}
        assert "rank" not in payload["results"][0]["series"][0]
        assert ranked(client, product, 403) == {"code": "use_denied"}

    unavailable()
    display = allow(operator, product, "display", fields=DERIVED)
    unavailable()
    allow(operator, product, "process_local", fields=DERIVED)
    assert_counts(ranked(client, product), 1, 1, 1)
    revoke_grant(operator.pk, display.pk, "V07 fixture")
    unavailable()
    allow(operator, product, "display", fields=DERIVED)
    for op, extra in (
        ("process_local", {"fields": ["*"], "effect": "deny"}),
        ("process_local", {"fields": ["*"], "restrictions": {"fixture": "restrict computation"}}),
        ("display", {"fields": ["overview_summary"], "effect": "deny"}),
    ):
        veto = allow(operator, product, op, **extra)
        unavailable()
        revoke_grant(operator.pk, veto.pk, "V07 fixture")
        assert_counts(ranked(client, product), 1, 1, 1)
    allow(
        operator, product, "display", fields=["overview_summary"], effect="deny", object_code="999"
    )
    assert_counts(ranked(client, product), 1, 1, 1)


def test_v08_anchor_then_filter_and_ties(national):
    operator, reader, product = national
    a = city(product, "001", "甲市", "east")
    b = city(product, "002", "乙市", "west")
    c = city(product, "003", "丙市", "west")
    d = city(product, "004", "丁市", "other")
    product_c = create_product("s04-city", "C", source=product.source)
    for op in ("process_local", "retain_normalized", "display"):
        allow(operator, product_c, op, fields=sorted(REQUIRED))
    for op in ("retain_normalized", "display"):
        allow(operator, product, op, fields=CATALOG)
    summary_grants(operator, product)
    put(
        operator,
        product,
        "national anchor",
        sample(product, "001", 90),
        sample(product, "002", 60),
        sample(product, "003", 60),
        sample(product, "002", 80, at=START + HOUR),
        sample(product, "001", 2, indicator="PM2.5Level"),
    )
    put(
        operator,
        product_c,
        "C only and C link",
        sample(product_c, "004", 100),
        sample(product_c, "001", 70),
    )
    client = http_client(reader)
    result = ranked(client, product, at=START.isoformat())
    assert_counts(result, 3, 3, 3)
    assert result["summary"]["expected"] is None and result["summary"]["completeness"] == "unknown"
    assert [(row["city"], row["series"][0]["rank"]) for row in result["results"]] == [
        (a.pk, 1),
        (b.pk, 2),
        (c.pk, 2),
    ]
    assert result["results"][0]["levels"][0]["unit"] == "grade"
    assert d.pk not in {row["city"] for row in result["results"]}
    [link] = result["results"][0]["links"]["city"]
    assert link["product"] == product_c.pk
    assert parse_qs(urlsplit(link["href"]).query) == {
        "product": [str(product_c.pk)],
        "scope": ["synthetic"],
        "at": [START.isoformat()],
    }
    latest = call(client, product)
    selected_stamp = latest["snapshot_at"]
    assert selected_stamp.startswith("2024-01-01T01:00:00")
    assert [r["city"] for r in latest["results"]] == [b.pk]
    for filters in [{"q": "甲"}, {"geography": "east"}]:
        automatic = call(client, product, **filters)
        assert (automatic["snapshot_at"], automatic["results"]) == (selected_stamp, [])
        fixed = ranked(client, product, at=START.isoformat(), **filters)
        assert_counts(fixed, 1, 1, 1)
        assert [row["city"] for row in fixed["results"]] == [a.pk]
    empty = ranked(client, product, at=(START + 2 * HOUR).isoformat())
    assert_counts(empty, 0, 0, 0)
    assert empty["results"] == [] and empty["state"] == "no_data"
    no_anchor = call(client, product, standard_series=STANDARD_2026)
    assert no_anchor["snapshot_at"] is None and no_anchor["results"] == []


def test_v08_newest_visible_same_standard_even_when_summary_is_missing(national):
    operator, reader, product = national
    a = city(product, "001")
    b = city(product, "002")
    day2 = START + timedelta(days=1)
    summary_grants(operator, product, data_until=day2.date())
    allow(operator, product, "display", fields=sorted(REQUIRED))
    put(
        operator,
        product,
        "different days and standards",
        sample(product, "001", 90),
        sample(product, "002", 80, at=day2),
        sample(product, "001", 70, at=day2 + HOUR, standard=STANDARD_2026),
    )
    client = http_client(reader)
    current = call(client, product, standard_series=STANDARD_2012)
    assert current["snapshot_at"].startswith("2024-01-02T00:00:00")
    assert [row["city"] for row in current["results"]] == [b.pk]
    assert current["summary"] == {"state": "unavailable"}
    assert ranked(client, product, 403) == {"code": "use_denied"}
    newest = call(client, product)
    assert newest["snapshot_at"].startswith("2024-01-02T01:00:00")
    assert [row["city"] for row in newest["results"]] == [a.pk]


def test_v08_roster_is_disclosed_only_with_independent_field_authority(national):
    operator, reader, product = national
    a = city(product, "001")
    b = city(product, "002")
    summary_grants(operator, product)
    for op in ("retain_normalized", "display"):
        allow(operator, product, op, fields=CATALOG)
    expected = {
        cell_key(row.pk, START, "AQI"): [code, START.isoformat(), "AQI"]
        for row, code in ((a, "001"), (b, "002"))
    }
    WorkWindow.objects.create(
        product=product,
        scope_key="national",
        target_start=START,
        target_end=START + HOUR,
        profile="f-exact-v1",
        expected=expected,
        roster_basis="independent F expected cities",
    )
    put(operator, product, "initial roster", sample(product, "001", 90))
    client = http_client(reader)
    concealed = call(client, product)["summary"]
    assert (concealed["expected"], concealed["completeness"]) == (None, "unknown")
    for op in ("retain_normalized", "display"):
        allow(operator, product, op, fields=sorted(REQUIRED | {"collection_ledger"}))
    some = call(client, product)["summary"]
    assert (some["expected"], some["completeness"]) == (2, "partial")
    put(operator, product, "full roster", sample(product, "002", 40))
    complete = call(client, product)["summary"]
    assert (complete["expected"], complete["completeness"]) == (2, "complete")


def test_v09_hidden_rows_do_not_change_anchors_aggregates_or_next_page(national):
    operator, reader, product = national
    a = city(product, "001")
    city(product, "002")
    city(product, "003")
    summary_grants(operator, product, object_code="001")
    summary_grants(operator, product, object_code="003")
    display_1 = allow(operator, product, "display", fields=CATALOG, object_code="001")
    display_3 = allow(operator, product, "display", fields=CATALOG, object_code="003")
    allow(operator, product, "retain_normalized", fields=CATALOG)
    hidden = allow(operator, product, "display", fields=CATALOG, object_code="002")
    put(operator, product, "base", sample(product, "001", 80), sample(product, "003", 60))
    client = http_client(reader)
    first = call(client, product, page_size=1)
    assert [row["city"] for row in first["results"]] == [a.pk]
    cursor = first["page"]["next_cursor"]
    revoke_grant(operator.pk, hidden.pk, "V09 fixture")
    put(
        operator,
        product,
        "hidden",
        sample(product, "002", 100),
        sample(product, "002", 200, at=START + HOUR),
    )
    new = call(client, product, page_size=1)
    assert first["snapshot_at"] == new["snapshot_at"]
    assert first["summary"] == new["summary"]
    assert [(r["city"], r["links"]) for r in first["results"]] == [
        (r["city"], r["links"]) for r in new["results"]
    ]
    assert cursor and new["page"]["next_cursor"]
    tail = call(client, product, page_size=1, cursor=cursor)
    assert (
        tail["page"]["next_cursor"] is None
        and tail["results"][0]["series"][0]["value"] == "60.000000"
    )
    assert call(client, product, q="002")["results"] == []

    for gid in (
        product.usegrant_set.filter(operation="display")
        .exclude(pk__in=[display_1.pk, display_3.pk])
        .filter(revoked_at=None)
        .values_list("pk", flat=True)
    ):
        revoke_grant(operator.pk, gid, "V09 withdraw derived display")
    revoke_grant(operator.pk, display_1.pk, "V09 withdraw last city on page")
    assert call(client, product, 409, page_size=1, cursor=cursor) == {"code": "page_changed"}
    revoke_grant(operator.pk, display_3.pk, "V09 withdraw all display")
    assert call(client, product, 403, page_size=1, cursor=cursor) == {"code": "use_denied"}
    assert call(http_client(operator), product, 403) == {"code": "use_denied"}
    assert CityObservation.objects.filter(product=product).count() == 4


def test_v10_partial_summary_excludes_unknown_standard_from_rank(national):
    operator, reader, product = national
    a = city(product, "001")
    b = city(product, "002")
    c = city(product, "003")
    allow(operator, product, "display", fields=sorted(REQUIRED))
    one = summary_grants(operator, product, object_code="001")
    two = summary_grants(operator, product, object_code="002")
    put(
        operator,
        product,
        "three standards",
        sample(product, "001", 90),
        sample(product, "002", 120, standard=UNKNOWN_STANDARD),
        sample(product, "003", 180),
    )
    client = http_client(reader)
    all_cities = call(client, product)
    assert [r["city"] for r in all_cities["results"]] == [a.pk, b.pk, c.pk]
    assert all_cities["results"][1]["series"][0]["grade"] is None
    assert "rank" not in all_cities["results"][2]["series"][0]
    assert_counts(all_cities, None, 2, 1)
    assert [r["city"] for r in ranked(client, product)["results"]] == [a.pk]
    three = summary_grants(operator, product, object_code="003")
    assert_counts(call(client, product), 3, 3, 2)
    assert [r["city"] for r in ranked(client, product)["results"]] == [c.pk, a.pk]
    for grant in one + two + three:
        revoke_grant(operator.pk, grant.pk, "V10 fixture")
    assert call(client, product)["summary"] == {"state": "unavailable"}
    assert ranked(client, product, 403) == {"code": "use_denied"}


def test_v10_filtered_summary_requires_permission_in_the_matching_directory_scope(national):
    operator, reader, product = national
    eligible = city(product, "001", "Eligible", "east")
    display_only = city(product, "002", "Display only", "west")
    city(product, "003", "No observation", "north")
    summary_grants(operator, product, object_code="001")
    summary_grants(operator, product, object_code="003")
    allow(operator, product, "display", fields=sorted(REQUIRED))
    for op in ("retain_normalized", "display"):
        allow(operator, product, op, fields=CATALOG)
    put(
        operator,
        product,
        "filtered summary",
        sample(product, "001", 90),
        sample(product, "002", 80),
    )
    client = http_client(reader)

    assert_counts(call(client, product), None, 1, 1)
    assert [row["city"] for row in ranked(client, product)["results"]] == [eligible.pk]
    for filters in ({"q": "display"}, {"geography": "west"}):
        payload = call(client, product, **filters)
        assert [row["city"] for row in payload["results"]] == [display_only.pk]
        assert payload["summary"] == {"state": "unavailable"}
        assert "rank" not in payload["results"][0]["series"][0]
        assert ranked(client, product, 403, **filters) == {"code": "use_denied"}

    for filters in ({"q": "No observation"}, {"geography": "north"}):
        empty = ranked(client, product, **filters)
        assert empty["snapshot_at"].startswith("2024-01-01T00:00:00")
        assert empty["results"] == [] and empty["state"] == "no_data"
        assert_counts(empty, 0, 0, 0)
        no_anchor = call(
            client, product, order="aqi_desc", standard_series=STANDARD_2026, **filters
        )
        assert no_anchor["snapshot_at"] is None and no_anchor["results"] == []
        assert no_anchor["summary"]["groups"][STANDARD_2026]["ranked_city_count"] == 0


def sql_ranking(product):
    with observe_db() as pg, pg.cursor() as cursor:
        cursor.execute(
            """
            SELECT k.city_id, r.id, DENSE_RANK() OVER (ORDER BY r.value DESC)
            FROM observations_cityobservation k
            JOIN observations_cityrevision r ON r.key_id = k.id
            WHERE k.product_id = %s AND k.indicator = 'AQI' AND k.observed_at_utc = %s
              AND k.standard_series = %s AND k.window_definition = %s
              AND NOT k.disputed AND NOT k.retired AND r.is_current AND NOT r.candidate
              AND r.quality = 'valid' AND r.value IS NOT NULL
            ORDER BY r.value DESC, k.city_id ASC
            """,
            (product.pk, START, STANDARD_2012, HOURLY),
        )
        return cursor.fetchall()


def test_v11_rank_paging_comparison_and_revisions(national, django_user_model):
    operator, reader, product = national
    city(product, "001")
    city(product, "002")
    city(product, "003")
    summary_grants(operator, product)
    put(
        operator,
        product,
        "A90 B80 C70",
        sample(product, "001", 90),
        sample(product, "002", 80),
        sample(product, "003", 70),
    )
    client = http_client(reader)

    def all_pages(size):
        data = []
        next_page = {}
        while True:
            result = ranked(client, product, page_size=size, **next_page)
            data.extend(
                (r["city"], r["series"][0]["revision"], r["series"][0]["rank"])
                for r in result["results"]
            )
            next_cursor = result["page"]["next_cursor"]
            if not next_cursor:
                return data
            next_page = {"cursor": next_cursor}

    for size in (1, 2, 100):
        assert all_pages(size) == sql_ranking(product)
    first = ranked(client, product, page_size=2)
    cursor = first["page"]["next_cursor"]
    newcomer = http_client(django_user_model.objects.create_user("s04-national-second-reader"))
    assert ranked(newcomer, product, 400, page_size=2, cursor=cursor) == {"code": "invalid_cursor"}
    assert ranked(client, product, 400, page_size=1, cursor=cursor) == {"code": "invalid_cursor"}
    assert ranked(client, product, 400, page_size=2, cursor=cursor + "bad") == {
        "code": "invalid_cursor"
    }
    put(operator, product, "C moves to 95", sample(product, "003", 95, token=2))
    moved = ranked(client, product, page_size=2, cursor=cursor)
    assert moved["results"] == [] and moved["page"]["next_cursor"] is None
    assert all_pages(2) == sql_ranking(product)
    first = ranked(client, product, page_size=2)
    put(operator, product, "displayed A changes", sample(product, "001", 60, token=2))
    assert ranked(client, product, 409, page_size=2, cursor=first["page"]["next_cursor"]) == {
        "code": "page_changed"
    }
    assert all_pages(2) == sql_ranking(product)


def test_v11_validation_and_constant_query_count_with_page_size(national):
    from django.db import transaction

    operator, reader, product = national
    summary_grants(operator, product)
    codes = [f"{i:03}" for i in range(1, 36)]
    for code in codes:
        city(product, code)
    put(operator, product, "bounded list", *(sample(product, code, 70) for code in codes))
    client = http_client(reader)
    for query in (
        {"page_size": 0},
        {"at": "2024-01-01T00:00:00"},
        {"at": "2024-01-01T00:00:03Z"},
        {"standard_series": "not_applicable"},
        {"as_of": "2099-01-01T00:00:00Z"},
        {"window_definition": "rolling_8h_right_label"},
        {"order": "-source"},
        {"at": START.isoformat(), "as_of": START.isoformat()},
        {"at": [START.isoformat(), START.isoformat()]},
    ):
        assert call(client, product, 400, **query) == {"code": "invalid_request"}
    assert call(client, product, 400, page_size=101) == {"code": "query_too_large"}
    lengths = []
    for size in (1, 100):
        with CaptureQueriesContext(connection) as captured:
            response = ranked(client, product, page_size=size)
        lengths.append(len(captured))
        assert len(response["results"]) == min(size, len(codes))
    assert lengths[0] == lengths[1], lengths

    with transaction.atomic():
        _, selected, _ = _selection({"product": str(product.pk), "scope": "synthetic"})
        ctx = ReadContext(reader.pk, [product.pk], True)
        scope = ctx.scope(product, ("AQI",), start=START, end=START + TICK, summary=True)
        plan = (
            _rankable(_hour_rows(scope, selected))
            .order_by("-value", "key__city_id")
            .explain(analyze=True, buffers=True)
        )
    assert "observations_cityobservation" in plan and "Buffers:" in plan
    assert CityRevision.objects.filter(key__product=product, is_current=True).count() == len(codes)


def test_v09_other_codes_of_one_city_do_not_supply_unlicensed_names(national):
    operator, reader, product = national
    main = city(product, "001", "目录名", "east")
    CityIdentifier.objects.create(
        source=product.source,
        city=main,
        code="002",
        active_period=TimestamptzRange(START, START + timedelta(days=2)),
        evidence="F second source code",
    )
    allow(operator, product, "display", fields=sorted(REQUIRED), object_code="001")
    allow(operator, product, "retain_normalized", fields=CATALOG, object_code="002")
    allow(operator, product, "display", fields=["catalog"], object_code="002")
    put(operator, product, "permitted source code", sample(product, "001", 90))
    client = http_client(reader)

    def check_visible():
        payload = call(client, product)
        [row] = payload["results"]
        assert row["city"] == main.pk
        assert row["name"] is None and row["geography"] is None
        assert [series["standard_series"] for series in row["series"]] == [STANDARD_2012]
        assert call(client, product, q="目录名")["results"] == []
        assert call(client, product, geography="east")["results"] == []

    check_visible()
    put(operator, product, "other source code", sample(product, "002", 110, standard=STANDARD_2026))
    check_visible()


def test_v08_accepts_hour_labels_in_the_product_timezone(national):
    from dataclasses import replace
    from zoneinfo import ZoneInfo

    from django.utils import timezone
    from observations.normalization import BatchContext, ProductProfile, normalize

    operator, reader, product = national
    product.source_timezone = "Asia/Kolkata"
    product.save(update_fields=["source_timezone"])
    a = city(product, "001")
    b = city(product, "002")
    allow(operator, product, "display", fields=sorted(REQUIRED))
    anchor = START + timedelta(minutes=30)
    local_label = anchor.astimezone(ZoneInfo(product.source_timezone)).isoformat()
    utc_label = anchor.isoformat()
    profile = ProductProfile("L", product.source_timezone, "S04 F label", HOURLY)
    rows = []
    for code in ("001", "002"):
        row = normalize(
            profile,
            {
                "object_code": code,
                "source_time": local_label,
                "indicator": "AQI",
                "raw_value": "80",
                "raw_unit": "1",
                "row_locator": f"F:{code}",
            },
            BatchContext(timezone.now()),
        )
        rows.append(replace(sample(product, code, 80), data=row))
    put(operator, product, "half hour offset", *rows)
    client = http_client(reader)
    first = call(client, product, at=local_label, page_size=1)
    assert [row["city"] for row in first["results"]] == [a.pk]
    next_page = call(
        client, product, at=utc_label, page_size=1, cursor=first["page"]["next_cursor"]
    )
    assert [row["city"] for row in next_page["results"]] == [b.pk]
    assert next_page["snapshot_at"] == first["snapshot_at"]
    assert call(client, product, 400, at=START.isoformat()) == {"code": "invalid_request"}
