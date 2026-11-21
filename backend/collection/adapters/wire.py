"""Parse a bounded response into normalized fixture measurements and quality codes."""

import json
import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from observations.normalization import (
    BatchContext,
    Normalized,
    NormalizedTime,
    ProductProfile,
    Rejected,
    normalize,
    normalize_time,
)

from .contract import CONTRACTS
from .transport import WireError

DOTNET = re.compile(r"/Date\((-?[0-9]{1,14})([+-][0-9]{4})?\)/")
ISO_LABEL = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T")
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
NULL_SENTINEL = "@s03-json-null"
MAX_NESTING = 12
MAX_VALUE_BYTES = 32
MAX_TEXT = 256
MAX_FIELDS = 80


@dataclass(frozen=True)
class Measurement:
    indicator: str
    data: Normalized | None
    issue: str
    present: bool


@dataclass(frozen=True)
class Row:
    object_code: str
    city_code: str | None
    area: str
    station_name: str | None
    time: NormalizedTime
    fields: tuple[Measurement, ...]
    source_description: str
    source_grade: int | None
    primary_pollutant: str


@dataclass(frozen=True)
class Parsed:
    rows: tuple[Row, ...]
    issues: tuple[str, ...]


def _bounded_structure(text):
    # Check lexical nesting before allowing the JSON decoder to allocate containers.
    depth = 0
    in_string = False
    escaped = False
    for char in text:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char in "[{":
            depth += 1
            if depth > MAX_NESTING:
                raise WireError("response_too_deep", responded=True)
        elif char in "]}":
            depth -= 1
            if depth < 0:
                raise WireError("invalid_body", responded=True)


def _pairs(pairs):
    if len(pairs) > MAX_FIELDS:
        raise WireError("response_too_many_fields", responded=True)
    result = {}
    for name, value in pairs:
        if name in result:
            raise WireError("duplicate_json_key", responded=True)
        result[name] = value
    return result


def _number(text):
    if len(text) <= MAX_VALUE_BYTES:
        try:
            return Decimal(text)
        except InvalidOperation:
            pass
    return INVALID_NUMBER


INVALID_NUMBER = object()


def _constant(_text):
    raise WireError("invalid_body", responded=True)


def _limit_values(value):
    if isinstance(value, str):
        if len(value) > MAX_TEXT:
            raise WireError("response_field_too_long", responded=True)
        # Lone surrogate escapes cannot be stored in PostgreSQL UTF-8.
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            raise WireError("invalid_body", responded=True) from None
    elif isinstance(value, list):
        for child in value:
            _limit_values(child)
    elif isinstance(value, dict):
        for key, child in value.items():
            _limit_values(key)
            _limit_values(child)


def _decode(body, limit):
    if len(body) > limit:
        raise WireError("response_too_large", responded=True)
    try:
        text = body.decode("utf-8")
        _bounded_structure(text)
        data = json.loads(
            text,
            parse_int=_number,
            parse_float=_number,
            parse_constant=_constant,
            object_pairs_hook=_pairs,
        )
    except (UnicodeError, json.JSONDecodeError):
        raise WireError("invalid_body", responded=True) from None
    _limit_values(data)
    return data


def _text(value, limit=100):
    if (
        not isinstance(value, str)
        or not value
        or len(value) > limit
        or value != value.strip()
        or not value.isprintable()
    ):
        raise Rejected("invalid_identity")
    return value


def _code(value):
    if isinstance(value, Decimal) and value.as_tuple().exponent == 0:
        value = str(value)
    code = _text(value)
    if code.startswith("-"):
        raise Rejected("invalid_identifier")
    return code


def _city_code(value):
    code = _code(value)
    if re.fullmatch(r"0+", code):
        raise Rejected("invalid_city_code")
    return code


def _identity(shape, row, city_code, city_name):
    area = _text(row.get("Area"))
    if city_name and city_name != area:
        raise Rejected("city_mismatch")
    if shape == "S":
        station = _code(row.get("StationCode"))
        name = _text(row.get("PositionName"))
        # Accepted only with a verified station→city site epoch by the caller.
        declared = row.get("CityCode")
        if declared is not None:
            declared = _code(declared)
            if re.fullmatch(r"0+", declared):
                declared = None
            elif declared != city_code:
                raise Rejected("city_mismatch")
        return station, declared, area, name
    if "StationCode" in row or "PositionName" in row:
        raise Rejected("shape_mismatch")
    declared = _city_code(row.get("CityCode"))
    if shape in {"C", "T"} and declared != city_code:
        raise Rejected("city_mismatch")
    return declared, declared, area, None


def _source_time(profile, raw):
    if not isinstance(raw, str) or len(raw) > 48:
        raise Rejected("invalid_time")
    if match := DOTNET.fullmatch(raw):
        try:
            zone = ZoneInfo(profile.source_timezone)
            local = (EPOCH + timedelta(milliseconds=int(match[1]))).astimezone(zone)
            if match[2] and match[2] != local.strftime("%z"):
                raise Rejected("invalid_source_offset")
            return local.isoformat(), replace(profile, timezone_basis="fixture_epoch_milliseconds")
        except (OverflowError, ZoneInfoNotFoundError):
            raise Rejected("invalid_time") from None
    return raw, profile


