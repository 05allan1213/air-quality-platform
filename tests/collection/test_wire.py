"""Wire contract regressions for S03 synthetic products (V02–V06, R/F+I)."""

import asyncio
import json
import ssl
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from collection import adapters
from collection.adapters import transport

TARGET = datetime(2026, 4, 1, 16, tzinfo=UTC)
AFTER = TARGET + timedelta(hours=1)
CODE = "F-001"
AREA = "合成城市"
STAMP = "2026-04-02T00:00:00+08:00"
ROW = {"CityCode": CODE, "Area": AREA, "TimePoint": STAMP, "AQI": 43}


def parse(shape, body, start=TARGET, end=AFTER, **kwargs):
    if not isinstance(body, bytes):
        body = json.dumps(body).encode()
    return adapters.parse(
        shape,
        body,
        AFTER,
        start=start,
        end=end,
        city_code=CODE,
        city_name="" if shape == "L" else AREA,
        **kwargs,
    )


def fields(row):
    return {m.indicator: m for m in row.fields}


def test_l_uses_grade_fields_and_shared_target_label():
    packet = [
        {
            **ROW,
            "PM2_5Level": 2,
            "NO2Level": "7",
            "COLevel": "1",
            "PM2_5": 29,
            "AqiLevel": 1,
            "Quality": "合成等级",
            "PrimaryPollutant": "PM2.5",
        },
        {**ROW, "CityCode": "F-002", "Area": "另一合成城市"},
        {**ROW, "CityCode": "0"},
        {**ROW, "TimePoint": "2026-04-01T23:00:00+08:00"},
    ]
    parsed = parse("L", packet)
    first = parsed.rows[0]
    f = fields(first)
    assert [r.object_code for r in parsed.rows] == [CODE, "F-002"]
    assert parsed.issues == ("invalid_city_code", "outside_target")
    assert f["PM2.5Level"].data.value == Decimal(2)
    assert f["COLevel"].data.unit == "grade"
    assert f["NO2Level"].present and f["NO2Level"].issue == "invalid_grade"
    assert "PM2.5" not in f
    assert first.source_grade == 1 and first.primary_pollutant == "PM2.5"
    assert f["AQI"].data.color == "" and f["AQI"].data.grade is None


def test_c_fields_distinguish_null_missing_markers_censor_and_zero():
    row = next(
        iter(
            parse(
                "C",
                b'{"CityCode":"F-001","Area":"' + AREA.encode() + b'",'
                b'"TimePoint":"2026-04-02T00:00:00","AQI":43,"PM2_5":0,"PM10":null,'
                b'"NO2":"<3","CO":3.10000e-1,"O3":"/","Quality":"synthetic"}',
            ).rows
        )
    )
    f = fields(row)
    assert row.time.observed_at_utc == TARGET
    assert row.time.source_local_date.isoformat() == "2026-04-02"
    assert f["CO"].data.value == Decimal("0.31") and f["CO"].data.unit == "mg/m³"
    assert f["PM2.5"].data.unit == "µg/m³" and f["PM2.5"].data.value == 0
    assert f["PM10"].data.quality == "missing" and f["PM10"].issue == "missing_null"
    assert f["NO2"].data.censor_limit == 3 and f["NO2"].data.value is None
    assert not f["SO2"].present and f["SO2"].issue == "missing_key"
    assert f["O3"].issue == "missing_slash"
    assert f["AQI"].data.standard_series == "unknown"
    assert row.source_description == "synthetic"


@pytest.mark.parametrize(
    ("packet", "code"),
    [
        ([], "invalid_shape"),
        ({}, "invalid_identity"),
        ({**ROW, "CityCode": "F-other"}, "city_mismatch"),
        ({**ROW, "Area": "different city"}, "city_mismatch"),
        ({**ROW, "StationCode": "station"}, "shape_mismatch"),
        ({**ROW, "TimePoint": "2026-04-01T16:00:00Z"}, "invalid_source_offset"),
        ({**ROW, "TimePoint": "2026-04-02T00:00:01+08:00"}, "invalid_window"),
        ({**ROW, "TimePointStr": "2026-04-02T01:00:00+08:00"}, "source_time_conflict"),
    ],
)
def test_c_rejects_shifted_identity_and_time(packet, code):
    with pytest.raises(adapters.WireError) as caught:
        parse("C", packet)
    assert caught.value.code == code
    assert caught.value.responded


def test_s_keeps_station_code_and_never_substitutes_rolling_values():
    sample = {**ROW, "PositionName": "同名站", "StationCode": "0001F", "CityCode": 0, "O3_8h": 75}
    parsed = parse(
        "S",
        [
            sample,
            {**sample, "StationCode": "0002F", "CityCode": CODE},
            {**sample, "StationCode": "0003F", "CityCode": "F-other"},
        ],
        window_definition="hourly_right_label",
    )
    first, second = parsed.rows
    assert (first.object_code, second.object_code) == ("0001F", "0002F")
    assert first.city_code is None and second.city_code == CODE
    assert first.station_name == second.station_name == "同名站"
    assert first.time.window_start == TARGET - timedelta(hours=1)
    assert first.time.window_end == TARGET
    assert not fields(first)["O3"].present
    assert parsed.issues == ("city_mismatch",)


