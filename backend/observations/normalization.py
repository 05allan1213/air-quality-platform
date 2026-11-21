"""Pure, product-specific normalization shared by controlled observation inputs."""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from samples.normalization import Rejected as Rejected
from samples.normalization import parse_time

CONCENTRATIONS = ("PM2.5", "PM10", "SO2", "NO2", "CO", "O3")
LEVELS = tuple(f"{name}Level" for name in CONCENTRATIONS)
INDICATORS = {
    "L": ("AQI", *LEVELS),
    "C": ("AQI", *CONCENTRATIONS),
    "S": ("AQI", *CONCENTRATIONS),
    "T": ("AQI",),
    "D": ("AQI", *CONCENTRATIONS),
    "H": CONCENTRATIONS,
    "F": ("AQI", *CONCENTRATIONS),
}
STANDARD_2012 = "HJ633-2012"
STANDARD_2026 = "HJ633-2026"
UNKNOWN_STANDARD = "unknown"
WINDOWS = {
    "hourly_label_unspecified": (1, "unspecified"),
    "hourly_left_label": (1, "left"),
    "hourly_right_label": (1, "right"),
    "rolling_8h_right_label": (8, "right"),
    "rolling_24h_right_label": (24, "right"),
    "local_daily": (24, "date"),
}
REPORT_KIND = {
    "rolling_8h_right_label": "rolling_8h",
    "rolling_24h_right_label": "rolling_24h",
    "local_daily": "daily",
}
NUMERIC = re.compile(r"-?[0-9]+(?:\.[0-9]+)?")
DAY = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
LOCAL_TIME = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}")
COLORS = ("#00E400", "#FFFF00", "#FF7E00", "#FF0000", "#99004C", "#7E0023")
# Local quality screening only. These thresholds do not define regulatory limits.
PEAK_LIMITS = {
    "PM2.5": 1000,
    "PM10": 2000,
    "SO2": 2000,
    "NO2": 2000,
    "CO": 60,
    "O3": 1200,
}


@dataclass(frozen=True)
class ProductProfile:
    shape: str
    source_timezone: str
    timezone_basis: str
    window_definition: str
    rule_version: str = "s02.v1"
    standard_series: str = UNKNOWN_STANDARD
    standard_basis: str = "unknown"
    standard_evidence: str = ""
    missing_markers: tuple[str, ...] = ("", "NA", "/")
    allow_naive_label: bool = False


@dataclass(frozen=True)
class BatchContext:
    fetched_at: datetime


@dataclass(frozen=True)
class NormalizedTime:
    source_time: str
    source_timezone: str
    timezone_basis: str
    observed_at_utc: datetime | None
    local_report_date: date | None
    source_local_date: date
    report_kind: str
    window_definition: str
    window_start: datetime | None
    window_end: datetime | None
    label_position: str


@dataclass(frozen=True)
class Normalized:
    object_code: str
    indicator: str
    time: NormalizedTime
    value: Decimal | None
    unit: str
    quality: str
    issue: str
    censor_limit: Decimal | None
    censor_direction: str
    raw_value: str
    raw_unit: str
    standard_series: str
    standard_basis: str
    standard_evidence: str
    source_description: str
    grade: int | None
    color: str
    comparable: bool
    rule_version: str
    fetched_at: datetime
    row_locator: str
    source_published_at: datetime | None


