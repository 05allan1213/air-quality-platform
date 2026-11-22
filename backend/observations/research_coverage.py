"""Versioned calendars for research coverage, independent of observation arrival."""

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from . import import_uci as uci
from .normalization import CONCENTRATIONS, WINDOWS
from .policy import Denied
from .selection import TICK
from .uci_catalog import DATASET

FIXTURE_CALENDAR = "s05-fixture-v1"
FIXTURE_EVIDENCE = "S05 authored F calendar and reporting identity v1"
FIXTURE_YEAR = 2024


def merge_windows(windows):
    result = []
    for low, high in sorted(windows):
        if result and low <= result[-1][1]:
            result[-1][1] = max(high, result[-1][1])
        else:
            result.append([low, high])
    return result


def uci_product(product):
    return (
        product.family == product.shape == "H"
        and product.level == "station"
        and not product.synthetic
        and product.source.namespace == uci.SOURCE
        and product.code == uci.PRODUCT
        and product.evidence == DATASET
        and product.source_timezone == uci.NORMALIZATION.source_timezone
    )


def fixture_product(product):
    return (
        product.family == "F"
        and product.synthetic
        and product.contract_version == FIXTURE_CALENDAR
        and product.evidence == FIXTURE_EVIDENCE
    )


def site_comparable(product, spec, identities, epochs):
    if product.level == "city":
        return
    sites = [epochs[pk] for pk in spec.ids if pk in epochs]
    if len(sites) != len(spec.ids):
        raise Denied()
    if len({site.station_id for site in sites}) != len(sites):
        raise Denied("incompatible_series")
    if spec.method != "compare":
        return
    if len({site.city_id for site in sites}) != 1:
        raise Denied("incompatible_series")
    if product.shape == "S":
        confirmed = all(
            site.location_evidence and site.certainty in {"confirmed", "documented"}
            for site in sites
        )
    elif uci_product(product):
        codes = {code for entity, code, *_ in identities if entity in spec.ids}
        confirmed = codes <= set(uci.SELECTED_STATIONS) and all(
            site.certainty == "dataset_reporting_identity_only"
            and site.location_evidence == DATASET
            for site in sites
        )
    else:
        confirmed = fixture_product(product) and all(
            site.location_evidence == FIXTURE_EVIDENCE
            and site.certainty in {"confirmed", "dataset_reporting_identity_only"}
            for site in sites
        )
    if not confirmed:
        raise Denied("incompatible_series")


def evidence_calendar(product, spec, code, indicator):
    zone = ZoneInfo(product.source_timezone)
    if (
        uci_product(product)
        and spec.window == uci.NORMALIZATION.window_definition
        and code in uci.SELECTED_STATIONS
        and indicator in CONCENTRATIONS
    ):
        return uci.PROFILE, [
            (start.replace(tzinfo=zone).astimezone(UTC), end.replace(tzinfo=zone).astimezone(UTC))
            for start, end in uci.SELECTED_WINDOWS
        ]
    if fixture_product(product) and spec.window == "hourly_label_unspecified":
        # The F contract covers a full source-local year, including DST transitions where applicable.
        # V05 includes fixed shorter calendars, a confirmed empty calendar, and unknown objects.
        year_start = datetime(FIXTURE_YEAR, 1, 1, tzinfo=zone).astimezone(UTC)
        if code == "unknown":
            return None, []
        if code == "empty":
            spans = []
        elif code == "three-days":
            spans = [(year_start, datetime(2024, 1, 4, tzinfo=zone).astimezone(UTC))]
        elif code == "first-day":
            spans = [(year_start, datetime(2024, 1, 2, tzinfo=zone).astimezone(UTC))]
        elif code == "second-day":
            spans = [
                (
                    datetime(2024, 1, 2, tzinfo=zone).astimezone(UTC),
                    datetime(2024, 1, 3, tzinfo=zone).astimezone(UTC),
                )
            ]
        else:
            spans = [
                (
                    datetime(FIXTURE_YEAR - 1, 12, 1, tzinfo=zone).astimezone(UTC),
                    datetime(FIXTURE_YEAR + 1, 1, 1, tzinfo=zone).astimezone(UTC),
                )
            ]
        return FIXTURE_CALENDAR, spans
    return None, []


def coverage(product, spec, identities, epochs):
    """Return one profile per requested column, after the caller proves scope and catalog use."""
    if spec.method == "detail":
        # A generic detail/export operation does not require the derived coverage field.
        # The selected observations remain eligible; no research calendar is consulted.
        return [
            {
                "known": False,
                "has_calendar": False,
                "full": False,
                "windows": [],
                "unknown_windows": [[spec.start, spec.end]],
                "unknown_by_code": [(None, spec.start, spec.end)],
            }
            for _ in range(len(spec.ids) * len(spec.indicators))
        ], {"label_position": WINDOWS[spec.window][1]}
    site_comparable(product, spec, identities, epochs)
    by_entity = defaultdict(list)
    for entity, code, _, low, high in identities:
        by_entity[entity].append((code, low, high))
    hours, position = WINDOWS[spec.window]
    width = timedelta(hours=hours)
    profiles = []
    versions = set()
    for entity in spec.ids:
        for indicator in spec.indicators:
            expected = []
            unknown = []
            unknown_by_code = []
            known = False
            for code, low, high in by_entity[entity]:
                version, spans = evidence_calendar(product, spec, code, indicator)
                known |= version is not None
                if version is not None:
                    versions.add(version)
                if position == "right":
                    low += width
                    if high is not None:
                        high += TICK
                elif position == "left" and high is not None:
                    high += TICK - width
                lo, hi = max(low, spec.start), min(high or spec.end, spec.end)
                if lo >= hi:
                    continue
                if version is None:
                    unknown.append((lo, hi))
                    unknown_by_code.append((code, lo, hi))
                for start, end in spans:
                    clipped_start, clipped_end = max(lo, start), min(hi, end)
                    if clipped_start < clipped_end:
                        expected.append((clipped_start, clipped_end))
            windows = merge_windows(expected)
            profiles.append(
                {
                    "known": known and not unknown,
                    "has_calendar": known,
                    "full": windows == [[spec.start, spec.end]] and not unknown,
                    "windows": windows,
                    "unknown_windows": merge_windows(unknown),
                    "unknown_by_code": sorted(unknown_by_code),
                }
            )
    return profiles, {
        "calendar_versions": sorted(versions),
        "label_position": position,
        "timezone_basis": (
            uci.NORMALIZATION.timezone_basis if uci_product(product) else "product_registration"
        ),
    }
