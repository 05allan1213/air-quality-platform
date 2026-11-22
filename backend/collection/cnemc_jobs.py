"""Versioned hourly job contracts. All object lists come from an offline registration."""

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from observations.models import CityIdentifier, SiteEpoch, StationIdentifier
from observations.policy import Denied, Scope, applicable

from .adapters import cnemc
from .models import Job, Run, RunItem

HOUR = timedelta(hours=1)
PROFILE = "cnemc-hour-v1"
CATALOG = {"catalog"}


def managed(job):
    return job.product.contract_version == cnemc.VERSION


def endpoint_for(job, reason):
    if not managed(job):
        return ""
    default = cnemc.PRODUCTS.get(job.product.code)
    if not default:
        raise Denied("unregistered_profile")
    if reason == "backfill" and default == "A07":
        if not job.backfill_policy:
            raise Denied("backfill_unavailable")
        return "A10"
    return default


def registry(job, endpoint, *, registration=False):
    p = job.product
    default = endpoint_for(job, "manual")
    contract = cnemc.ENDPOINTS[default]
    if job.disposed:
        raise Denied("job_disposed")
    if not (
        job.profile == PROFILE
        and p.source_timezone == cnemc.TIMEZONE
        and p.shape == contract.shape
        and (
            contract.nationwide
            and job.city_id is None
            or not contract.nationwide
            and job.city_id
            and job.city.source_id == p.source_id
        )
        and (endpoint == default or default == "A07" and endpoint == "A10" and job.backfill_policy)
    ):
        raise Denied("unregistered_profile")
    if p.synthetic and p.source.namespace == "cnemc.fixture" and p.family == "F":
        if registration or settings.CNEMC_FIXTURE_HTTP:
            return
    elif not p.synthetic and p.source.namespace == "cnemc" and p.family == p.shape:
        if registration and settings.CNEMC_MODE == "off":
            return
        if settings.CNEMC_MODE == "periodic" or (
            settings.CNEMC_MODE == "limited" and job.validation_grant_id and not job.next_due_at
        ):
            return
    raise Denied("unregistered_profile")


def objects(value, shape):
    indicators = cnemc.ENDPOINTS[cnemc.PRODUCTS[shape]].indicators
    if not isinstance(value, dict) or not 0 < len(value) <= cnemc.MAX_CODES:
        raise Denied("invalid_selection")
    result = {}
    for code, fields in value.items():
        if (
            not isinstance(code, str)
            or not 0 < len(code) <= 100
            or code != code.strip()
            or not code.isprintable()
            or not isinstance(fields, list)
            or not 1 <= len(fields) <= len(indicators)
            or not all(isinstance(i, str) and i in indicators for i in fields)
        ):
            raise Denied("invalid_selection")
        result[code] = sorted(set(fields))
    return result


def configure(job):
    sel = job.selection
    if isinstance(sel, dict) and set(sel) == {"mode", "objects"} and sel["mode"] == "include":
        job.selection = {"mode": "include", "objects": objects(sel["objects"], job.product.code)}
    elif sel != {"mode": "all"}:
        raise Denied("invalid_selection")
    if job.expected_codes is not None:
        if not job.roster_basis.strip():
            raise Denied("invalid_roster")
        job.expected_codes = objects(job.expected_codes, job.product.code)
        selected = job.selection.get("objects")
        if selected and any(
            not set(fields) <= set(selected.get(code, ()))
            for code, fields in job.expected_codes.items()
        ):
            raise Denied("invalid_roster")
    elif job.roster_basis:
        raise Denied("invalid_roster")
    if job.standard != {}:
        v = job.standard
        try:
            valid = (
                isinstance(v, dict)
                and set(v) == {"series", "basis", "evidence", "from", "until"}
                and v["series"] in ("HJ633-2012", "HJ633-2026")
                and v["basis"] in ("source_stated", "document_inferred")
                and isinstance(v["evidence"], str)
                and v["evidence"].isprintable()
                and 0 < len(v["evidence"].strip()) <= 240
                and date.fromisoformat(v["from"]) < date.fromisoformat(v["until"])
            )
        except (TypeError, ValueError):
            valid = False
        if not valid:
            raise Denied("invalid_standard_evidence")
    if job.backfill_policy != {}:
        from .admission import parse_hour

        v = job.backfill_policy
        bounds = {"lookback_hours": 23, "max_windows_per_scan": 24, "max_requests_per_scan": 24}
        if (
            job.product.code != "station-national-hour"
            or not isinstance(v, dict)
            or set(v) != {*bounds, "operating_from", "equivalence_basis"}
            or not isinstance(v["equivalence_basis"], str)
            or not v["equivalence_basis"].strip()
            or not v["equivalence_basis"].isprintable()
            or len(v["equivalence_basis"]) > 240
            or not all(type(v[k]) is int and 1 <= v[k] <= limit for k, limit in bounds.items())
        ):
            raise Denied("invalid_backfill_policy")
        v["operating_from"] = parse_hour(v["operating_from"]).isoformat()
    if job.next_due_at and (job.validation_grant_id or job.cadence_seconds % 3600):
        raise Denied("invalid_schedule")


