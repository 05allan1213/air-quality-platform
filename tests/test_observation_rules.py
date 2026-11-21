from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, localcontext

import pytest
from observations.normalization import (
    CONCENTRATIONS,
    STANDARD_2012,
    STANDARD_2026,
    BatchContext,
    ProductProfile,
    Rejected,
    normalize,
)

BASE = ProductProfile("S", "Asia/Shanghai", "F confirmed", "hourly_right_label")
CTX = BatchContext(datetime(2026, 4, 2, tzinfo=UTC))
RAW = {
    "object_code": "001",
    "source_time": "2024-01-02T00:00:00+08:00",
    "indicator": "CO",
    "raw_unit": "µg/m³",
    "raw_value": "1250",
}


def test_co_conversion_keeps_timezone_and_anchor_evidence():
    sample = normalize(BASE, RAW, CTX)
    assert str(sample.value) == "1.250"
    assert (sample.unit, sample.raw_unit) == ("mg/m³", "µg/m³")
    assert sample.fetched_at == CTX.fetched_at
    assert sample.time.source_time == RAW["source_time"]
    assert sample.time.window_start == datetime(2024, 1, 1, 15, tzinfo=UTC)
    assert (
        sample.time.observed_at_utc
        == sample.time.window_end
        == datetime(2024, 1, 1, 16, tzinfo=UTC)
    )
    assert sample.time.source_local_date == date(2024, 1, 2)
    assert sample.time.local_report_date is None
    assert sample.standard_series == "not_applicable"
    direct = normalize(BASE, RAW | {"raw_value": "1.25", "raw_unit": "mg/m³"}, CTX)
    assert direct.value == sample.value


@pytest.mark.parametrize(
    ("shape", "window", "indicator", "anchor", "length", "report"),
    [
        ("C", "hourly_left_label", "O3", RAW["source_time"], 1, "hourly"),
        ("C", "rolling_8h_right_label", "O3", RAW["source_time"], 8, "rolling_8h"),
        ("C", "rolling_24h_right_label", "O3", RAW["source_time"], 24, "rolling_24h"),
        ("D", "local_daily", "O3", "2024-01-02", 24, "daily"),
    ],
)
def test_reporting_window_and_anchor(shape, window, indicator, anchor, length, report):
    record = normalize(
        replace(BASE, shape=shape, window_definition=window),
        RAW | {"source_time": anchor, "indicator": indicator, "raw_value": "3"},
        CTX,
    )
    t = record.time
    assert t.report_kind == report
    assert t.window_end - t.window_start == timedelta(hours=length)
    label = datetime(2024, 1, 1, 16, tzinfo=UTC)
    if shape == "D":
        assert t.local_report_date == date(2024, 1, 2) and t.observed_at_utc is None
        assert t.window_start == label
    else:
        assert t.observed_at_utc == label and t.local_report_date is None
        assert (t.window_start if t.label_position == "left" else t.window_end) == label


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"source_time": "2024-01-02T00:00:00"}, "invalid_time"),
        ({"source_time": "2024-01-02T00:00:00Z"}, "invalid_source_offset"),
        ({"source_time": "2024-01-02T00:00:00+08:60"}, "invalid_time"),
        ({"source_time": "2024-01-02T00:03:00+08:00"}, "invalid_window"),
        ({"raw_unit": "ppm"}, "invalid_unit"),
        ({"raw_value": "-1"}, "invalid_value"),
        ({"raw_value": "NaN"}, "invalid_value"),
        ({"raw_value": "Infinity"}, "invalid_value"),
        ({"raw_value": "0.0001"}, "invalid_precision"),
        ({"raw_value": "1e3"}, "invalid_value"),
    ],
)
def test_noninterpretable_fields_raise_fixed_rejection_codes(changes, code):
    with pytest.raises(Rejected, match=f"^{code}$"):
        normalize(BASE, RAW | changes, CTX)


def test_missing_field_is_distinct_from_reported_missing():
    absent = RAW.copy()
    del absent["raw_value"]
    with pytest.raises(Rejected, match="^missing_key$"):
        normalize(BASE, absent, CTX)


