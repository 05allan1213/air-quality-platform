import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from urllib.parse import parse_qs

import httpx
import pytest
from collection.adapters import WireError, cnemc, wire

TARGET = datetime(2026, 4, 1, 16, tzinfo=UTC)
STOP = TARGET + timedelta(hours=1)
CODE = "999999"
AREA = "合成城市"
STAMP = "2026-04-02T00:00:00"
ALL = {"mode": "all"}
ROW = {"CityCode": CODE, "Area": AREA, "TimePoint": STAMP, "AQI": 43}
STATION = {**ROW, "StationCode": "0201F", "PositionName": "合成站"}
AQI_STANDARD = {
    "series": "HJ633-2026",
    "basis": "document_inferred",
    "evidence": "测试依据",
    "from": "2026-04-02",
    "until": "2026-04-03",
}


def dotnet(at):
    return f"/Date({int(at.timestamp() * 1000)})/"


def parse(endpoint, packet, *, start=TARGET, end=STOP, **kwargs):
    return cnemc.parse(
        endpoint,
        packet if isinstance(packet, bytes) else json.dumps(packet).encode(),
        STOP,
        start=start,
        end=end,
        **{"selection": ALL, **kwargs},
    )


def fields(row):
    return {m.indicator: m for m in row.fields}


class Stream(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield b"[]"


@pytest.fixture
def requests(monkeypatch):
    caught = []

    def respond(request):
        caught.append(request)
        return httpx.Response(200, headers={"Content-Type": "application/json"}, stream=Stream())

    def transport(*, retries, verify, trust_env):
        assert (retries, verify, trust_env) == (0, True, False)
        return httpx.MockTransport(respond)

    monkeypatch.setattr(httpx, "AsyncHTTPTransport", transport)
    return caught


@pytest.mark.parametrize(
    ("endpoint", "method", "path", "query", "form"),
    [
        ("A01", "GET", "/CityData/GetAllCityRealTimeAQIModels", {}, {}),
        ("A03", "POST", "/CityData/GetAQIDataPublishLiveInfo", {"cityCode": CODE}, {}),
        ("A04", "GET", "/CityData/GetAQIDataPublishLive", {"cityName": AREA}, {}),
        (
            "A05",
            "POST",
            "/HourChangesPublish/GetCityRealTimeAqiHistoryByCondition",
            {"citycode": CODE},
            {},
        ),
        ("A07", "POST", "/HourChangesPublish/GetAllAQIPublishLive", {}, {}),
        (
            "A10",
            "POST",
            "/HourChangesPublish/GetAQIHistoryByConditionHis",
            {},
            {"date": ["2026-04-02 00:00:00"]},
        ),
    ],
)
def test_request_contract(settings, requests, endpoint, method, path, query, form):
    settings.CNEMC_MODE = "limited"
    body, fetched = cnemc.fetch(endpoint, CODE, AREA, TARGET, synthetic=False)
    (request,) = requests
    assert body == b"[]" and fetched.tzinfo is UTC
    assert request.method == method and request.url.path == path
    assert request.url.scheme == "https" and request.url.host == "air.cnemc.cn"
    assert request.url.port == 18007 and dict(request.url.params) == query
    assert parse_qs(request.content.decode()) == form
    if form:
        assert request.headers["Content-Type"] == "application/x-www-form-urlencoded"
    else:
        assert request.content == b""
    assert request.headers["Accept-Encoding"] == "identity"
    assert {"X-S03-Target", "Authorization", "Cookie"}.isdisjoint(request.headers)


def test_switches_and_invalid_params_do_not_send(settings, requests):
    settings.CNEMC_MODE = "off"
    settings.CNEMC_FIXTURE_HTTP = False
    with pytest.raises(WireError, match="unregistered_profile"):
        cnemc.fetch("A01", "", "", TARGET, synthetic=False)
    with pytest.raises(WireError, match="unregistered_profile"):
        cnemc.fetch("A01", "", "", TARGET, synthetic=True)
    settings.CNEMC_MODE = "periodic"
    for invalid in ("0", "CITY-F", "123456\n", "123456&other=1"):
        with pytest.raises(WireError, match="invalid_target"):
            cnemc.fetch("A03", invalid, AREA, TARGET, synthetic=False)
    with pytest.raises(WireError, match="invalid_target"):
        cnemc.fetch("A10", "", "", TARGET + timedelta(minutes=1), synthetic=False)
    assert requests == []
    settings.CNEMC_FIXTURE_HTTP = True
    cnemc.fetch("A03", "CITY-F", AREA, TARGET, synthetic=True)
    assert requests[0].url.host == "s03-origin"


def test_shadowed_indicators_do_not_reach_normalization(monkeypatch):
    seen = []
    original = wire._measurement

    def record(indicator, raw, code, *args, **kwargs):
        assert set(raw) == {"AQI"}
        seen.append((code, indicator))
        return original(indicator, raw, code, *args, **kwargs)

    monkeypatch.setattr(wire, "_measurement", record)
    packet = [
        STATION | {"PM2_5": 80, "CO": 0, "O3_8h": 75},
        STATION | {"StationCode": "0102F", "PositionName": {"unexpected": "value"}},
    ]
    parsed = parse("A07", packet, selection={"mode": "include", "objects": {"0201F": ["AQI"]}})
    assert parsed.issues == () and seen == [("0201F", "AQI")]
    assert tuple(fields(parsed.rows[0])) == ("AQI",)
    assert not parse("A07", packet, selection=None).rows


def test_projection_keeps_levels_concentrations_and_trend_distinct():
    packet = ROW | {"PM2_5Level": 2, "PM2_5": 0, "CO": "0.31", "O3_8h": 75}
    (city_list,) = parse("A01", [packet]).rows
    (city_live,) = parse("A03", packet, city_code=CODE, city_name=AREA).rows
    (trend,) = parse("A05", [packet], city_code=CODE, city_name=AREA).rows
    listed, live = fields(city_list), fields(city_live)
    assert listed["PM2.5Level"].data.unit == "grade" and "PM2.5" not in listed
    assert live["PM2.5"].data.unit == "µg/m³" and live["PM2.5"].data.value == 0
    assert live["CO"].data.unit == "mg/m³" and live["CO"].data.value == Decimal("0.31")
    assert not live["O3"].present and live["O3"].issue == "missing_key"
    assert tuple(fields(trend)) == ("AQI",)


def test_c_checks_array_shape_city_scope_and_timestamp():
    with pytest.raises(WireError, match="invalid_shape"):
        parse("A03", [ROW], city_code=CODE, city_name=AREA)
    with pytest.raises(WireError, match="city_mismatch"):
        parse(
            "A03",
            ROW | {"CityCode": "111111"},
            city_code=CODE,
            city_name=AREA,
            selection={"mode": "include", "objects": {CODE: ["AQI"]}},
        )
    with pytest.raises(WireError, match="invalid_window"):
        parse("A03", ROW | {"TimePoint": "2026-04-02T00:01:00"}, city_code=CODE, city_name=AREA)


def test_missing_null_markers_and_censor_have_distinct_semantics():
    packet = ROW | {"PM2_5": None, "PM10": "", "SO2": "NA", "NO2": "/", "CO": "<3"}
    (row,) = parse("A03", packet, city_code=CODE, city_name=AREA).rows
    values = fields(row)
    assert values["PM2.5"].issue == "missing_null"
    assert values["PM10"].issue == "missing_empty"
    assert values["SO2"].issue == "missing_na"
    assert values["NO2"].issue == "missing_slash"
    assert values["CO"].data.censor_limit == 3 and values["CO"].data.censor_direction == "<"
    assert values["O3"].issue == "missing_key" and not values["O3"].present
    assert all(values[indicator].present for indicator in ("PM2.5", "PM10", "SO2", "NO2", "CO"))


def test_known_grade_conflict_unknown_description_and_absent_optional_grade():
    packet = ROW | {"AqiLevel": 5, "Quality": "源类别"}
    (known,) = parse("A01", [packet], standard=AQI_STANDARD).rows
    (invalid_grade,) = parse("A01", [packet | {"AqiLevel": 8}], standard=AQI_STANDARD).rows
    assert invalid_grade.source_grade is None
    assert fields(invalid_grade)["AQI"].issue == "source_grade_conflict"
    (unknown,) = parse("A01", [packet]).rows
    (live,) = parse("A03", ROW, city_code=CODE, city_name=AREA, standard=AQI_STANDARD).rows
    conflict = fields(known)["AQI"].data
    assert conflict.quality == "suspect" and conflict.issue == "source_grade_conflict"
    assert conflict.grade is None and conflict.color == ""
    source = fields(unknown)["AQI"].data
    assert source.quality == "valid" and source.source_description == "源类别"
    assert source.grade is None and source.color == "" and not source.comparable
    verified = fields(live)["AQI"].data
    assert verified.grade == 1 and verified.quality == "valid"
    with pytest.raises(WireError, match="invalid_standard_evidence"):
        parse("A01", [packet], standard={"series": "HJ633-2026"})


def test_known_standard_only_applies_within_source_local_date_period():
    older = ROW | {"TimePoint": dotnet(TARGET - timedelta(hours=1))}
    current = ROW | {"TimePoint": dotnet(TARGET)}
    pending = ROW | {"TimePoint": dotnet(TARGET + timedelta(hours=24))}
    confirmed = []
    for packet, hour in ((older, -1), (current, 0), (pending, 24)):
        start = TARGET + timedelta(hours=hour)
        (row,) = parse(
            "A05",
            [packet],
            start=start,
            end=start + timedelta(hours=1),
            city_code=CODE,
            city_name=AREA,
            standard=AQI_STANDARD,
        ).rows
        confirmed.append(fields(row)["AQI"].data.standard_series)
    assert confirmed == ["unknown", "HJ633-2026", "unknown"]


def test_exact_snapshot_checks_all_labels_before_normalizing(monkeypatch):
    selection = {"mode": "include", "objects": {"0201F": ["AQI"]}}
    packet = [STATION, STATION | {"StationCode": "other", "TimePoint": dotnet(STOP)}]
    monkeypatch.setattr(wire, "_measurement", lambda *_args, **_kwargs: pytest.fail("wrong hour"))
    with pytest.raises(WireError, match="exact_target_mismatch"):
        parse("A10", packet, selection=selection)
    assert parse("A10", []).rows == ()


def test_station_source_clock_and_city_code_contracts():
    packet = STATION | {"TimePoint": dotnet(TARGET), "CityCode": int(CODE)}
    (national,) = parse("A07", [packet]).rows
    (station_city,) = parse("A04", [STATION | {"CityCode": 0}], city_code=CODE, city_name=AREA).rows
    (recovery,) = parse("A10", [{k: v for k, v in STATION.items() if k != "CityCode"}]).rows
    assert (
        national.city_code == CODE and station_city.city_code is None and recovery.city_code is None
    )
    assert national.time.observed_at_utc == station_city.time.observed_at_utc == TARGET
    assert national.time.timezone_basis == recovery.time.timezone_basis
    assert national.time.source_time == dotnet(TARGET) and recovery.time.source_time == STAMP
    assert recovery.time.source_local_date.isoformat() == "2026-04-02"
    assert fields(national)["AQI"].data.source_published_at is None
    assert parse("A07", [packet | {"CityCode": 0}]).issues == ("invalid_city_code",)
    old = parse("A07", [packet | {"TimePoint": dotnet(TARGET - timedelta(hours=1))}])
    assert old.issues == ("outside_target",) and old.rows == ()
    conflict = parse("A07", [packet | {"TimePointStr": "2026年04月02日 01时"}])
    assert conflict.issues == ("source_time_conflict",) and conflict.rows == ()


def test_trend_orders_rolling_hours_and_enforces_slot_limit():
    start = TARGET - timedelta(hours=23)
    packet = [ROW | {"TimePoint": dotnet(TARGET - timedelta(hours=h))} for h in range(24)]
    parsed = parse("A05", packet, city_code=CODE, city_name=AREA, start=start)
    assert len(parsed.rows) == 24
    assert parsed.rows[0].time.observed_at_utc == start
    assert parsed.rows[-1].time.observed_at_utc == TARGET
    with pytest.raises(WireError, match="response_too_many_hours"):
        parse(
            "A05",
            packet + [ROW | {"TimePoint": dotnet(start - timedelta(hours=1))}],
            city_code=CODE,
            city_name=AREA,
            start=start,
        )


@pytest.mark.parametrize(
    ("packet", "code"),
    [
        ([STATION] * 2049, "response_too_many_rows"),
        (b" " * (2**21 + 1), "response_too_large"),
        (b'[{"StationCode":"a","StationCode":"b"}]', "duplicate_json_key"),
        ([STATION | {"Extra": "x" * 257}], "response_field_too_long"),
    ],
)
def test_national_packet_is_bounded(packet, code):
    with pytest.raises(WireError, match=code):
        parse("A07", packet)


@pytest.mark.django_db
def test_national_station_city_code_matches_catalog_at_observation():
    from types import SimpleNamespace

    from collection.writer import identify
    from observations.models import (
        City,
        CityIdentifier,
        Product,
        SiteEpoch,
        Source,
        Station,
        StationIdentifier,
    )
    from observations.policy import Denied
    from psycopg.types.range import Range

    source = Source.objects.create(namespace="cnemc.fixture", publisher="F")
    product = Product.objects.create(
        source=source,
        code="station-national-hour",
        family="F",
        shape="S",
        level="station",
        synthetic=True,
    )
    old = TARGET - timedelta(days=1)
    live = Range(old, None, "[)")
    city = City.objects.create(source=source, geography="F-city", name=AREA)
    other = City.objects.create(source=source, geography="F-other", name=AREA)
    for code, mapped_city, period in (
        (CODE, city, live),
        ("999998", other, live),
        ("999997", city, Range(old, TARGET, "[)")),
    ):
        CityIdentifier.objects.create(
            source=source, code=code, city=mapped_city, active_period=period, evidence="F directory"
        )
    station = Station.objects.create(source=source, name=STATION["PositionName"])
    StationIdentifier.objects.create(
        source=source,
        code=STATION["StationCode"],
        station=station,
        active_period=live,
        evidence="F directory",
    )
    epoch = SiteEpoch.objects.create(
        station=station,
        city=city,
        name=station.name,
        active_period=live,
        location_evidence="F directory",
        certainty="confirmed",
    )
    run = SimpleNamespace(endpoint="A07", job=SimpleNamespace(product=product, city_id=None))
    (row,) = parse("A07", [STATION]).rows
    assert identify(run, row, for_write=True) == (epoch, city.geography)
    for bad_code, error in (("999998", "city_mismatch"), ("999997", "identity_unresolved")):
        (row,) = parse("A07", [{**STATION, "CityCode": bad_code}]).rows
        with pytest.raises(Denied, match=error):
            identify(run, row, for_write=True)
    run.endpoint = "A10"
    (row,) = parse("A10", [{**STATION, "CityCode": "999998"}]).rows
    with pytest.raises(Denied, match="city_mismatch"):
        identify(run, row, for_write=True)
    (row,) = parse("A10", [{k: v for k, v in STATION.items() if k != "CityCode"}]).rows
    assert identify(run, row, for_write=True) == (epoch, city.geography)
