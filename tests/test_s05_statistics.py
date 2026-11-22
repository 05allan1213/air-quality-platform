"""S05 statistics through the official routes on isolated PostgreSQL with authored F data."""

import csv
import secrets
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from io import StringIO
from zoneinfo import ZoneInfo

import pytest
from accounts.research_exports import export_fields
from django.db import connection
from observations.models import CityObservation, StationObservation
from observations.normalization import STANDARD_2012, STANDARD_2026, Rejected
from observations.policy import revoke_grant
from s05_support import (
    DISPLAY,
    END,
    HOUR,
    START,
    authorize,
    candidate,
    city,
    grant,
    ingest,
    product,
    site,
)
from test_s05_selection import DETAIL, parent, post, session

pytestmark = pytest.mark.django_db(transaction=True)
COMPARE = "/api/v1/analysis/compare/"
STATISTICS = "/api/v1/analysis/statistics/"


@pytest.fixture
def users(django_user_model):
    op = django_user_model.objects.create_superuser("research-operator")
    user = django_user_model.objects.create_user(
        "research-reader", password=secrets.token_urlsafe(24)
    )
    return op, session(user)


def successful(web, url, payload):
    response = post(web, url, payload)
    assert response.status_code == 200, response.content
    assert "no-store" in response.headers["Cache-Control"]
    return response.json()


def with_dates(req, start, end):
    return req | {
        "selection": req["selection"] | {"start": start.isoformat(), "end": end.isoformat()}
    }


@pytest.mark.parametrize("shape", ("C", "S", "H"))
def test_v03_supported_same_layer_shapes_and_incompatibility(users, shape):
    op, web = users
    p = product("v03", shape)
    a = city(p, "101") if shape == "C" else site(p, "101")
    b = city(p, "102") if shape == "C" else site(p, "102", parent=a.city)
    authorize(op, p)
    ingest(op, p, [candidate(p, "101", value=12), candidate(p, "102", value=18)])
    req = parent(p, [a, b], method="compare", hours=1)
    result = successful(web, COMPARE, req)
    assert [(c["E"], c["O"], c["V"]) for c in result["columns"]] == [(1, 1, 1)] * 2
    assert result["P"] == 1
    assert Decimal(str(result["mean_difference"])) == -6
    assert [r["entity"] for r in result["ranking"]] == [b.pk, a.pk]
    for alternative in (
        {"units": {"CO": "µg/m³"}},
        {"standard_series": {"CO": "unknown"}},
        {"report_kind": "rolling_8h"},
    ):
        response = post(web, COMPARE, req | {"selection": req["selection"] | alternative})
        assert (response.status_code, response.json()) == (422, {"code": "incompatible_series"})
    if shape in {"S", "H"}:
        same_name_geography_different_city = site(p, "103")
        req = parent(p, [a, same_name_geography_different_city], method="compare", hours=1)
        assert post(web, COMPARE, req).json() == {"code": "incompatible_series"}


@pytest.mark.parametrize("standard", (STANDARD_2012, STANDARD_2026, "unknown"))
def test_v03_aqi_is_bound_to_explicit_source_standard(users, standard):
    op, web = users
    p = product("v03aqi", "C")
    a, b = city(p, "101"), city(p, "102")
    authorize(op, p)
    basis = (
        {
            "standard_series": standard,
            "standard_basis": "source_stated",
            "standard_evidence": "F standard",
        }
        if standard != "unknown"
        else {}
    )
    ingest(
        op, p, [candidate(p, code, indicator="AQI", unit="1", **basis) for code in ("101", "102")]
    )
    req = parent(p, [a, b], method="compare", hours=1, indicators=("AQI",))
    req["selection"]["standard_series"] = {"AQI": standard}
    if standard == "unknown":
        assert post(web, COMPARE, req).json() == {"code": "incompatible_series"}
        req = parent(p, [a], method="statistics", hours=1, indicators=("AQI",))
        report = successful(web, STATISTICS, req)
        assert report["columns"][0]["V"] == 0
        assert report["chart"]["points"][0]["value"] == 10
        assert "mean" not in report["buckets"][0]
    else:
        report = successful(web, COMPARE, req)
        assert report["P"] == 1
        assert "mean" not in report["buckets"][0]