def _time(profile, row):
    source_time = row.get("TimePoint")
    stamp, profile = _source_time(profile, source_time)
    observed = normalize_time(profile, stamp)
    display = row.get("TimePointStr")
    if isinstance(display, str) and (ISO_LABEL.match(display) or DOTNET.fullmatch(display)):
        second_stamp, second_profile = _source_time(profile, display)
        if normalize_time(second_profile, second_stamp).observed_at_utc != observed.observed_at_utc:
            raise Rejected("source_time_conflict")
    return stamp, replace(observed, source_time=source_time), profile


def _measurement(indicator, row, code, profile, stamp, observed, fetched_at):
    wire_key = indicator.replace(".", "_")
    if wire_key not in row:
        return Measurement(indicator, None, "missing_key", False)
    value = row[wire_key]
    try:
        raw = _value(value)
        unit = (
            "1"
            if indicator == "AQI"
            else "grade"
            if indicator.endswith("Level")
            else ("mg/m³" if indicator == "CO" else "µg/m³")
        )
        normalized = normalize(
            profile,
            {
                "object_code": code,
                "indicator": indicator,
                "source_time": stamp,
                "raw_value": raw,
                "raw_unit": unit,
                "source_description": (
                    _description(row.get("Quality"), 200) if indicator == "AQI" else ""
                ),
            },
            BatchContext(fetched_at),
        )
        normalized = replace(normalized, time=observed)
        if value is None:
            normalized = replace(normalized, raw_value="null", issue="missing_null")
        return Measurement(indicator, normalized, normalized.issue, True)
    except Rejected as exc:
        return Measurement(indicator, None, exc.code, True)


def _value(value):
    if value is None:
        return NULL_SENTINEL
    if isinstance(value, str) and value != NULL_SENTINEL:
        return value
    if isinstance(value, Decimal):
        # Refuse pathological exponents before a fixed-point string can allocate.
        if value.adjusted() > 11 or (value and value.adjusted() < -6):
            raise Rejected("invalid_precision")
        if value.is_zero():
            return "0"
        parts = value.as_tuple()
        end = len(parts.digits)
        while end > 1 and parts.digits[end - 1] == 0:
            end -= 1
        exponent = parts.exponent + len(parts.digits) - end
        if exponent < -6:
            raise Rejected("invalid_precision")
        return format(Decimal((parts.sign, parts.digits[:end], exponent)), "f")
    raise Rejected("invalid_value")


def _description(value, limit):
    if not isinstance(value, str) or len(value) > limit or not value.isprintable():
        return ""
    return value


def _grade(value):
    if isinstance(value, str) and len(value) == 1 and value in "123456":
        return int(value)
    if isinstance(value, Decimal) and 1 <= value <= 6 and value == value.to_integral_value():
        return int(value)
    return None


def parse(
    shape,
    body,
    fetched_at,
    *,
    start,
    end,
    city_code="",
    city_name="",
    source_timezone="Asia/Shanghai",
    window_definition="hourly_label_unspecified",
    allowed_codes=None,
):
    """Return only the requested source labels. Resolve identity and rights before storing."""
    contract = CONTRACTS[shape]
    payload = _decode(body, contract.max_bytes)
    if shape == "C":
        if not isinstance(payload, dict):
            raise WireError("invalid_shape", responded=True)
        payload = [payload]
    elif not isinstance(payload, list):
        raise WireError("invalid_shape", responded=True)
    if len(payload) > contract.max_rows:
        raise WireError("response_too_many_rows", responded=True)
    profile = ProductProfile(
        shape,
        source_timezone,
        "fixture_source_local_label",
        window_definition,
        rule_version="s03.f.v1",
        missing_markers=("", "NA", "/", NULL_SENTINEL),
        allow_naive_label=True,
    )
    rows = []
    issues = []
    allowed = frozenset(allowed_codes) if allowed_codes is not None else None
    trend_slots = set()
    for row in payload:
        try:
            if not isinstance(row, dict):
                raise Rejected("invalid_row")
            code, declared_city, area, station = _identity(shape, row, city_code, city_name)
            stamp, observed, row_profile = _time(profile, row)
        except Rejected as exc:
            if shape == "C":
                raise WireError(exc.code, responded=True) from None
            issues.append(exc.code)
            continue
        at = observed.observed_at_utc
        if shape == "T":
            trend_slots.add(at)
            if len(trend_slots) > 24:
                raise WireError("response_too_many_hours", responded=True)
        if not start <= at < end:
            issues.append("outside_target")
            continue
        if allowed is not None and code not in allowed:
            issues.append("outside_roster")
            continue
        fields = tuple(
            _measurement(indicator, row, code, row_profile, stamp, observed, fetched_at)
            for indicator in contract.indicators
        )
        rows.append(
            Row(
                code,
                declared_city,
                area,
                station,
                observed,
                fields,
                _description(row.get("Quality"), 200),
                _grade(row.get("AqiLevel")),
                _description(row.get("PrimaryPollutant"), 100),
            )
        )
    return Parsed(tuple(rows), tuple(issues))
