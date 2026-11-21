from datetime import UTC, datetime
from decimal import Decimal

import pytest
from samples.normalization import Rejected, load_fixture, normalize, parse_time


def test_fixed_input_hand_calculation():
    sample = normalize(load_fixture())
    assert str(sample.value) == "1.250" and sample.unit == "mg/m³"
    assert sample.observed_at == sample.window_end == datetime(2001, 2, 3, tzinfo=UTC)
    assert sample.window_start == datetime(2001, 2, 2, 23, tzinfo=UTC)
    assert sample.synthetic and sample.quality == "valid"


def test_zero_is_valid():
    assert normalize(load_fixture() | {"raw_value": "0"}).value == Decimal("0.000")


@pytest.mark.parametrize(
    ("field", "raw", "code"),
    [
        ("raw_value", None, "missing_value"),
        ("raw_value", "", "missing_value"),
        ("raw_value", "<3", "censored_value"),
        ("raw_value", "-1", "invalid_value"),
        ("raw_value", "Infinity", "invalid_value"),
        ("raw_value", "NaN", "invalid_value"),
        ("raw_value", "abc", "invalid_value"),
        ("raw_value", "0.1", "invalid_value"),
        ("product", "C", "invalid_product"),
        ("synthetic", 1, "invalid_synthetic"),
        ("entity", "real-city", "invalid_entity"),
        ("source", "H", "invalid_source"),
        ("raw_unit", "mg/m³", "invalid_raw_unit"),
        ("source_timezone", "UTC", "invalid_source_timezone"),
        ("source_time", "2001-02-03T08:00:00", "invalid_time"),
        ("source_time", "9999-12-31T23:00:00Z", "invalid_time"),
        ("source_time", "2001-02-03T08:00:00Z", "invalid_source_offset"),
        ("source_time", "2001-02-03T08:30:00+08:00", "invalid_window"),
        ("window_semantics", "daily", "invalid_window_semantics"),
    ],
)
def test_invalid_input_has_no_normalized_result(field, raw, code):
    with pytest.raises(Rejected) as exc:
        normalize(load_fixture() | {field: raw})
    assert exc.value.code == code


@pytest.mark.parametrize(
    "text",
    [
        "2001-02-31T00:00:00Z",
        "2001-02-03T00:00:00+08:60",
        "2001-02-03",
        "2001-02-03T00:00:00.0000001Z",
    ],
)
def test_invalid_precise_time(text):
    with pytest.raises(Rejected, match="invalid_time"):
        parse_time(text)