@pytest.mark.parametrize(("offset", "pairs"), [(6, 12), (1, 17), (0, 18)])
def test_v04_threshold_uses_the_same_samples_and_drilldown(users, offset, pairs):
    op, web = users
    p = product("v04")
    a = site(p, "101")
    b = site(p, "102", parent=a.city)
    c = site(p, "103", parent=a.city)
    authorize(op, p)
    ingest(
        op,
        p,
        [candidate(p, "101", START + i * HOUR, 3, indicator="PM2.5") for i in range(18)]
        + [
            candidate(p, "102", START + i * HOUR, 0 if i == offset else 9, indicator="PM2.5")
            for i in range(offset, offset + 18)
        ]
        + [candidate(p, "103", START + 17 * HOUR, 10, indicator="PM2.5")],
    )
    req = parent(p, [a, b], method="compare", indicators=("PM2.5",))
    report = successful(web, COMPARE, req)
    assert [(i["E"], i["V"], i["valid_ratio"]) for i in report["columns"]] == [(24, 18, 0.75)] * 2
    assert (report["E_common"], report["P"], report["paired_ratio"]) == (24, pairs, pairs / 24)
    assert bool(report["ranking"]) == (pairs == 18)
    assert report["paired_points"][0]["percent_change"] is None
    assert report["paired_points"][1]["difference"] == -6
    reverse = successful(web, COMPARE, parent(p, [b, a], method="compare", indicators=("PM2.5",)))
    assert reverse["paired_points"][1]["difference"] == 6
    detail_request = req | {
        "input_version": report["input_version"],
        **report["paired_drilldown"],
        "page_size": 7,
    }
    matched = []
    while True:
        page = successful(web, DETAIL, detail_request)
        matched.extend((r["key"], r["revision"]) for r in page["results"])
        if not page["page"]["next_cursor"]:
            break
        detail_request["cursor"] = page["page"]["next_cursor"]
    with connection.cursor() as cur:
        cur.execute(
            """SELECT k.id,r.id FROM observations_stationobservation k
          JOIN observations_stationrevision r ON r.key_id=k.id AND r.is_current
          WHERE k.product_id=%s AND k.site_epoch_id IN (%s,%s)
            AND k.observed_at_utc >= %s AND k.observed_at_utc < %s
          ORDER BY k.observed_at_utc, k.site_epoch_id""",
            (p.pk, a.pk, b.pk, START + offset * HOUR, START + 18 * HOUR),
        )
        assert matched == cur.fetchall()
    three = successful(web, COMPARE, parent(p, [a, b, c], method="compare", indicators=("PM2.5",)))
    assert three["P"] == 1
    assert three["ranking"] is None


@pytest.mark.parametrize("code,expected", [("unknown", None), ("empty", 0), ("three-days", 72)])
def test_v05_distinguishes_unknown_empty_and_partial_calendar(users, code, expected):
    op, web = users
    p = product("v05")
    a = site(p, code)
    authorize(op, p)
    ingest(op, p, [candidate(p, code, START + i * HOUR, i % 10) for i in range(72)])
    report = successful(
        web, STATISTICS, parent(p, [a], method="statistics", hours=30 * 24, grain="month")
    )
    coverage = report["columns"][0]
    assert coverage["E"] == expected
    assert coverage["valid_ratio"] == (1 if expected else None)
    assert coverage["O"] == (0 if expected == 0 else 72)
    assert report["whole_period_mean"] is None
    assert (
        report["conclusion"]["state"]
        == {None: "denominator_unknown", 0: "no_expected_slots", 72: "partial_coverage"}[expected]
    )


def test_v05_independent_known_intersection_and_quality(users):
    op, web = users
    p = product("v05q")
    a = site(p, "first-day")
    b = site(p, "second-day", parent=a.city)
    authorize(op, p)
    with pytest.raises(Rejected, match="invalid_value"):
        candidate(p, "first-day", START + 6 * HOUR, "bad")
    # An already normalized invalid value is distinct from a rejected source input.
    invalid = candidate(p, "first-day", START + 4 * HOUR, "NA")
    invalid = replace(invalid, data=replace(invalid.data, quality="invalid"))
    ingest(
        op,
        p,
        [
            candidate(p, "first-day", START + i * HOUR, value)
            for i, value in enumerate((0, "NA", "<3", "61"))
        ]
        + [invalid, candidate(p, "first-day", START + 5 * HOUR, 6)],
    )
    ingest(op, p, [candidate(p, "first-day", START + 5 * HOUR, 7)])
    report = successful(web, COMPARE, parent(p, [a, b], method="compare", hours=48))
    assert (report["E_common"], report["P"], report["paired_ratio"]) == (0, 0, None)
    coverage = report["columns"][0]
    assert (coverage["E"], coverage["O"], coverage["V"]) == (24, 6, 1)
    assert coverage["exclusions"] == dict(
        gap=18, missing=1, censored=1, invalid=1, suspect=1, disputed=1, unknown=0
    )
    assert any(
        point["quality"] == "censored" and point["censor_limit"] == 3
        for point in report["chart"]["points"]
    )


