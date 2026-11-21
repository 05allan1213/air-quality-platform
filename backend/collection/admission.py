"""Catalogue identity, complete-request rights and PG-serialized request budgets."""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from observations.catalog import resolve
from observations.models import CityIdentifier, Source
from observations.normalization import INDICATORS
from observations.policy import REQUIRED, Denied, Scope, actor, applicable, clock, locked_product

from .models import Job, Run

HOUR = timedelta(hours=1)
LEDGER = REQUIRED | {"collection_ledger"}
PROFILES = {"f-exact-v1", "f-latest-v1"}


def validate_limits(limits):
    bounds = {
        "min_interval_seconds": (0, 86400),
        "max_concurrent": (1, 32),
        "period_seconds": (1, 86400),
        "max_requests": (1, 10000),
    }
    if (
        not isinstance(limits, dict)
        or set(limits) != set(bounds)
        or any(
            type(limits[k]) is not int or not lo <= limits[k] <= hi
            for k, (lo, hi) in bounds.items()
        )
    ):
        raise Denied("unconfirmed_limits")
    return limits


def lock_job(job_id):
    path = Job.objects.filter(pk=job_id).values_list("product__source_id", "product_id").first()
    if not path:
        raise Denied()
    source = Source.objects.select_for_update().get(pk=path[0])
    product = locked_product(path[1])
    product.source = source
    job = Job.objects.select_for_update(of=("self",)).select_related("city").get(pk=job_id)
    job.product = product
    return job


def registry(job):
    p = job.product
    if not (
        settings.S03_FIXTURE_HTTP
        and p.source.namespace == "s03.fixture"
        and p.synthetic
        and p.family == "F"
        and p.shape in {"L", "C", "S", "T"}
        and p.code == p.shape
        and p.contract_version == "s03.f.v1"
        and job.profile in PROFILES
        and (
            (p.shape == "L" and job.city_id is None)
            or (p.shape != "L" and job.city_id and job.city.source_id == p.source_id)
        )
    ):
        raise Denied("unregistered_profile")


def parse_hour(value):
    if not isinstance(value, str) or len(value) > 40:
        raise Denied("invalid_target")
    try:
        ts = datetime.fromisoformat(value)
        if ts.utcoffset() is None:
            raise ValueError
        ts = ts.astimezone(UTC)
        if ts.minute or ts.second or ts.microsecond or not 2 <= ts.year <= 9998:
            raise ValueError
        return ts
    except (ValueError, OverflowError):
        raise Denied("invalid_target") from None


def bounds(job, target):
    return (target - 23 * HOUR if job.product.shape == "T" else target, target + HOUR)


def target_code(job, target):
    if not job.city_id:
        return ""
    codes = list(
        CityIdentifier.objects.filter(
            city_id=job.city_id,
            source=job.product.source,
            kind="code",
            active_period__contains=target,
        ).values_list("code", flat=True)[:2]
    )
    if len(codes) != 1:
        raise Denied("identity_unresolved")
    return codes[0]


def data_scope(job, start, end):
    zone = ZoneInfo(job.product.source_timezone)
    return Scope(
        job.city.geography if job.city_id else "*",
        target_code(job, end - HOUR) if job.product.shape in {"C", "T"} else "*",
        "*",
        start.astimezone(zone).date(),
        (end - timedelta(microseconds=1)).astimezone(zone).date() + timedelta(days=1),
    )


def admission(job, actor_id, start, end, *, allow_paused=False):
    registry(job)
    actor(actor_id, "run_collection")
    actor(job.executor_id, "run_collection")
    if not allow_paused and (
        not job.enabled
        or job.pause_reason
        or job.product.source.collection_pause
        or job.product.collection_pause
    ):
        raise Denied("collection_paused")
    p = job.product
    processing = data_scope(job, start, end)
    acquisition = processing
    if job.profile == "f-latest-v1":
        # The latest endpoint has no verified lower bound on publication dates.
        acquisition = Scope(processing.geography, processing.object_code, "*", date.min, date.max)
    now = clock()
    rows = list(p.usegrant_set.filter(revoked_at=None))
    basis = applicable(p, "acquire_online", acquisition, {"*"}, now, rows)
    selected_use(job, start, end, "process_local", {"normalized"}, now, rows)
    selected_use(job, start, end, "retain_normalized", LEDGER, now, rows)
    source_limits = validate_limits(p.source.collection_limits)
    # Each quota belongs to a complete applicable grant. An unconfigured grant cannot authorize a fetch.
    caps = [
        validate_limits(g.collection_limits)
        for g in rows
        if g.pk in basis.ids and g.collection_limits is not None
    ]
    if not caps:
        raise Denied("unconfirmed_limits")
    # Protect the final SQL write if a permission boundary falls between checks.
    boundary = min(
        (
            t
            for g in rows
            if g.operation in {"acquire_online", "process_local", "retain_normalized"}
            for t in (g.valid_from, g.valid_until, g.retain_until)
            if t and t > now
        ),
        default=None,
    )
    return source_limits, caps, boundary


