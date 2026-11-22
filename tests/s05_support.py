"""Project-authored S05 data and grants, for disposable F verification databases only."""

from datetime import UTC, datetime, timedelta
from itertools import batched
from zoneinfo import ZoneInfo

from observations.models import (
    City,
    CityIdentifier,
    Product,
    SiteEpoch,
    Source,
    Station,
    StationIdentifier,
)
from observations.normalization import LEVELS, BatchContext, ProductProfile, normalize
from observations.policy import LOCAL_AUTHENTICATED, OPERATIONS, REQUIRED, register_grant
from observations.revisions import Candidate, fixture_ticket, ingest_batch
from psycopg.types.range import TimestamptzRange

START = datetime(2024, 1, 1, tzinfo=UTC)
END = datetime(2025, 1, 1, tzinfo=UTC)
HOUR = timedelta(hours=1)
PROFILE = "s05-fixture-v1"
NAMESPACE = "test.s05"
EVIDENCE = "S05 authored F calendar and reporting identity v1"
ATTRIBUTION = "S05 项目自制 F，供本地隔离验证"
ANALYZE = REQUIRED | {"catalog", "research_coverage", "research_summary"}
RETAIN = REQUIRED | {"catalog", "research_coverage", "export_manifest", "statistics_baseline"}
DISPLAY = ANALYZE


def product(code="fixture", shape="S", *, timezone="UTC", known=True, source=None):
    if source is None:
        source, _ = Source.objects.get_or_create(
            namespace=f"{NAMESPACE}.{code}", defaults={"publisher": ATTRIBUTION}
        )
    return Product.objects.create(
        source=source,
        code=code,
        family="F",
        shape=shape,
        level="station" if shape in {"S", "H"} else "city",
        synthetic=True,
        contract_version=PROFILE if known else "s02.v1",
        source_timezone=timezone,
        evidence=EVIDENCE if known else "",
        attribution=ATTRIBUTION,
    )


def city(p, code="001", *, geography="F-city", start=START, end=END, name="F city"):
    result = City.objects.create(source=p.source, name=name, geography=geography)
    CityIdentifier.objects.create(
        source=p.source,
        city=result,
        code=code,
        active_period=TimestamptzRange(start, end),
        evidence=EVIDENCE,
    )
    return result


def site(p, code="001", *, parent=None, start=START, end=END, name="F station"):
    if parent is None:
        parent = city(p, code, start=start, end=end)
    station = Station.objects.create(source=p.source, name=name)
    period = TimestamptzRange(start, end)
    StationIdentifier.objects.create(
        source=p.source,
        station=station,
        code=code,
        active_period=period,
        evidence=EVIDENCE,
    )
    return SiteEpoch.objects.create(
        station=station,
        city=parent,
        active_period=period,
        name=name,
        location_evidence=EVIDENCE,
        certainty="dataset_reporting_identity_only" if p.shape == "H" else "confirmed",
    )


def grant(operator, p, operation, *, fields=None, start=START, end=END, **terms):
    zone = ZoneInfo(p.source_timezone)
    defaults = {
        "operation": operation,
        "audience": LOCAL_AUTHENTICATED
        if operation in ("analyze", "export", "export_aggregate")
        else OPERATIONS[operation],
        "geography": "*",
        "data_from": start.astimezone(zone).date(),
        # Include the source day containing the last instant in the authorized interval.
        "data_until": (end - timedelta(microseconds=1)).astimezone(zone).date() + timedelta(days=1),
        "fields": sorted(fields if fields is not None else REQUIRED),
        "terms": "Project-authored F, local authenticated verification by named operation",
        "terms_version": "s05-fixture-rights-v1",
        "attribution": ATTRIBUTION,
    }
    return register_grant(operator.pk, p.pk, **(defaults | terms))


def authorize(operator, p, *, detail_fields=(), aggregate_fields=(), **scope):
    grants = {
        op: grant(operator, p, op, fields=fields, **scope)
        for op, fields in {
            "process_local": REQUIRED,
            "retain_normalized": RETAIN,
            "analyze": ANALYZE,
            "display": DISPLAY,
        }.items()
    }
    for op, fields in (("export", detail_fields), ("export_aggregate", aggregate_fields)):
        if fields:
            grants[op] = grant(operator, p, op, fields=fields, **scope)
    return grants


def candidate(
    p,
    code="001",
    at=START,
    value="10",
    *,
    indicator="CO",
    unit=None,
    token=None,
    rule=1,
    window="hourly_label_unspecified",
    fetched_at=None,
    **profile_fields,
):
    profile = ProductProfile(p.shape, p.source_timezone, EVIDENCE, window, **profile_fields)
    if unit is None:
        if indicator in LEVELS:
            unit = "grade"
        else:
            unit = {"CO": "mg/m³", "AQI": "1"}.get(indicator, "µg/m³")
    site_time = at.astimezone(ZoneInfo(p.source_timezone))
    fields = {
        "object_code": code,
        "source_time": (site_time.date() if window == "local_daily" else site_time).isoformat(),
        "indicator": indicator,
        "raw_unit": unit,
        "raw_value": str(value),
        "row_locator": f"F:{code}:{at.isoformat()}",
    }
    return Candidate(
        normalize(profile, fields, BatchContext(fetched_at or datetime.now(UTC))),
        token=token,
        scheme="sequence" if token is not None else "",
        rule=rule,
    )


def ingest(operator, p, candidates):
    for rows in batched(candidates, 1152, strict=False):
        ingest_batch(operator.pk, p.pk, fixture_ticket(p.pk), p.contract_version, rows)