@pytest.mark.parametrize(
    "hours,pairs,constant,reason",
    [
        (40, 29, False, "insufficient_coverage"),
        (36, 29, False, "insufficient_pairs"),
        (40, 30, False, "sufficient"),
        (40, 30, True, "zero_variance"),
        (41, 30, False, "insufficient_coverage"),
    ],
)
def test_v06_pearson_threshold_and_variance(users, hours, pairs, constant, reason):
    op, web = users
    p = product("v06")
    a = site(p)
    authorize(op, p)
    ingest(
        op,
        p,
        [
            r
            for i in range(pairs)
            for r in (
                candidate(p, at=START + i * HOUR, value=i),
                candidate(
                    p, at=START + i * HOUR, value=1 if constant else 3 * i + 5, indicator="PM2.5"
                ),
            )
        ],
    )
    req = parent(p, [a], method="pearson", hours=hours, indicators=("CO", "PM2.5"))
    report = successful(web, STATISTICS, req)
    assert (report["E_common"], report["P"]) == (hours, pairs)
    assert report["conclusion"]["state"] == reason
    coefficient = report["pearson"]
    if reason == "sufficient":
        assert coefficient == pytest.approx(1, abs=1e-9)
    else:
        assert coefficient is None


def test_v07_month_weights_effective_hours_and_support_matches_sql(users):
    op, web = users
    p = product("v07", "C")
    a = city(p)
    authorize(op, p)
    start, end = datetime(2024, 4, 1, tzinfo=UTC), datetime(2024, 5, 1, tzinfo=UTC)
    ingest(
        op,
        p,
        [
            candidate(p, at=start + (day * 24 + i) * HOUR, value=10 if day < 20 else 40)
            for day in range(30)
            for i in range(24 if day < 20 else 12)
        ],
    )
    req = with_dates(parent(p, [a], method="statistics", grain="month"), start, end)
    report = successful(web, STATISTICS, req)
    bucket = report["buckets"][0]
    assert (bucket["E"], bucket["O"], bucket["V"], bucket["mean"], bucket["median"]) == (
        720,
        600,
        600,
        16,
        10,
    )
    assert (
        bucket["minimum"],
        bucket["maximum"],
        bucket["minimum_ties"],
        bucket["maximum_ties"],
    ) == (10, 40, 480, 120)
    rows = []
    details = req | {
        "input_version": report["input_version"],
        "page_size": 100,
        **bucket["drilldown"],
    }
    while True:
        page = successful(web, DETAIL, details)
        rows.extend(Decimal(r["value"]) for r in page["results"])
        if not page["page"]["next_cursor"]:
            break
        details["cursor"] = page["page"]["next_cursor"]
    with connection.cursor() as cur:
        cur.execute(
            """SELECT count(*), avg(r.value) FROM observations_cityobservation k
          JOIN observations_cityrevision r ON r.key_id=k.id AND r.is_current
          WHERE k.product_id=%s AND r.quality='valid' AND k.observed_at_utc>=%s AND k.observed_at_utc<%s""",
            (p.pk, start, end),
        )
        assert (len(rows), sum(rows) / len(rows)) == cur.fetchone() == (600, 16)