def can_fetch(job, end, now):
    if end - HOUR > now:
        return False
    if job.profile == "f-exact-v1":
        return True
    # A latest-only request must never be advertised as retrieving a historical window.
    return now < end


class BudgetExhausted(Denied):
    def __init__(self, check_at):
        super().__init__("budget_exhausted")
        self.check_at = check_at


def check_budget(runs, limits, now):
    """Earliest admissible time under one set of limits, under the Source lock."""

    def nth_latest(field, cutoff, n):
        rows = runs.filter(**{f"{field}__gt": cutoff}).order_by(f"-{field}")
        return rows.values_list(field, flat=True)[n - 1 : n].first()

    period = timedelta(seconds=limits["period_seconds"])
    interval = timedelta(seconds=limits["min_interval_seconds"])
    # If limits have tightened, several reservations may have to expire.
    rolling = nth_latest("reserved_at", now - period, limits["max_requests"])
    latest = nth_latest("reserved_at", now - interval, 1) if interval else None
    slot = nth_latest("occupied_until", now, limits["max_concurrent"])
    return max(
        now, rolling + period if rolling else now, latest + interval if latest else now, slot or now
    )


def budget(job, caps, now):
    source_limits, grant_limits, boundary = caps
    all_runs = Run.objects.filter(
        job__product__source_id=job.product.source_id, reserved_at__isnull=False
    )
    product_runs = all_runs.filter(job__product=job.product)
    check_at = max(
        check_budget(all_runs, source_limits, now),
        *(check_budget(product_runs, limits, now) for limits in grant_limits),
    )
    if check_at > now:
        raise BudgetExhausted(min(check_at, boundary) if boundary else check_at)


def cell_key(entity_id, observed_at, indicator):
    return f"{entity_id}/{observed_at.isoformat()}/{indicator}"


def expected(job, start, end):
    codes = job.expected_codes
    if codes is None:
        return None
    if (
        not job.roster_basis
        or not isinstance(codes, list)
        or len(codes) > 512
        or not all(isinstance(c, str) and 0 < len(c) <= 100 for c in codes)
        or len(codes) != len(set(codes))
    ):
        raise Denied("invalid_roster")
    cells = {}
    p = job.product
    while start < end:
        for code in codes:
            try:
                entity, _ = resolve(p, code, start)
            except Denied:
                # No period in the verified catalogue at this target label.
                continue
            city_id = entity.city_id if p.level == "station" else entity.pk
            if job.city_id and job.city_id != city_id:
                raise Denied("invalid_roster")
            for indicator in INDICATORS[p.shape]:
                cells[cell_key(entity.pk, start, indicator)] = [code, start.isoformat(), indicator]
        start += HOUR
    return cells


def selected_scopes(job, start, end):
    """Explicit processing selection. Acquisition still covers the whole returned package."""
    scope = data_scope(job, start, end)
    if job.expected_codes is None:
        return [replace(scope, indicator=ind) for ind in INDICATORS[job.product.shape]]
    codes = job.expected_codes
    if (
        not isinstance(codes, list)
        or len(codes) > 512
        or not all(type(c) is str and 0 < len(c) <= 100 for c in codes)
    ):
        raise Denied("invalid_roster")
    result = set()
    while start < end:
        local = start.astimezone(ZoneInfo(job.product.source_timezone)).date()
        for code in codes:
            try:
                entity, geo = resolve(job.product, code, start)
            except Denied:
                continue
            if job.city_id and job.city_id != (
                entity.city_id if job.product.level == "station" else entity.pk
            ):
                raise Denied("invalid_roster")
            for ind in INDICATORS[job.product.shape]:
                result.add(Scope(geo, code, ind, local, local + timedelta(days=1)))
        start += HOUR
    return result or [scope]


def selected_use(job, start, end, op, fields, now, rows):
    try:
        applicable(job.product, op, data_scope(job, start, end), fields, now, rows)
    except Denied:
        for scope in selected_scopes(job, start, end):
            applicable(job.product, op, scope, fields, now, rows)