def test_processing_roster_filters_before_measurement_normalization(monkeypatch):
    from collection.adapters import wire

    original = wire.normalize

    def normalize(profile, data, context):
        assert data["object_code"] == CODE
        return original(profile, data, context)

    monkeypatch.setattr(wire, "normalize", normalize)
    parsed = parse("L", [ROW, ROW | {"CityCode": "unpermitted"}], allowed_codes=[CODE])
    assert [row.object_code for row in parsed.rows] == [CODE]
    assert parsed.issues == ("outside_roster",)


def test_t_requires_primary_time_and_keeps_same_hour_conflict_inputs():
    ticks = int(TARGET.timestamp()) * 1000
    raw = f"/Date({ticks}+0800)/"
    rows = [
        {**ROW, "TimePoint": raw, "TimePointStr": "04-02 00:00", "PM2_5": 23},
        {**ROW, "TimePoint": "2026-04-01T23:00:00"},
        {**ROW, "TimePoint": raw, "AQI": 54},
        {**ROW, "TimePoint": raw, "AQI": 43},
        {**ROW, "TimePoint": "bad", "TimePointStr": "04-01 23:00"},
        {**ROW, "TimePoint": "2026-03-31T23:00:00"},
    ]
    parsed = parse("T", rows, TARGET - timedelta(hours=23))
    assert [val.fields[0].data.value for val in parsed.rows] == [43, 43, 54, 43]
    assert all(tuple(fields(val)) == ("AQI",) for val in parsed.rows)
    assert parsed.rows[0].time.source_time == raw
    assert parsed.rows[1].time.source_local_date.isoformat() == "2026-04-01"
    assert parsed.issues == ("invalid_time", "outside_target")


def test_t_rejects_more_than_24_distinct_hours():
    zone = datetime.fromisoformat(STAMP).tzinfo
    points = [
        {**ROW, "TimePoint": (TARGET - timedelta(hours=i)).astimezone(zone).isoformat()}
        for i in range(25)
    ]
    with pytest.raises(adapters.WireError, match="response_too_many_hours"):
        parse("T", points, TARGET - timedelta(hours=23))


def test_wire_limits_invalid_numbers_without_converting_to_null():
    packet = json.dumps(ROW).encode().removesuffix(b"}")
    row = parse("C", packet + b',"CO":1e90000000,"O3":true,"NO2":1e-7}').rows[0]
    assert fields(row)["CO"].issue == "invalid_precision"
    assert fields(row)["O3"].issue == "invalid_value"
    assert fields(row)["NO2"].issue == "invalid_precision"
    assert all(fields(row)[key].present for key in ("CO", "O3", "NO2"))


@pytest.mark.parametrize(
    ("body", "code"),
    [
        (b"", "invalid_body"),
        (b"<html>", "invalid_body"),
        (b"[NaN]", "invalid_body"),
        (b'{"duplicate":1,"duplicate":2}', "duplicate_json_key"),
        (b"[" * 13 + b"]" * 13, "response_too_deep"),
        (b'["' + b"a" * 257 + b'"]', "response_field_too_long"),
        (b'["\\ud800"]', "invalid_body"),
        (b"[" + b"{}," * 512 + b"{}]", "response_too_many_rows"),
        (b"x" * (2**20 + 1), "response_too_large"),
    ],
)
def test_wire_bounds(body, code):
    with pytest.raises(adapters.WireError, match=code):
        parse("L", body)


class Stream(httpx.AsyncByteStream):
    def __init__(self, chunks, *, delay=0):
        self.chunks = chunks
        self.delay = delay
        self.closed = False

    async def __aiter__(self):
        for item in self.chunks:
            await asyncio.sleep(self.delay)
            if isinstance(item, Exception):
                raise item
            yield item

    async def aclose(self):
        self.closed = True


@pytest.fixture
def mock_transport(monkeypatch, settings):
    settings.S03_FIXTURE_HTTP = True

    def install(handler):
        def factory(*, retries, verify, trust_env):
            assert (retries, verify, trust_env) == (0, True, False)
            return httpx.MockTransport(handler)

        monkeypatch.setattr(httpx, "AsyncHTTPTransport", factory)

    return install