def normalize_time(profile: ProductProfile, raw_time: object) -> NormalizedTime:
    window = profile.window_definition
    if window not in WINDOWS:
        raise Rejected("invalid_window")
    hours, position = WINDOWS[window]
    if profile.shape == "D" and position != "date" or profile.shape != "D" and position == "date":
        raise Rejected("invalid_window")
    if profile.shape == "H" and window != "hourly_label_unspecified":
        raise Rejected("invalid_window")
    if profile.shape in {"L", "T"} and hours != 1:
        raise Rejected("invalid_window")
    if not isinstance(raw_time, str):
        raise Rejected("invalid_time")
    try:
        zone = ZoneInfo(profile.source_timezone)
        if position == "date":
            if not DAY.fullmatch(raw_time):
                raise Rejected("invalid_time")
            local_day = date.fromisoformat(raw_time)
            start_day = datetime.combine(local_day, time.min, tzinfo=zone).astimezone(UTC)
            end_day = datetime.combine(
                local_day + timedelta(days=1), time.min, tzinfo=zone
            ).astimezone(UTC)
            return NormalizedTime(
                raw_time,
                profile.source_timezone,
                profile.timezone_basis,
                None,
                local_day,
                local_day,
                "daily",
                window,
                start_day,
                end_day,
                position,
            )
        if profile.allow_naive_label and LOCAL_TIME.fullmatch(raw_time):
            naive = datetime.fromisoformat(raw_time)
            local = naive.replace(tzinfo=zone)
            at = local.astimezone(UTC)
            if (
                at.astimezone(zone).replace(tzinfo=None) != naive
                or local.utcoffset() != local.replace(fold=1).utcoffset()
            ):
                raise Rejected("ambiguous_source_time")
        else:
            at = parse_time(raw_time)
            local = at.astimezone(zone)
            if local.utcoffset() != datetime.fromisoformat(raw_time).utcoffset():
                raise Rejected("invalid_source_offset")
        if local.minute or local.second or local.microsecond:
            raise Rejected("invalid_window")
        start: datetime | None
        end: datetime | None
        if position == "left":
            start, end = at, at + timedelta(hours=hours)
        elif position == "right":
            start, end = at - timedelta(hours=hours), at
        else:
            start = end = None
    except Rejected:
        raise
    except (ValueError, OverflowError, ZoneInfoNotFoundError) as exc:
        raise Rejected("invalid_time") from exc
    return NormalizedTime(
        raw_time,
        profile.source_timezone,
        profile.timezone_basis,
        at,
        None,
        local.date(),
        REPORT_KIND.get(window, "hourly"),
        window,
        start,
        end,
        position,
    )


def measurement_unit(indicator: str, raw_unit: object) -> tuple[str, int]:
    if indicator == "AQI" and raw_unit == "1":
        return "1", 0
    if indicator in LEVELS and raw_unit == "grade":
        return "grade", 0
    if indicator == "CO":
        if raw_unit in ("µg/m³", "μg/m³"):
            return "mg/m³", 3
        if raw_unit == "mg/m³":
            return "mg/m³", 0
    elif indicator in CONCENTRATIONS and raw_unit in ("µg/m³", "μg/m³"):
        return "µg/m³", 0
    raise Rejected("invalid_unit")


def numeric_value(text: str, shift: int) -> Decimal:
    if not NUMERIC.fullmatch(text):
        raise Rejected("invalid_value")
    # Bound precision before Decimal arithmetic or writing to PostgreSQL.
    whole, _, fraction = text.lstrip("-").partition(".")
    places = len(fraction.rstrip("0")) + shift
    if places > 6 or len(whole.lstrip("0")) - shift > 12:
        raise Rejected("invalid_precision")
    value = Decimal(text)
    if value < 0:
        raise Rejected("invalid_value")
    if not shift:
        return value
    parts = value.as_tuple()
    assert isinstance(parts.exponent, int)
    return Decimal((parts.sign, parts.digits, parts.exponent - shift))


