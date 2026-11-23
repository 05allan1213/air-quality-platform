"""The reviewed CNEMC hourly request contracts and local field projections."""

import re
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from types import MappingProxyType
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from django.conf import settings
from observations.normalization import (
    INDICATORS,
    ProductProfile,
    Rejected,
)
from observations.normalization import (
    standard as check_standard,
)

from . import transport, wire
from .contract import Contract
from .transport import WireError

VERSION = "cnemc.hour.v1"
ORIGIN = "https://air.cnemc.cn:18007"
TIMEZONE = "Asia/Shanghai"
MAX_CODES = 2048
PRODUCTS = MappingProxyType(
    {
        "city-list-hour": "A01",
        "city-live-hour": "A03",
        "station-city-hour": "A04",
        "city-trend-hour": "A05",
        "station-national-hour": "A07",
    }
)


@dataclass(frozen=True)
class Endpoint(Contract):
    shape: str
    nationwide: bool
    exact: bool = False


def _endpoint(
    shape, method, path, parameter, max_bytes, max_rows, *, nationwide=False, exact=False
):
    return Endpoint(
        method, path, parameter, max_bytes, max_rows, INDICATORS[shape], shape, nationwide, exact
    )


ENDPOINTS = MappingProxyType(
    {
        "A01": _endpoint(
            "L", "GET", "/CityData/GetAllCityRealTimeAQIModels", None, 2**20, 512, nationwide=True
        ),
        "A03": _endpoint("C", "POST", "/CityData/GetAQIDataPublishLiveInfo", "cityCode", 2**16, 1),
        "A04": _endpoint("S", "GET", "/CityData/GetAQIDataPublishLive", "cityName", 2**19, 256),
        "A05": _endpoint(
            "T",
            "POST",
            "/HourChangesPublish/GetCityRealTimeAqiHistoryByCondition",
            "citycode",
            2**17,
            48,
        ),
        "A07": _endpoint(
            "S",
            "POST",
            "/HourChangesPublish/GetAllAQIPublishLive",
            None,
            2**21,
            2048,
            nationwide=True,
        ),
        "A10": _endpoint(
            "S",
            "POST",
            "/HourChangesPublish/GetAQIHistoryByConditionHis",
            "date",
            2**21,
            2048,
            nationwide=True,
            exact=True,
        ),
    }
)


def fetch(endpoint, city_code, city_name, target, *, synthetic):
    contract = ENDPOINTS.get(endpoint)
    if contract is None:
        raise WireError("unregistered_profile")
    if synthetic is True and settings.CNEMC_FIXTURE_HTTP:
        origin = transport.ORIGIN
    elif synthetic is False and settings.CNEMC_MODE in {"limited", "periodic"}:
        origin = ORIGIN
    else:
        raise WireError("unregistered_profile")
    params = {}
    content = b""
    headers = {"Accept": "application/json", "Accept-Encoding": "identity"}
    try:
        if contract.exact:
            if not isinstance(target, datetime) or target.utcoffset() is None:
                raise Rejected("invalid_target")
            local = target.astimezone(ZoneInfo(TIMEZONE))
            if local.minute or local.second or local.microsecond or not 2 <= local.year <= 9998:
                raise Rejected("invalid_target")
            label = local.replace(tzinfo=None).isoformat(sep=" ", timespec="seconds")
            content = urlencode({"date": label}).encode("ascii")
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        elif contract.parameter == "cityName":
            params["cityName"] = wire._text(city_name)
        elif contract.parameter:
            code = wire._city_code(city_code)
            # Official city codes are six digits. The test origin also accepts synthetic codes.
            if not synthetic and re.fullmatch(r"[0-9]{6}", code) is None:
                raise Rejected("invalid_target")
            params[contract.parameter] = code
    except (ValueError, OverflowError):
        raise WireError("invalid_target") from None
    return transport.request(contract, origin, params, content, headers)


def observation_profile(shape, source_timezone, standard):
    """Validate the hourly profile and return its optional source-local standard period."""
    if source_timezone != TIMEZONE:
        raise WireError("unregistered_profile")
    standard = {} if standard is None else standard
    series = basis = "unknown"
    evidence = ""
    period = None
    if standard != {}:
        if not isinstance(standard, dict) or set(standard) != {
            "series",
            "basis",
            "evidence",
            "from",
            "until",
        }:
            raise WireError("invalid_standard_evidence")
        series, basis, evidence = standard["series"], standard["basis"], standard["evidence"]
        try:
            period = date.fromisoformat(standard["from"]), date.fromisoformat(standard["until"])
        except (TypeError, ValueError):
            raise WireError("invalid_standard_evidence") from None
        if (
            not isinstance(series, str)
            or series == "unknown"
            or not isinstance(basis, str)
            or not isinstance(evidence, str)
            or not 0 < len(evidence.strip()) <= 240
            or not evidence.isprintable()
            or period[0] >= period[1]
        ):
            raise WireError("invalid_standard_evidence")

    profile = ProductProfile(
        shape,
        source_timezone,
        "cnemc_source_local_label",
        "hourly_label_unspecified",
        rule_version=VERSION,
        standard_series=series,
        standard_basis=basis,
        standard_evidence=evidence,
        missing_markers=("", "NA", "/", wire.NULL_SENTINEL),
        allow_naive_label=True,
    )
    try:
        check_standard(profile, "AQI")
    except Rejected as exc:
        raise WireError(exc.code) from None
    return profile, period