@pytest.mark.parametrize(
    ("shape", "method", "path", "params"),
    [
        ("L", "GET", "/CityData/GetAllCityRealTimeAQIModels", {}),
        ("C", "POST", "/CityData/GetAQIDataPublishLiveInfo", {"cityCode": CODE}),
        ("S", "GET", "/CityData/GetAQIDataPublishLive", {"cityName": AREA}),
        (
            "T",
            "POST",
            "/HourChangesPublish/GetCityRealTimeAqiHistoryByCondition",
            {"citycode": CODE},
        ),
    ],
)
def test_fixture_transport_uses_fixed_requests(mock_transport, shape, method, path, params):
    calls = []
    stream = Stream([b"[", b"]"])

    def respond(req):
        calls.append(req)
        return httpx.Response(
            200, headers={"Content-Type": "application/json; charset=utf-8"}, stream=stream
        )

    mock_transport(respond)
    body, fetched = adapters.fetch(shape, CODE, AREA, TARGET, "f-exact-v1")
    (request,) = calls
    assert request.method == method and request.url.path == path
    assert request.url.host == "s03-origin" and request.url.port == 8080
    assert request.headers["X-S03-Target"] == TARGET.isoformat()
    assert request.headers["Accept-Encoding"] == "identity"
    assert dict(request.url.params) == params
    assert request.content == b"" and stream.closed
    assert body == b"[]" and fetched.tzinfo is UTC


def test_fixture_continues_to_use_legacy_query_without_target_header(mock_transport):
    def respond(req):
        assert "X-S03-Target" not in req.headers
        return httpx.Response(
            200, headers={"Content-Type": "application/json"}, stream=Stream([b"[]"])
        )

    mock_transport(respond)
    assert adapters.fetch("L", "", "", TARGET, "f-latest-v1")[0] == b"[]"


def test_disabled_fixture_and_nonfixture_profiles_do_not_open_http(mock_transport, settings):
    def unexpected(_req):
        pytest.fail("outbound request attempted without fixture registration")

    mock_transport(unexpected)
    with pytest.raises(adapters.WireError, match="unregistered_profile") as failed:
        adapters.fetch("C", CODE, AREA, TARGET, "https://external.test")
    assert not failed.value.responded
    settings.S03_FIXTURE_HTTP = False
    with pytest.raises(adapters.WireError, match="unregistered_profile"):
        adapters.fetch("C", CODE, AREA, TARGET, "f-exact-v1")


@pytest.mark.parametrize(
    ("status", "headers", "chunks", "code"),
    [
        (302, {"Location": "http://redirect.test/forbidden"}, [], "unexpected_redirect"),
        (302, {"Location": "http://[invalid"}, [], "unexpected_redirect"),
        (429, {"Retry-After": "90"}, [], "upstream_rate_limited"),
        (403, {}, [], "upstream_denied"),
        (503, {}, [], "upstream_transient"),
        (200, {"Content-Encoding": "gzip"}, [], "unsupported_encoding"),
        (200, {"Content-Type": "text/html"}, [], "invalid_content_type"),
        (200, {"Content-Length": str(2**16 + 1)}, [], "response_too_large"),
        (200, {}, [b"x" * 2**16, b"x"], "response_too_large"),
        (200, {"Content-Length": "3"}, [b"{}"], "invalid_body"),
        (200, {}, [httpx.RemoteProtocolError("incomplete")], "invalid_body"),
        (200, {"Content-Length": "0"}, [], "invalid_body"),
    ],
)
def test_transport_errors_are_bounded_and_never_follow_redirects(
    mock_transport, status, headers, chunks, code
):
    stream = Stream(chunks)
    calls = []

    def respond(req):
        calls.append(req)
        return httpx.Response(
            status, headers={"Content-Type": "application/json", **headers}, stream=stream
        )

    mock_transport(respond)
    with pytest.raises(adapters.WireError, match=code) as caught:
        adapters.fetch("C", CODE, AREA, TARGET, "f-exact-v1")
    assert len(calls) == 1 and stream.closed and caught.value.responded
    if status == 429:
        assert 89 <= (caught.value.retry_after - datetime.now(UTC)).total_seconds() <= 90


def test_slow_stream_hits_whole_attempt_deadline(mock_transport, monkeypatch):
    stream = Stream([b"["] + [b" "] * 100 + [b"]"], delay=0.01)
    monkeypatch.setattr(transport, "HTTP_DEADLINE", 0.04)

    def respond(_req):
        return httpx.Response(200, headers={"Content-Type": "application/json"}, stream=stream)

    mock_transport(respond)
    with pytest.raises(adapters.WireError, match="timeout") as caught:
        adapters.fetch("L", "", "", TARGET, "f-exact-v1")
    assert stream.closed and caught.value.responded


def test_tls_errors_map_to_source_pause_without_leaking_transport_text(mock_transport):
    def respond(_req):
        try:
            raise ssl.SSLCertVerificationError("private certificate text")
        except ssl.SSLCertVerificationError as cause:
            raise httpx.ConnectError("private certificate text") from cause

    mock_transport(respond)
    with pytest.raises(adapters.WireError, match="^transport_security$") as caught:
        adapters.fetch("C", CODE, AREA, TARGET, "f-exact-v1")
    assert not caught.value.responded and caught.value.__suppress_context__