def test_v08_calendar_iqr_heatmap_and_restricted_series(users):
    op, web = users
    p = product("v08")
    a = site(p, start=datetime(2023, 12, 1, tzinfo=UTC))
    authorize(op, p, start=datetime(2023, 12, 1, tzinfo=UTC))
    start = datetime(2024, 2, 28, tzinfo=UTC)
    end = datetime(2024, 3, 1, tzinfo=UTC)
    values = [1 + (i % 4) for i in range(47)] + [50]
    ingest(
        op, p, [candidate(p, at=start + i * HOUR, value=value) for i, value in enumerate(values)]
    )
    req = with_dates(parent(p, [a], method="statistics", grain="season"), start, end)
    before = StationObservation.objects.filter(product=p).count()
    report = successful(web, STATISTICS, req)
    winter = report["buckets"][0]
    assert (winter["season"], winter["season_year"], winter["E"], winter["V"]) == (
        "winter",
        2024,
        48,
        48,
    )
    assert winter["box"] == {
        "q1": 1.75,
        "median": 2.5,
        "q3": 3.25,
        "lower_whisker": 1,
        "upper_whisker": 4,
        "n": 48,
    }
    assert len(report["heatmap"]) == 24 and all(c["E"] == c["V"] == 2 for c in report["heatmap"])
    assert report["months"][0]["complete_month"] is False
    assert sum(b["n"] for b in report["distribution"]) == 48
    assert report["anomalies"]["count"] == 1
    assert report["anomalies"]["candidates"][0]["value"] == 50
    assert StationObservation.objects.filter(product=p).count() == before
    assert CityObservation.objects.count() == 0
    from observations.models import UseGrant

    for g in UseGrant.objects.filter(product=p, operation="display"):
        revoke_grant(op.pk, g.pk, "statistics-only display")
    grant(
        op,
        p,
        "display",
        fields=DISPLAY - {"normalized", "provenance"},
        start=datetime(2023, 12, 1, tzinfo=UTC),
    )
    restricted = successful(web, STATISTICS, req)
    assert "chart" not in restricted and "candidates" not in restricted["anomalies"]
    request = req | {"input_version": restricted["input_version"], **winter["drilldown"]}
    assert post(web, DETAIL, request).json() == {"code": "use_denied"}


def test_v09_annual_chart_is_bounded_and_dst_uses_real_utc_hours(users):
    op, web = users
    p = product("v09", "C", timezone="America/New_York")
    a = city(p, start=START - timedelta(days=2), end=END + timedelta(days=2))
    authorize(op, p, start=START - timedelta(days=2), end=END + timedelta(days=2))
    request = with_dates(
        parent(p, [a], method="statistics", grain="month"), START + 5 * HOUR, END + 5 * HOUR
    )
    annual = successful(web, STATISTICS, request)
    assert annual["columns"][0]["E"] == 8784
    assert len(annual["chart"]["points"]) <= 1000
    assert sum(p["gaps"] for p in annual["chart"]["points"]) == 8784
    for start, end, expected in (
        (datetime(2024, 3, 10, 5, tzinfo=UTC), datetime(2024, 3, 11, 4, tzinfo=UTC), 23),
        (datetime(2024, 11, 3, 4, tzinfo=UTC), datetime(2024, 11, 4, 5, tzinfo=UTC), 25),
    ):
        report = successful(
            web, STATISTICS, with_dates(parent(p, [a], method="statistics"), start, end)
        )
        assert report["buckets"][0]["E"] == expected
        assert len({p["at"] for p in report["chart"]["points"]}) == expected


