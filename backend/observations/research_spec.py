"""The bounded parent intent shared by research, support rows and both CSV formats."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from math import ceil

from .detail import unit_for
from .normalization import REPORT_KIND, WINDOWS
from .policy import Denied
from .s04_http import instant

METHOD_VERSION = "s05.v1"
SELECTION_KEYS = {
    "product",
    "source",
    "scope",
    "cities",
    "site_epochs",
    "indicators",
    "units",
    "standard_series",
    "report_kind",
    "window_definition",
    "quality",
    "start",
    "end",
    "anchor",
    "span_hours",
}


def object_with_keys(raw, allowed, required=()):
    if not isinstance(raw, dict) or set(raw) - set(allowed) or set(required) - set(raw):
        raise Denied("invalid_request")
    return raw


def text(value):
    if not isinstance(value, str) or not value or len(value) > 100 or not value.isprintable():
        raise Denied("invalid_request")
    return value


def positive_int(value):
    if type(value) is not int or not 0 < value < 2**63:
        raise Denied("invalid_request")
    return value


def ordered(values, maximum, convert):
    if not isinstance(values, list) or not values:
        raise Denied("invalid_request")
    if len(values) > maximum:
        raise Denied("query_too_large")
    result = tuple(convert(v) for v in values)
    if len(set(result)) != len(result):
        raise Denied("invalid_request")
    return result


@dataclass(frozen=True)
class QuerySpec:
    product_id: int
    source: str
    synthetic: bool
    entity_key: str
    ids: tuple
    indicators: tuple
    units: dict
    standards: dict
    start: datetime
    end: datetime
    window: str
    report: str
    method: str
    method_version: str
    grain: str

    @property
    def cells(self):
        return (
            ceil((self.end - self.start) / timedelta(hours=1))
            * len(self.ids)
            * len(self.indicators)
        )

    @property
    def canonical(self):
        return {
            "selection": {
                "product": self.product_id,
                "source": self.source,
                "scope": "synthetic" if self.synthetic else "formal",
                self.entity_key: list(self.ids),
                "indicators": list(self.indicators),
                "units": self.units,
                "standard_series": self.standards,
                "report_kind": self.report,
                "window_definition": self.window,
                "quality": "reliable",
                "start": self.start.isoformat(),
                "end": self.end.isoformat(),
            },
            "method": self.method,
            "method_version": self.method_version,
            "grain": self.grain,
        }


def parse_spec(payload, *, export=False):
    object_with_keys(
        payload, {"selection", "method", "method_version", "grain"}, {"selection", "method"}
    )
    selection = object_with_keys(
        payload["selection"], SELECTION_KEYS, {"product", "source", "indicators"}
    )
    entity = {"cities", "site_epochs"} & selection.keys()
    if len(entity) != 1:
        raise Denied("invalid_request")
    entity_key = entity.pop()
    ids = ordered(selection[entity_key], 5, positive_int)
    inds = ordered(selection["indicators"], 7, text)
    scope = text(selection.get("scope", "formal"))
    window = text(selection.get("window_definition", "hourly_label_unspecified"))
    report = text(selection.get("report_kind", REPORT_KIND.get(window, "hourly")))
    method = text(payload["method"])
    version = text(payload.get("method_version", METHOD_VERSION))
    grain = text(payload.get("grain", "day"))
    if (
        scope not in {"synthetic", "formal"}
        or selection.get("quality", "reliable") != "reliable"
        or method not in {"compare", "statistics", "pearson", "detail"}
        or grain not in {"hour", "day", "month", "season"}
        or window not in WINDOWS
        or method == "compare"
        and (len(ids) < 2 or len(inds) != 1)
        or method == "pearson"
        and (len(ids) != 1 or len(inds) != 2)
        or method == "statistics"
        and (len(ids) != 1 or len(inds) != 1)
    ):
        raise Denied("invalid_request")
    bounds = {"start", "end", "anchor", "span_hours"} & selection.keys()
    if bounds == {"anchor", "span_hours"}:
        end = instant(selection["anchor"])
        hours = positive_int(selection["span_hours"])
        if hours > 8784:
            raise Denied("query_too_large")
        start = end - timedelta(hours=hours)
    elif bounds == {"start", "end"}:
        start, end = instant(selection["start"]), instant(selection["end"])
    else:
        raise Denied("invalid_request")
    if end <= start:
        raise Denied("invalid_request")
    units = selection.get("units", {i: unit_for(i) for i in inds})
    standards = selection.get(
        "standard_series",
        {i: "unknown" if unit_for(i) in {"1", "grade"} else "not_applicable" for i in inds},
    )
    for mapping in (units, standards):
        object_with_keys(mapping, inds, inds)
        for value in mapping.values():
            text(value)
    spec = QuerySpec(
        positive_int(selection["product"]),
        text(selection["source"]),
        scope == "synthetic",
        entity_key,
        ids,
        inds,
        units,
        standards,
        start,
        end,
        window,
        report,
        method,
        version,
        grain,
    )
    annual = len(ids) == len(inds) == 1 and grain in {"month", "season"}
    if end - start > timedelta(days=366 if annual else 30) or spec.cells > (
        12000 if export else 8784 if annual else 3600
    ):
        raise Denied("query_too_large")
    return spec