def _time(profile, raw):
    stamp, observed, _ = wire._time(profile, raw)
    # Some arrays provide a full local label in addition to epoch milliseconds.
    display = raw.get("TimePointStr")
    if isinstance(display, str) and (
        match := re.fullmatch(r"([0-9]{4})年([0-9]{2})月([0-9]{2})日 ([0-9]{2})时", display)
    ):
        year, month, day, hour = match.groups()
        label = f"{year}-{month}-{day}T{hour}:00:00"
        if wire.normalize_time(profile, label).observed_at_utc != observed.observed_at_utc:
            raise Rejected("source_time_conflict")
    return stamp, observed


def _included(selection, contract):
    if selection == {"mode": "all"}:
        return None
    if (
        not isinstance(selection, dict)
        or set(selection) != {"mode", "objects"}
        or selection["mode"] != "include"
        or not isinstance(selection["objects"], dict)
        or len(selection["objects"]) > MAX_CODES
    ):
        raise WireError("invalid_selection")
    for indicators in selection["objects"].values():
        if not isinstance(indicators, list) or any(
            i not in contract.indicators for i in indicators
        ):
            raise WireError("invalid_selection")
    return selection["objects"]


def parse(
    endpoint,
    body,
    fetched_at,
    *,
    start,
    end,
    city_code="",
    city_name="",
    source_timezone=TIMEZONE,
    selection=None,
    standard=None,
):
    """Decode a bounded packet, then normalize only the admitted field projection."""
    contract = ENDPOINTS.get(endpoint)
    if contract is None:
        raise WireError("unregistered_profile")
    profile, standard_period = observation_profile(contract.shape, source_timezone, standard)
    included = _included(
        {"mode": "include", "objects": {}} if selection is None else selection, contract
    )
    payload = wire._decode(body, contract.max_bytes)
    if contract.shape == "C":
        if not isinstance(payload, dict):
            raise WireError("invalid_shape", responded=True)
        payload = [payload]
    elif not isinstance(payload, list):
        raise WireError("invalid_shape", responded=True)
    if len(payload) > contract.max_rows:
        raise WireError("response_too_many_rows", responded=True)
    if contract.exact and end != start + timedelta(hours=1):
        raise WireError("invalid_target", responded=True)

    selected = []
    issues = set()
    trend_slots = set()
    for raw in payload:
        if not isinstance(raw, dict):
            raise WireError("invalid_row", responded=True)
        # Check every exact-response timestamp, including rows outside our local process right.
        # Finish validation before handing any observation to the writer.
        if contract.exact:
            try:
                stamp, observed = _time(profile, raw)
            except Rejected as exc:
                raise WireError(exc.code, responded=True) from None
            if observed.observed_at_utc != start:
                raise WireError("exact_target_mismatch", responded=True)
        wire_code = raw.get("StationCode" if contract.shape == "S" else "CityCode")
        try:
            code = wire._code(wire_code)
        except Rejected as exc:
            if contract.shape == "C" or contract.exact:
                raise WireError(exc.code, responded=True) from None
            if included is None:
                issues.add(exc.code)
            continue
        indicators = contract.indicators if included is None else included.get(code, ())
        if not indicators and contract.shape not in {"C", "T"}:
            continue
        try:
            identity = wire._identity(
                contract.shape,
                raw,
                city_code,
                city_name,
                require_station_city=endpoint == "A07",
                nationwide=contract.nationwide,
            )
            if not contract.exact:
                stamp, observed = _time(profile, raw)
        except Rejected as exc:
            if contract.shape == "C" or contract.exact:
                raise WireError(exc.code, responded=True) from None
            issues.add(exc.code)
            continue
        at = observed.observed_at_utc
        if contract.shape == "T":
            trend_slots.add(at)
            if len(trend_slots) > 24:
                raise WireError("response_too_many_hours", responded=True)
        if not start <= at < end:
            issues.add("outside_target")
            continue
        if indicators:
            selected.append((identity, stamp, observed, raw, indicators))

    rows = []
    unverified = replace(
        profile, standard_series="unknown", standard_basis="unknown", standard_evidence=""
    )
    for identity, stamp, observed, raw, indicators in selected:
        row_profile = profile
        if (
            standard_period
            and not standard_period[0] <= observed.source_local_date < standard_period[1]
        ):
            row_profile = unverified
        description = grade = declared_grade = pollutant = None
        if "AQI" in indicators:
            description = wire._description(raw.get("Quality"), 200)
            supplied_grade = raw.get("AqiLevel")
            grade = wire._grade(supplied_grade)
            # An explicit invalid grade can invalidate an otherwise comparable AQI.
            # Only an actual grade may be retained as optional metadata.
            declared_grade = grade if supplied_grade in (None, "") or grade else "invalid_grade"
            pollutant = wire._description(raw.get("PrimaryPollutant"), 100)
        fields = []
        for indicator in contract.indicators:
            if indicator not in indicators:
                continue
            key = indicator.replace(".", "_")
            reading = {key: raw[key]} if key in raw else {}
            if indicator == "AQI" and description:
                reading["Quality"] = description
            fields.append(
                wire._measurement(
                    indicator,
                    reading,
                    identity[0],
                    row_profile,
                    stamp,
                    observed,
                    fetched_at,
                    source_grade=declared_grade if indicator == "AQI" else None,
                )
            )
        rows.append(
            wire.Row(*identity, observed, tuple(fields), description or "", grade, pollutant or "")
        )
    rows.sort(key=lambda row: (row.time.observed_at_utc, row.object_code))
    return wire.Parsed(tuple(rows), tuple(sorted(issues)))