def test_hour_buckets_keep_repeated_dst_labels_distinct_in_http_details_and_csv(users):
    op, web = users
    p = product("hour-dst", "C", timezone="America/New_York")
    a, b = city(p, "A"), city(p, "B")
    authorize(op, p, aggregate_fields=export_fields("aggregate"))
    first = datetime(2024, 11, 3, 5, tzinfo=UTC)
    second, gap = first + HOUR, first + 2 * HOUR
    end = gap + HOUR / 2
    ingest(
        op,
        p,
        [
            candidate(p, "A", first - HOUR, 1),
            candidate(p, "A", first, 10),
            candidate(p, "A", second, 20),
            candidate(p, "B", second, 6),
            candidate(p, "A", gap + HOUR, 50),
        ],
    )
    req = with_dates(parent(p, [a], method="statistics", grain="hour"), first - HOUR / 2, end)
    report = successful(web, STATISTICS, req)
    bins = report["buckets"]
    assert [
        (datetime.fromisoformat(b["start"]), datetime.fromisoformat(b["end"])) for b in bins
    ] == [(first, second), (second, gap), (gap, end)]
    assert [
        datetime.fromisoformat(b["start"]).astimezone(ZoneInfo(p.source_timezone)).hour
        for b in bins
    ] == [1, 1, 2]
    expected = [(1, 1, 1, 10), (1, 1, 1, 20), (1, 0, 0, None)]
    assert [(b["E"], b["O"], b["V"], b["mean"]) for b in bins] == expected
    assert [b["exclusions"]["gap"] for b in bins] == [0, 0, 1]
    assert report["chart"]["resolution"] == "exact"
    group = req | {"input_version": report["input_version"]}
    for bucket, (_, _, _, mean) in zip(bins, expected, strict=True):
        page = successful(web, DETAIL, group | bucket["drilldown"])
        assert [Decimal(r["value"]) for r in page["results"]] == ([] if mean is None else [mean])
        assert all(r["observed_at"] == bucket["start"] for r in page["results"])

    compare_req = with_dates(
        parent(p, [a, b], method="compare", grain="hour"), first - HOUR / 2, end
    )
    compare = successful(web, COMPARE, compare_req)
    assert [(row["pos"], row["mean"]) for row in compare["buckets"]] == [
        (0, 10),
        (1, None),
        (0, 20),
        (1, 6),
        (0, None),
        (1, None),
    ]
    for bucket in compare["buckets"]:
        rows = successful(
            web,
            DETAIL,
            compare_req | {"input_version": compare["input_version"]} | bucket["drilldown"],
        )["results"]
        assert len(rows) == bucket["V"] and all(r["pos"] == bucket["pos"] for r in rows)
    assert (compare["E_common"], compare["P"]) == (3, 1)
    paired = successful(
        web,
        DETAIL,
        compare_req | {"input_version": compare["input_version"]} | compare["paired_drilldown"],
    )
    assert [(row["pos"], Decimal(row["value"])) for row in paired["results"]] == [(0, 20), (1, 6)]
    assert all(datetime.fromisoformat(row["observed_at"]) == second for row in paired["results"])

    exports = "/api/v1/me/exports/"
    ticket = post(web, exports, {"kind": "aggregate", "groups": [group]})
    assert ticket.status_code == 201, ticket.content
    download = web.get(f"{exports}{ticket.json()['id']}/download/?scope=synthetic")
    assert download.status_code == 200, download.content
    assert download.headers["Content-Type"] == "text/csv; charset=utf-8"
    rows = list(csv.DictReader(StringIO(download.content.decode())))
    assert [
        (datetime.fromisoformat(r["start"]), datetime.fromisoformat(r["end"])) for r in rows
    ] == [(first, second), (second, gap), (gap, end)]
    assert [
        (int(r["E"]), int(r["O"]), int(r["V"]), Decimal(r["mean"]) if r["mean"] else None)
        for r in rows
    ] == expected
    assert {r["input_version"] for r in rows} == {report["input_version"]}


def test_heatmap_drilldown_keeps_local_hour_and_both_dst_instants(users):
    op, web = users
    p = product("heatmap-dst", "C", timezone="America/New_York")
    a = city(p)
    authorize(op, p)
    start = datetime(2024, 11, 3, 4, tzinfo=UTC)
    end = datetime(2024, 11, 4, 7, tzinfo=UTC)
    first, second = start + HOUR, start + 2 * HOUR
    missing = start + 3 * HOUR
    ingest(
        op,
        p,
        [
            candidate(p, at=start, value=50),
            candidate(p, at=first, value=10),
            candidate(p, at=second, value=20),
            candidate(p, at=missing, value="NA"),
        ],
    )
    req = with_dates(parent(p, [a], method="statistics", grain="month"), start, end)
    report = successful(web, STATISTICS, req)
    group = req | {"input_version": report["input_version"]}
    cells = {row["hour"]: row for row in report["heatmap"]}
    cell = cells[1]
    assert (cell["month"], cell["E"], cell["O"], cell["V"], cell["mean"]) == (
        "2024-11-01",
        3,
        2,
        2,
        15,
    )
    assert cell["exclusions"]["gap"] == 1
    bucket = cell["drilldown"]["bucket"]
    assert (datetime.fromisoformat(bucket["start"]), datetime.fromisoformat(bucket["end"])) == (
        start,
        end,
    )
    assert bucket["local_hour"] == 1
    request = group | cell["drilldown"] | {"page_size": 1}
    page1 = successful(web, DETAIL, request)
    cursor = page1["page"]["next_cursor"]
    assert cursor
    page2 = successful(web, DETAIL, request | {"cursor": cursor})
    assert page2["page"]["next_cursor"] is None
    rows = page1["results"] + page2["results"]
    assert [datetime.fromisoformat(row["observed_at"]) for row in rows] == [first, second]
    assert sum(Decimal(row["value"]) for row in rows) / len(rows) == cell["mean"]
    changed_bucket = request | {"bucket": bucket | {"local_hour": 0}, "cursor": cursor}
    assert post(web, DETAIL, changed_bucket).json() == {"code": "invalid_cursor"}
    assert successful(web, DETAIL, group | cells[2]["drilldown"])["results"] == []
    observed = successful(web, DETAIL, group | cells[2]["observed_drilldown"])["results"]
    assert [(datetime.fromisoformat(row["observed_at"]), row["quality"]) for row in observed] == [
        (missing, "missing"),
    ]