def due_time(value):
    try:
        due = datetime.fromisoformat(value)
        if not isinstance(value, str) or len(value) > 40 or due.utcoffset() is None:
            raise ValueError
        return due.astimezone(UTC)
    except (TypeError, ValueError, OverflowError):
        raise Denied("invalid_target") from None


def resolved(job, codes, at):
    """Resolve a registered list at one hour in a bounded number of catalogue queries."""
    p = job.product
    restrictions = dict(
        source_id=p.source_id, kind="code", code__in=codes, active_period__contains=at
    )
    if p.level == "city":
        data = {
            code: (city, geo, city)
            for code, city, geo in CityIdentifier.objects.filter(
                city__source_id=p.source_id, **restrictions
            ).values_list("code", "city_id", "city__geography")
        }
    else:
        identifiers = dict(
            StationIdentifier.objects.filter(
                station__source_id=p.source_id, **restrictions
            ).values_list("code", "station_id")
        )
        epochs = {
            station: (epoch, geo, city)
            for station, epoch, geo, city in SiteEpoch.objects.filter(
                station_id__in=identifiers.values(),
                city__source_id=p.source_id,
                active_period__contains=at,
            ).values_list("station_id", "pk", "city__geography", "city_id")
        }
        data = {code: epochs[sid] for code, sid in identifiers.items() if sid in epochs}
    if (
        set(codes) != data.keys()
        or job.city_id
        and any(city != job.city_id for _, _, city in data.values())
    ):
        raise Denied("identity_unresolved")
    return data


def expected(job, start, end):
    from .admission import cell_key

    if job.expected_codes is None:
        return None
    cells = {}
    while start < end:
        data = resolved(job, job.expected_codes, start)
        for code, fields in job.expected_codes.items():
            for indicator in fields:
                cells[cell_key(data[code][0], start, indicator)] = [
                    code,
                    start.isoformat(),
                    indicator,
                ]
        start += HOUR
    return cells


def scopes(job, start, end):
    from .admission import data_scope

    if job.selection.get("mode") == "all":
        return [data_scope(job, start, end)]
    codes = job.selection.get("objects")
    if not codes:
        raise Denied("invalid_selection")
    found = set()
    while start < end:
        day = start.astimezone(ZoneInfo(cnemc.TIMEZONE)).date()
        data = resolved(job, codes, start)
        for code, fields in codes.items():
            for indicator in fields:
                found.add(Scope(data[code][1], code, indicator, day, day + timedelta(days=1)))
        start += HOUR
    return found


def validation_scope(grant):
    return Scope(
        grant.geography, grant.object_code, grant.indicator, grant.data_from, grant.data_until
    )


def endpoint_grants(job, endpoint, acquisition, now, rows):
    # Unscoped old allows cannot open a CNEMC endpoint. An unscoped deny still vetoes it.
    if job.validation_grant_id:
        grant = job.validation_grant
        if grant.validation_disabled:
            raise Denied("validation_grant_disabled")
        applicable(
            job.product,
            "retain_normalized",
            validation_scope(grant),
            {"validation_ledger"},
            now,
            rows,
        )
    eligible = [
        g
        for g in rows
        if not g.validation_disabled
        and (
            endpoint in g.endpoints or (not g.endpoints and (g.effect == "deny" or g.restrictions))
        )
    ]
    basis = applicable(job.product, "acquire_online", acquisition, {"*"}, now, eligible)
    bounded = {g.pk for g in eligible if g.pk in basis.ids and g.max_total_requests is not None}
    if bounded != ({job.validation_grant_id} if job.validation_grant_id else set()):
        raise Denied("validation_grant_required")
    return basis


