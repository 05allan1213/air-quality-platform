"""Rules for one self-authored F product. No city or station identity is inferred."""

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo

KEY = "co-hour-v1"
SOURCE = "local.fixture.v1"
ENTITY = "F:CITY:001"
ZONE = "Asia/Shanghai"
WINDOW = "previous_hour_right_label_[start,end)"
FIXTURE = Path(__file__).with_name("data") / "co-hour-v1.json"
TIMESTAMP = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])"
)


class Rejected(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def load_fixture() -> dict[str, object]:
    data: dict[str, object] = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return data


def parse_time(text: object) -> datetime:
    if not isinstance(text, str) or not TIMESTAMP.fullmatch(text):
        raise Rejected("invalid_time")
    try:
        return datetime.fromisoformat(text).astimezone(UTC)
    except (ValueError, OverflowError) as exc:
        raise Rejected("invalid_time") from exc


@dataclass(frozen=True)
class Normalized:
    fixture_key: str
    fixture_version: str
    product: str
    source: str
    entity: str
    synthetic: bool
    purpose: str
    indicator: str
    raw_value: str
    raw_unit: str
    source_time: str
    source_timezone: str
    observed_at: datetime
    window_start: datetime
    window_end: datetime
    window_semantics: str
    value: Decimal
    unit: str = "mg/m³"
    quality: str = "valid"
    aqi_standard: str = "not_applicable"


def normalize(data: Mapping[str, object]) -> Normalized:
    for field, expected in {
        "fixture_key": KEY,
        "fixture_version": "1",
        "product": "F",
        "source": SOURCE,
        "entity": ENTITY,
        "purpose": "local_demo",
        "indicator": "CO",
        "raw_unit": "µg/m³",
        "source_timezone": ZONE,
        "window_semantics": WINDOW,
    }.items():
        if data.get(field) != expected:
            raise Rejected(f"invalid_{field}")
    if data.get("synthetic") is not True:
        raise Rejected("invalid_synthetic")
    text = data.get("source_time")
    if not isinstance(text, str):
        raise Rejected("invalid_time")
    end = parse_time(text)
    try:
        local = end.astimezone(ZoneInfo(ZONE))
        start = end - timedelta(hours=1)
    except OverflowError as exc:
        raise Rejected("invalid_time") from exc
    if local.utcoffset() != datetime.fromisoformat(text).utcoffset():
        raise Rejected("invalid_source_offset")
    if local.minute or local.second or local.microsecond:
        raise Rejected("invalid_window")
    raw = data.get("raw_value")
    if raw is None or raw == "":
        raise Rejected("missing_value")
    if not isinstance(raw, str) or len(raw) > 32:
        raise Rejected("invalid_value")
    if raw.startswith("<"):
        raise Rejected("censored_value")
    try:
        value = Decimal(raw)
    except InvalidOperation as exc:
        raise Rejected("invalid_value") from exc
    if not value.is_finite() or value < 0:
        raise Rejected("invalid_value")
    # The fixed F format reports integral micrograms; preserve every accepted digit.
    if value >= 10**12 or value != value.to_integral_value():
        raise Rejected("invalid_value")
    return Normalized(
        KEY,
        "1",
        "F",
        SOURCE,
        ENTITY,
        True,
        "local_demo",
        "CO",
        raw,
        "µg/m³",
        text,
        ZONE,
        end,
        start,
        end,
        WINDOW,
        (value / 1000).quantize(Decimal("0.001")),
    )