def test_hour_buckets_respect_http_json_budget(users):
    op, web = users
    p = product("hour-budget", "C")
    cities = [city(p, str(i + 1)) for i in range(5)]
    authorize(op, p)
    oversized = post(web, COMPARE, parent(p, cities, method="compare", hours=30 * 24, grain="hour"))
    assert oversized.status_code == 400, len(oversized.content)
    assert oversized.json() == {"code": "query_too_large"}
    assert "no-store" in oversized.headers["Cache-Control"]
    smaller = successful(web, COMPARE, parent(p, cities, method="compare", hours=30 * 24))
    assert len(smaller["buckets"]) == 30 * 5


def test_v05_scope_truncation_and_unconfirmed_window(users):
    op, web = users
    p = product("v05scopes", "C")
    a = city(p)
    authorize(op, p, end=START + 24 * HOUR)
    req = parent(p, [a], method="statistics", hours=48)
    response = post(web, STATISTICS, req)
    assert (response.status_code, response.json()) == (403, {"code": "use_denied"})
    rolling = parent(p, [a], method="statistics", hours=24, indicators=("O3",))
    rolling["selection"]["window_definition"] = "rolling_8h_right_label"
    ingest(
        op,
        p,
        [
            candidate(
                p, at=START + 8 * HOUR, value=30, indicator="O3", window="rolling_8h_right_label"
            )
        ],
    )
    report = successful(web, STATISTICS, rolling)
    assert report["columns"][0]["E"] is None
    assert (report["columns"][0]["O"], report["columns"][0]["V"]) == (1, 1)


def test_v06_pairs_join_by_instant_not_by_row_position(users):
    op, web = users
    p = product("v06time")
    a = site(p)
    authorize(op, p)
    ingest(
        op,
        p,
        [candidate(p, at=START + i * HOUR, value=i, indicator="CO") for i in range(30)]
        + [candidate(p, at=START + (i + 20) * HOUR, value=i, indicator="PM2.5") for i in range(30)],
    )
    req = parent(p, [a], method="pearson", hours=50, indicators=("CO", "PM2.5"))
    report = successful(web, STATISTICS, req)
    assert [c["V"] for c in report["columns"]] == [30, 30]
    assert (report["P"], report["pearson"]) == (10, None)


def test_v08_cross_year_complete_months_odd_quartiles_and_empty_month(users):
    op, web = users
    p = product("v08winter", "C", timezone="Asia/Shanghai")
    start = datetime(2023, 11, 30, 16, tzinfo=UTC)
    end = datetime(2024, 2, 29, 16, tzinfo=UTC)
    a = city(p, start=start, end=end)
    authorize(op, p, start=start, end=end)
    ingest(op, p, [candidate(p, at=start + i * HOUR, value=v) for i, v in enumerate((1, 2, 5))])
    req = with_dates(parent(p, [a], method="statistics", grain="season"), start, end)
    result = successful(web, STATISTICS, req)
    winter = result["buckets"][0]
    assert winter["season"] == "winter" and winter["season_year"] == 2024
    assert winter["E"] == 2184 and winter["complete_season"] is True
    assert winter["box"] == {
        "q1": 1.5,
        "median": 2,
        "q3": 3.5,
        "lower_whisker": 1,
        "upper_whisker": 5,
        "n": 3,
    }
    assert [
        (b["E"], b["V"], b["complete_month"], b["all_received"], b["all_effective"])
        for b in result["months"]
    ] == [(744, 3, True, False, False), (744, 0, True, False, False), (696, 0, True, False, False)]
    assert len(result["heatmap"]) == 72
    assert all(
        b["mean"] is None and b["E"] == 29 and b["exclusions"]["gap"] == 29
        for b in result["heatmap"]
        if b["month"] == "2024-02-01"
    )
    assert result["anomalies"]["state"] == "insufficient_samples"


def test_v08_zero_iqr_leaves_peak_in_effective_samples(users):
    op, web = users
    p = product("v08iqr")
    a = site(p)
    authorize(op, p)
    ingest(
        op, p, [candidate(p, at=START + i * HOUR, value=50 if i == 29 else 1) for i in range(30)]
    )
    req = parent(p, [a], method="statistics", hours=30)
    result = successful(web, STATISTICS, req)
    assert result["anomalies"] == {"state": "zero_iqr", "count": None}
    assert result["columns"][0]["V"] == 30
    assert result["buckets"][-1]["maximum"] == 50