@pytest.mark.parametrize(
    ("text", "quality", "reason", "value", "limit"),
    [
        ("", "missing", "missing_empty", None, None),
        ("NA", "missing", "missing_na", None, None),
        ("/", "missing", "missing_slash", None, None),
        ("<3", "censored", "left_censored", None, Decimal("0.003")),
        ("0", "valid", "", Decimal(0), None),
        ("125000", "suspect", "concentration_peak", Decimal(125), None),
    ],
)
def test_value_semantics_are_explicit(text, quality, reason, value, limit):
    sample = normalize(BASE, RAW | {"raw_value": text}, CTX)
    assert (sample.value, sample.quality, sample.issue, sample.censor_limit) == (
        value,
        quality,
        reason,
        limit,
    )
    assert sample.raw_value == text
    assert sample.censor_direction == ("<" if limit is not None else "")


def test_conversion_keeps_precision_under_the_callers_decimal_context():
    with localcontext(prec=3):
        sample = normalize(BASE, RAW | {"raw_value": "1250.123"}, CTX)
    assert sample.value == Decimal("1.250123")


@pytest.mark.parametrize(
    ("series", "raw", "quality", "grade", "color", "comparable"),
    [
        (STANDARD_2012, "0", "valid", 1, "#00E400", True),
        (STANDARD_2012, "501", "valid", 6, "#7E0023", True),
        (STANDARD_2026, "0", "suspect", None, "", False),
        (STANDARD_2026, "1", "valid", 1, "#00E400", True),
        (STANDARD_2026, "50", "valid", 1, "#00E400", True),
        (STANDARD_2026, "51", "valid", 2, "#FFFF00", True),
        (STANDARD_2026, "500", "valid", 6, "#7E0023", True),
        (STANDARD_2026, "501", "suspect", None, "", False),
        ("unknown", "0", "valid", None, "", False),
        ("unknown", "501", "valid", None, "", False),
    ],
)
def test_aqi_uses_evidence_from_profile_and_keeps_source_description(
    series, raw, quality, grade, color, comparable
):
    basis = "unknown" if series == "unknown" else "document_inferred"
    record = {
        "indicator": "AQI",
        "raw_unit": "1",
        "raw_value": raw,
        "source_description": "Synthetic source description",
    }
    profile = replace(
        BASE, standard_series=series, standard_basis=basis, standard_evidence="F only"
    )
    sample = normalize(profile, RAW | record, CTX)
    assert (sample.quality, sample.grade, sample.color, sample.comparable) == (
        quality,
        grade,
        color,
        comparable,
    )
    assert sample.standard_series == series
    assert sample.source_description == record["source_description"]
    earlier = BatchContext(datetime(2026, 2, 1, tzinfo=UTC))
    assert normalize(profile, RAW | record, earlier).standard_series == sample.standard_series


def test_aqi_requires_version_evidence_and_reports_claimed_grade_conflict():
    data = RAW | {"indicator": "AQI", "raw_unit": "1", "raw_value": "50", "source_grade": 2}
    profile = replace(BASE, standard_series=STANDARD_2026, standard_basis="source_stated")
    with pytest.raises(Rejected, match="^invalid_standard_evidence$"):
        normalize(profile, data, CTX)
    sample = normalize(replace(profile, standard_evidence="F only"), data, CTX)
    assert sample.issue == "source_grade_conflict"
    assert sample.quality == "suspect" and sample.grade is None and sample.color == ""


def test_product_shapes_keep_pollutant_grade_and_concentration_distinct():
    for indicator in CONCENTRATIONS:
        data = RAW | {"indicator": indicator, "raw_value": "2"}
        sample = normalize(replace(BASE, shape="C"), data, CTX)
        assert sample.indicator == indicator and sample.unit != "grade"
        grade = data | {"indicator": f"{indicator}Level", "raw_unit": "grade"}
        assert normalize(replace(BASE, shape="L"), grade, CTX).unit == "grade"
        for shape in ("L", "T"):
            with pytest.raises(Rejected, match="^invalid_indicator$"):
                normalize(replace(BASE, shape=shape), data, CTX)
    with pytest.raises(Rejected, match="^invalid_indicator$"):
        normalize(
            replace(BASE, shape="H", window_definition="hourly_label_unspecified"),
            RAW | {"indicator": "AQI", "raw_unit": "1"},
            CTX,
        )