def check_total(job):
    if job.validation_grant_id:
        cap = job.validation_grant.max_total_requests
        if (
            cap is None
            or Run.objects.filter(
                validation_grant_id=job.validation_grant_id, reserved_at__isnull=False
            ).count()
            >= cap
        ):
            raise Denied("validation_budget_exhausted")


def ensure_schedule(job):
    """Under the source lock, reject duplicate clocks and overlapping station request modes."""
    if not job.next_due_at:
        return
    others = (
        Job.objects.filter(
            product__source_id=job.product.source_id,
            profile=PROFILE,
            enabled=True,
            next_due_at__isnull=False,
            disposed=False,
        )
        .exclude(pk=job.pk)
        .select_related("product")
    )
    for other in others:
        if (
            other.product_id == job.product_id
            and other.city_id == job.city_id
            and other.cadence_seconds == job.cadence_seconds
        ):
            raise Denied("duplicate_schedule")
        if {job.product.code, other.product.code} == {"station-national-hour", "station-city-hour"}:
            national, city = (
                (job, other) if job.product.code == "station-national-hour" else (other, job)
            )
            codes = national.selection.get("objects")
            if not codes:
                raise Denied("station_schedule_conflict")
            # Evaluate the registered scope at actual operating time. Unresolved catalogue fails closed.
            from observations.policy import clock

            if any(v[2] == city.city_id for v in resolved(national, codes, clock()).values()):
                raise Denied("station_schedule_conflict")


def anchor(job, now):
    # Only retained observations from the current-package endpoint can establish a source clock.
    qs = RunItem.objects.filter(
        run__endpoint="A07",
        window__product_id=job.product_id,
        accepted=True,
        receipts__isnull=False,
        window__disposed=False,
    )
    selected = job.selection.get("objects")
    if selected:
        qs = qs.filter(object_code__in=selected)
    # A narrower job cannot use another metric's publication time to open a history request.
    # Only the most recent two distinct hours can be fresh enough. Recheck the data-use basis
    # at the anchor day, which can differ from the requested backfill day.
    stamps = qs.order_by("-observed_at").values_list("observed_at", flat=True).distinct()[:2]
    grants = list(job.product.usegrant_set.filter(revoked_at=None))
    for stamp in stamps:
        if not now - 2 * HOUR < stamp <= now:
            return None
        pairs = [
            (code, ind)
            for code, ind in qs.filter(observed_at=stamp)
            .values_list("object_code", "indicator")
            .distinct()
            if selected is None or ind in selected.get(code, ())
        ]
        if not pairs:
            continue
        day = stamp.astimezone(ZoneInfo(cnemc.TIMEZONE)).date()
        try:
            locations = resolved(job, {code for code, _ in pairs}, stamp)
        except Denied:
            return None
        for code, ind in pairs:
            scope = Scope(locations[code][1], code, ind, day, day + timedelta(days=1))
            try:
                applicable(
                    job.product,
                    "retain_normalized",
                    scope,
                    {"normalized", "provenance", "collection_ledger"},
                    now,
                    grants,
                )
                applicable(job.product, "process_local", scope, {"normalized"}, now, grants)
            except Denied:
                continue
            return stamp
    return None


def exact_available(job, target, now):
    policy = job.backfill_policy
    if not policy:
        return False
    stamp = anchor(job, now)
    return bool(
        stamp
        and max(
            stamp - HOUR * policy["lookback_hours"],
            datetime.fromisoformat(policy["operating_from"]),
        )
        <= target
        <= stamp
    )


def same_job(job):
    fields = (
        "product_id",
        "city_id",
        "executor_id",
        "profile",
        "expected_codes",
        "roster_basis",
        "selection",
        "standard",
        "backfill_policy",
        "cadence_seconds",
        "validation_grant_id",
    )
    attributes = {name: getattr(job, name) for name in fields}
    if attributes["expected_codes"] is None:
        del attributes["expected_codes"]
        attributes["expected_codes__isnull"] = True  # SQL NULL, distinct from JSON null.
    rows = Job.objects.filter(
        disposed=False,
        next_due_at__isnull=job.next_due_at is None,
        **attributes,
    ).order_by("pk")
    for row in rows:
        if (
            job.next_due_at is None
            or (job.next_due_at - row.next_due_at).total_seconds() % job.cadence_seconds == 0
        ):
            return row
    return None