def quality_value(
    indicator: str, profile: ProductProfile, raw: str, shift: int
) -> tuple[Decimal | None, str, str, Decimal | None]:
    if raw in profile.missing_markers:
        marker = {"": "missing_empty", "NA": "missing_na", "/": "missing_slash"}
        return None, "missing", marker.get(raw, "missing_reported"), None
    if raw.startswith("<"):
        if indicator == "AQI" or indicator in LEVELS:
            raise Rejected("invalid_value")
        return None, "censored", "left_censored", numeric_value(raw[1:], shift)
    value = numeric_value(raw, shift)
    if indicator == "AQI":
        if value != value.to_integral_value():
            raise Rejected("invalid_value")
        if profile.standard_series == STANDARD_2026 and not 1 <= value <= 500:
            return value, "suspect", "aqi_outside_range", None
    elif indicator in LEVELS:
        if value != value.to_integral_value() or not 1 <= value <= 6:
            raise Rejected("invalid_grade")
    elif value > PEAK_LIMITS[indicator]:
        return value, "suspect", "concentration_peak", None
    return value, "valid", "", None


def standard(profile: ProductProfile, indicator: str) -> tuple[str, str, str]:
    if indicator != "AQI" and indicator not in LEVELS:
        return "not_applicable", "not_applicable", ""
    if profile.standard_series == UNKNOWN_STANDARD and profile.standard_basis == "unknown":
        return UNKNOWN_STANDARD, "unknown", ""
    if (
        profile.standard_series not in {STANDARD_2012, STANDARD_2026}
        or profile.standard_basis not in {"source_stated", "document_inferred"}
        or not profile.standard_evidence
    ):
        raise Rejected("invalid_standard_evidence")
    return profile.standard_series, profile.standard_basis, profile.standard_evidence


def normalize(
    product_profile: ProductProfile, raw_record: Mapping[str, object], batch_context: BatchContext
) -> Normalized:
    profile = product_profile
    indicator = raw_record.get("indicator")
    if not isinstance(indicator, str) or indicator not in INDICATORS.get(profile.shape, ()):
        raise Rejected("invalid_indicator")
    if profile.window_definition.startswith("rolling_") and indicator != "O3":
        raise Rejected("invalid_window")
    object_code = raw_record.get("object_code")
    if (
        not isinstance(object_code, str)
        or len(object_code) > 100
        or not object_code.strip()
        or not object_code.isprintable()
    ):
        raise Rejected("invalid_object")
    times = normalize_time(profile, raw_record.get("source_time"))
    raw_unit = raw_record.get("raw_unit")
    unit, shift = measurement_unit(indicator, raw_unit)
    assert isinstance(raw_unit, str)
    if "raw_value" not in raw_record:
        raise Rejected("missing_key")
    raw = raw_record["raw_value"]
    if not isinstance(raw, str) or len(raw) > 32:
        raise Rejected("invalid_value")
    series, basis, evidence = standard(profile, indicator)
    value, quality, issue, limit = quality_value(indicator, profile, raw, shift)
    description = raw_record.get("source_description", "")
    locator = raw_record.get("row_locator", "")
    if not isinstance(description, str) or len(description) > 200:
        raise Rejected("invalid_description")
    if not isinstance(locator, str) or len(locator) > 100:
        raise Rejected("invalid_locator")
    publication = raw_record.get("source_published_at")
    published_at = parse_time(publication) if publication is not None else None
    grade = None
    color = ""
    if (
        indicator == "AQI"
        and series != UNKNOWN_STANDARD
        and quality == "valid"
        and value is not None
    ):
        grade = next(
            (pos for pos, end in enumerate((50, 100, 150, 200, 300), 1) if value <= end), 6
        )
        declared_grade = raw_record.get("source_grade")
        if declared_grade is not None and (
            type(declared_grade) is not int or declared_grade != grade
        ):
            quality, issue = "suspect", "source_grade_conflict"
            grade = None
        else:
            color = COLORS[grade - 1]
    comparable = quality == "valid" and series != UNKNOWN_STANDARD
    return Normalized(
        object_code,
        indicator,
        times,
        value,
        unit,
        quality,
        issue,
        limit,
        "<" if limit is not None else "",
        raw,
        raw_unit,
        series,
        basis,
        evidence,
        description,
        grade,
        color,
        comparable,
        profile.rule_version,
        batch_context.fetched_at,
        locator,
        published_at,
    )
