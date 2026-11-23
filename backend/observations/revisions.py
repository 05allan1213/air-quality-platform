"""Both observation tables use one ordered, transaction-local revision decision."""

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from django.core.signing import BadSignature, Signer
from django.db import transaction
from django.db.models import Max, Q
from django.utils.http import base36_to_int, int_to_base36

from .catalog import resolve
from .loss import require_continuous
from .models import (
    Batch,
    Change,
    CityObservation,
    CityRevision,
    MappingLink,
    QualityIssue,
    Receipt,
    StagedInput,
    StationObservation,
    StationRevision,
)
from .normalization import INDICATORS, BatchContext, Normalized, ProductProfile, normalize
from .policy import RAW, REQUIRED, Denied, Scope, actor, applicable, clock, locked_product
from .quality_events import (
    MAPPING_CONFLICTS,
    QUALITY_DETAIL,
    active_candidates,
    add_key_issue,
    advance_key_events,
    clear_event_state,
    keyed_issues,
    severity,
)


@dataclass(frozen=True)
class Candidate:
    data: Normalized
    token: int | None = None
    scheme: str = ""
    rule: int = 1


@dataclass
class WriteContext:
    product: object
    actor_id: int
    batch: Batch | None
    grants: list
    checks: set
    issued_at: datetime | None = None
    collection: bool = False


def tables(level):
    if level == "city":
        return CityObservation, CityRevision, "city", "city_key", "city_revision"
    return StationObservation, StationRevision, "site_epoch", "station_key", "station_revision"


def key_scope(key):
    geography = (
        key.city.geography if isinstance(key, CityObservation) else key.site_epoch.city.geography
    )
    return Scope(
        geography,
        key.object_code,
        key.indicator,
        key.source_local_date,
        key.source_local_date + timedelta(days=1),
    )


def check(ctx, op, scope, fields):
    now = clock()
    if ctx.issued_at is not None:
        require_continuous(ctx.product, op, scope, fields, now, since=ctx.issued_at)
    basis = applicable(ctx.product, op, scope, fields, now, ctx.grants)
    ctx.checks.add((op, scope, frozenset(fields)))
    return basis


def finish(ctx):
    actor(ctx.actor_id, "import_history")
    finish_checks(ctx)


def finish_checks(ctx):
    now = clock()
    for op, scope, fields in ctx.checks:
        applicable(ctx.product, op, scope, fields, now, ctx.grants)
        if ctx.issued_at is not None:
            require_continuous(ctx.product, op, scope, fields, now, since=ctx.issued_at)


def locate(ctx, candidate, *, for_write=True):
    d, t = candidate.data, candidate.data.time
    p = ctx.product
    if (
        t.source_timezone != p.source_timezone
        or d.indicator not in INDICATORS[p.shape]
        or (p.shape == "D") != (t.report_kind == "daily")
    ):
        raise Denied("product_mismatch")
    if candidate.token is not None and (
        not p.synthetic
        or candidate.scheme != "sequence"
        or type(candidate.token) is not int
        or not 0 <= candidate.token <= 2**63 - 1
    ):
        raise Denied("untrusted_version")
    if candidate.rule < 1:
        raise Denied("invalid_rule")
    entity, geo = resolve(
        p,
        d.object_code,
        t.observed_at_utc or t.window_start,
        t.window_start,
        t.window_end,
        for_write=for_write,
    )
    scope = Scope(
        geo,
        d.object_code,
        d.indicator,
        t.source_local_date,
        t.source_local_date + timedelta(days=1),
    )
    check(ctx, "process_local", scope, {"normalized"})
    check(ctx, "retain_normalized", scope, REQUIRED)
    try:
        check(ctx, "retain_normalized", scope, RAW)
        raw = True
    except Denied:
        raw = False
    obs, _, entity_field, _, _ = tables(p.level)
    identity = dict(
        product=p,
        **{entity_field: entity},
        indicator=d.indicator,
        report_kind=t.report_kind,
        window_definition=t.window_definition,
        standard_series=d.standard_series,
        observed_at_utc=t.observed_at_utc,
        local_report_date=t.local_report_date,
    )
    return obs, identity, raw


def content(d):
    return dict(
        value=d.value,
        unit=d.unit,
        quality=d.quality,
        limit_value=d.censor_limit,
        limit_operator=d.censor_direction,
        source_description=d.source_description,
    )


def provenance(d):
    t = d.time
    return dict(
        source_time=t.source_time,
        source_timezone=t.source_timezone,
        timezone_basis=t.timezone_basis,
        label_position=t.label_position,
        window_start=t.window_start.isoformat() if t.window_start else None,
        window_end=t.window_end.isoformat() if t.window_end else None,
        standard_basis=d.standard_basis,
        standard_evidence=d.standard_evidence,
        grade=d.grade,
        color=d.color,
        comparable=d.comparable,
        issue=d.issue,
        mapping=d.rule_version,
        source_published_at=d.source_published_at.isoformat() if d.source_published_at else None,
    )


def same(rev, d):
    return all(getattr(rev, k) == v for k, v in content(d).items()) and all(
        rev.provenance.get(k) == v
        for k, v in provenance(d).items()
        if k not in {"source_time", "source_published_at", "mapping"}
    )


def append(ctx, key, candidate, raw, kind, *, disputed=False, reason=""):
    d = candidate.data
    rev_model = tables(ctx.product.level)[1]
    number = (key.revisions.aggregate(n=Max("revision_no"))["n"] or 0) + 1
    if not disputed:
        key.revisions.filter(is_current=True).update(is_current=False)
    return rev_model.objects.create(
        key=key,
        revision_no=number,
        is_current=not disputed,
        candidate=disputed,
        kind=kind,
        actor_id=ctx.actor_id,
        reason=reason,
        rule_version=candidate.rule,
        source_token=candidate.token,
        raw_value=d.raw_value if raw else None,
        raw_unit=d.raw_unit if raw else None,
        provenance=provenance(d),
        **content(d),
    )


def permitted_optional_fields(ctx, key, d):
    scope = key_scope(key)
    for field in ("source_description", "source_time"):
        if not (d.source_description if field == "source_description" else d.time.source_time):
            continue
        try:
            check(ctx, "retain_normalized", scope, {field})
        except Denied:
            if field == "source_description":
                d = replace(d, source_description="")
            else:
                d = replace(d, time=replace(d.time, source_time=None))
    return d


def emit(product, key, kind):
    return Change.objects.create(
        product=product,
        kind=kind,
        rights_version=product.rights_version,
        qualification_version=key.qualification_version,
        **{tables(product.level)[3]: key},
    )


def issue(ctx, key, code):
    if ctx.collection:
        try:
            check(ctx, "retain_normalized", key_scope(key), QUALITY_DETAIL)
        except Denied:
            return None
    return add_key_issue(key, code, batch=ctx.batch, rule=key.rule_floor)


def decide(ctx, key, c, current, raw):
    # Compare order first. Same bytes at an older token are still a late receipt.
    if key.retired or c.rule < key.rule_floor:
        return current, "retired_mapping"
    ordered = c.token is not None and key.watermark is not None and c.scheme == key.trusted_scheme
    if ordered and c.token < key.watermark:
        issue(ctx, key, "late")
        return current, "late"
    newer = ordered and c.token > key.watermark
    if current is None or same(current, c.data):
        if current is None:
            current = append(ctx, key, c, raw, "initial")
            emit(ctx.product, key, "first")
            outcome = "first"
        else:
            outcome = "duplicate"
        if c.token is not None and (key.watermark is None or newer):
            key.watermark, key.trusted_scheme = c.token, c.scheme
            if outcome == "duplicate":
                outcome = "watermark_advanced"
        return current, outcome
    if newer:
        rev = append(ctx, key, c, raw, "source_revision")
        key.watermark = c.token
        emit(ctx.product, key, "content_changed")
        return rev, "content_changed"
    kind = "same_version_conflict" if ordered else "unordered_conflict"
    other = next(
        (
            r
            for r in active_candidates(key).filter(
                source_token=c.token, kind=kind, rule_version=c.rule
            )
            if same(r, c.data)
        ),
        None,
    )
    if other is None:
        other = append(ctx, key, c, raw, kind, disputed=True)
        issue(ctx, key, kind)
    if not key.disputed:
        key.disputed = True
        key.qualification_version += 1
        emit(ctx.product, key, "qualification_changed")
    return other, kind


def apply_observation(ctx, candidate, ordinal):
    """Caller holds the product lock and commits its receipt in this transaction."""
    d = candidate.data
    obs, identity, raw = locate(ctx, candidate)
    key, _ = obs.objects.select_for_update().get_or_create(
        **identity,
        defaults={"object_code": d.object_code, "source_local_date": d.time.source_local_date},
    )
    if key.object_code != d.object_code or key.source_local_date != d.time.source_local_date:
        raise Denied("identity_code_conflict")
    d = permitted_optional_fields(ctx, key, d)
    candidate = replace(candidate, data=d)
    current = key.revisions.filter(is_current=True).first()
    new, outcome = decide(ctx, key, candidate, current, raw)
    key.save(update_fields=["trusted_scheme", "watermark", "disputed", "qualification_version"])
    if d.issue and outcome not in {"late", "retired_mapping"}:
        issue(ctx, key, d.issue)
    evidence = Receipt.objects.create(
        batch=ctx.batch,
        ordinal=ordinal,
        rule_version=candidate.rule,
        locator=d.row_locator if raw else None,
        outcome=outcome,
        source_token=candidate.token,
        **{tables(ctx.product.level)[4]: new},
    )
    if outcome in {"first", "content_changed", "watermark_advanced"} or (
        new.candidate
        and outcome in {"same_version_conflict", "unordered_conflict"}
        and not Receipt.objects.filter(**{tables(ctx.product.level)[4]: new})
        .exclude(pk=evidence.pk)
        .exists()
    ):
        advance_key_events(key)
    if key.disputed and outcome in {"content_changed", "watermark_advanced"}:
        # A manually closed work item does not resolve the underlying dispute. A new
        # trusted head provides meaningful evidence even when it introduces no candidate.
        if active_candidates(key).filter(kind="same_version_conflict").exists():
            issues = keyed_issues(key).filter(code="same_version_conflict")
            if not issues.filter(resolved_at__isnull=True).exists():
                closed = issues.order_by("-pk").first()
                if closed is not None:
                    closed.status, closed.resolved_at, closed.handler_id = "open", None, None
                    closed.save(update_fields=["status", "resolved_at", "handler"])
    if (
        new.candidate
        and outcome in {"same_version_conflict", "unordered_conflict"}
        and (
            not ctx.collection
            or ("retain_normalized", key_scope(key), QUALITY_DETAIL) in ctx.checks
        )
    ):
        event = QualityIssue.objects.filter(
            product=ctx.product,
            code=outcome,
            resolved_at__isnull=True,
            **{tables(ctx.product.level)[3]: key},
        ).first()
        if event and event.receipt_id is None and event.batch_id == evidence.batch_id:
            event.receipt = evidence
            event.save(update_fields=["receipt"])
    return key, new, outcome


def fixture_ticket(product_id):
    """Opaque request identity. No source value, digest or user label is signed or retained."""
    issued = int_to_base36(int(clock().timestamp() * 1_000_000))
    return Signer(salt="observations.synthetic.batch").sign(f"{product_id}:{uuid4().hex}:{issued}")


def fixture_identity(product, ticket, profile):
    try:
        scope, key, *issued = Signer(salt="observations.synthetic.batch").unsign(ticket).split(":")
        if len(issued) > 1:
            raise ValueError
        stamp = (
            datetime.fromtimestamp(base36_to_int(issued[0]) / 1_000_000, UTC)
            if issued
            else datetime.min.replace(tzinfo=UTC)
        )
        if (
            int(scope) != product.pk
            or UUID(key).version != 4
            or profile != product.contract_version
        ):
            raise ValueError
        return str(UUID(key)), stamp
    except (BadSignature, TypeError, ValueError, AttributeError, OverflowError, OSError):
        raise Denied("invalid_batch") from None


def lock_stations(product, candidates, *, additional=()):
    if product.level == "station":
        # Products can share stations. Every writer takes station locks in PK order.
        from .models import Station

        codes = {candidate.data.object_code for candidate in candidates}
        station_ids = Station.objects.filter(
            source=product.source, stationidentifier__kind="code", stationidentifier__code__in=codes
        ).values_list("pk", flat=True)
        list(
            Station.objects.select_for_update()
            .filter(Q(pk__in=station_ids) | Q(pk__in=additional))
            .order_by("pk")
            .values_list("pk", flat=True)
        )


@transaction.atomic
def ingest_batch(actor_id, product_id, identity, profile, candidates, *, stage_id=None):
    product = locked_product(product_id)
    actor(actor_id, "import_history")
    candidates = list(candidates)
    if (
        not candidates
        or len(candidates) > 1152
        or not identity
        or not isinstance(identity, str)
        or len(identity) > 150
        or not profile
        or len(profile) > 100
    ):
        raise Denied("invalid_batch")
    if product.family != "F" and not (
        product.family == "H" and product.source.namespace == "uci.501" and product.code == "uci501"
    ):
        raise Denied()
    grants = list(product.usegrant_set.filter(revoked_at__isnull=True))
    if product.family == "H":
        from .disposition import PACKAGE_SCOPE
        from .history import check_import
        from .import_uci import PROFILE, SHA256

        if (profile, identity) != (PROFILE, SHA256):
            raise Denied("invalid_batch")
        now = clock()
        stage = StagedInput.objects.filter(
            pk=stage_id,
            product=product,
            owner_id=actor_id,
            state="claimed",
            created_at__lte=now,
            expires_at__gt=now,
        ).first()
        if stage is None:
            raise Denied("invalid_stage")
        check_import(product, actor_id, stage=stage)
        issued = stage.created_at
    else:
        if stage_id is not None:
            raise Denied("invalid_stage")
        identity, issued = fixture_identity(product, identity, profile)
    ctx = WriteContext(product, actor_id, None, grants, set(), issued_at=issued)
    if product.family == "H":
        check(ctx, "retain_raw", PACKAGE_SCOPE, {"archive"})
        check(ctx, "retain_normalized", PACKAGE_SCOPE, {"batch_identity"})
    lock_stations(product, candidates)
    batch, created = Batch.objects.get_or_create(
        product=product,
        identity=identity,
        profile=profile,
        defaults={"fetched_at": candidates[0].data.fetched_at},
    )
    ctx.batch = batch
    for i, c in enumerate(candidates, 1):
        try:
            if created:
                apply_observation(ctx, c, i)
            else:
                locate(ctx, c)
        except Denied as exc:
            if exc.code not in {"identity_unresolved", "identity_window_crossing"}:
                raise
            # Without attributable geography only a whole-product grant permits a minimal issue.
            d = c.data
            scope = Scope(
                "*",
                d.object_code,
                d.indicator,
                d.time.source_local_date,
                d.time.source_local_date + timedelta(days=1),
            )
            check(ctx, "process_local", scope, {"normalized"})
            check(ctx, "retain_normalized", scope, REQUIRED)
            QualityIssue.objects.get_or_create(
                product=product,
                batch=batch,
                code=exc.code,
                scope=scope.serialize(),
                severity=severity(exc.code),
                rule_version=c.rule,
            )
    finish(ctx)
    return batch, created


def mapping_time(current):
    """A retained source label, or the unambiguous authorized logical key time."""
    label = current.provenance.get("source_time")
    if label:
        return label
    key = current.key
    if key.local_report_date:
        return key.local_report_date.isoformat()
    return key.observed_at_utc.astimezone(
        ZoneInfo(current.provenance["source_timezone"])
    ).isoformat()


@transaction.atomic
def correct_mapping(
    actor_id, product_id, level, key_id, base_revision, base_watermark, candidate, reason
):
    product = locked_product(product_id)
    actor(actor_id, "review_observation")
    if product.level != level or not reason or len(reason) > 100:
        raise Denied("invalid_correction")
    obs, _, _, _, _ = tables(level)
    # Lock the registered source and target Station set before either logical key.
    # Product lock already serializes observation writes for this product.
    preflight = obs.objects.get(product=product, pk=key_id)
    if level == "station":
        lock_stations(product, [candidate], additional=[preflight.site_epoch.station_id])
    key = obs.objects.select_for_update().get(pk=preflight.pk)
    current = key.revisions.get(is_current=True)
    grants = list(product.usegrant_set.filter(revoked_at__isnull=True))
    scope = key_scope(key)
    for op, fields in [("review", RAW), ("retain_normalized", RAW)]:
        applicable(product, op, scope, fields, clock(), grants)
    ctx = WriteContext(product, actor_id, None, grants, set())
    if (
        current.pk != base_revision
        or key.watermark != base_watermark
        or key.retired
        or current.raw_value is None
        or candidate.rule <= key.rule_floor
        or candidate.data.raw_value != current.raw_value
    ):
        issue(ctx, key, "mapping_review_required")
        return "mapping_review_required"
    if (
        not product.synthetic
        or candidate.rule != 2
        or candidate.data.indicator != "CO"
        or current.raw_unit is None
    ):
        raise Denied("mapping_evidence_mismatch")
    # Registered F-v2 mapping: the archived source CO field is in µg/m³.
    # The same evidence can be remapped to the operator-identified correct entity.
    data = candidate.data
    profile = ProductProfile(
        product.shape,
        current.provenance["source_timezone"],
        current.provenance["timezone_basis"],
        data.time.window_definition,
        rule_version="f.co.v2",
        allow_naive_label=True,
    )
    recomputed = normalize(
        profile,
        dict(
            object_code=data.object_code,
            source_time=mapping_time(current),
            indicator="CO",
            raw_value=current.raw_value,
            raw_unit="µg/m³",
            source_description=current.source_description,
            row_locator=data.row_locator,
        ),
        BatchContext(data.fetched_at),
    )
    if (
        content(recomputed) != content(data)
        or recomputed.time != data.time
        or candidate.token not in {None, base_watermark}
    ):
        raise Denied("mapping_evidence_mismatch")
    candidate = Candidate(recomputed, base_watermark, key.trusted_scheme, 2)
    _, identity, raw = locate(ctx, candidate)
    target, _ = obs.objects.select_for_update().get_or_create(
        **identity,
        defaults={
            "object_code": candidate.data.object_code,
            "source_local_date": candidate.data.time.source_local_date,
        },
    )
    if target.object_code != candidate.data.object_code:
        raise Denied("identity_code_conflict")
    # A logical time reconstructed from the key is never an original source label.
    corrected = replace(
        recomputed,
        time=replace(
            recomputed.time,
            source_time=current.provenance.get("source_time"),
        ),
    )
    candidate = replace(candidate, data=permitted_optional_fields(ctx, target, corrected))
    # A local mapping rule cannot retire or clear an independent upstream conflict.
    source_conflict = active_candidates(key).exclude(kind="mapping_conflict").exists()
    if source_conflict and target.pk != key.pk:
        raise Denied("mapping_evidence_mismatch")
    if target.pk != key.pk:
        target_scope = key_scope(target)
        check(ctx, "review", target_scope, RAW)
        check(ctx, "retain_normalized", target_scope, RAW)
        if target.disputed or target.retired or target.rule_floor > candidate.rule:
            issue(ctx, key, "mapping_review_required")
            finish_correction(ctx, scope)
            return "mapping_review_required"
        MappingLink.objects.create(
            product=product,
            rule=candidate.rule,
            actor_id=actor_id,
            reason=reason,
            **{level + "_old": key, level + "_new": target},
        )
        existing = target.revisions.filter(is_current=True).first()
        if existing and not same(existing, candidate.data):
            append(ctx, target, candidate, raw, "mapping_conflict", disputed=True, reason=reason)
            for affected in (target, key):
                if not affected.disputed:
                    affected.disputed = True
                    affected.qualification_version += 1
                    affected.save(update_fields=["disputed", "qualification_version"])
                    emit(product, affected, "qualification_changed")
                issue(ctx, affected, "mapping_conflict")
                advance_key_events(affected)
            finish_correction(ctx, scope)
            return "mapping_conflict"
        key.retired = True
        key.qualification_version += 1
        key.save(update_fields=["retired", "qualification_version"])
        emit(product, key, "mapping_retired")
        advance_key_events(key)
        if existing is None:
            target.watermark, target.trusted_scheme = key.watermark, key.trusted_scheme
    append(ctx, target, candidate, raw, "local_mapping_fix", reason=reason)
    if not source_conflict and not active_candidates(target).exists():
        if target.disputed:
            target.disputed = False
            target.qualification_version += 1
            emit(product, target, "qualification_changed")
        clear_event_state(target, MAPPING_CONFLICTS, clock())
    target.rule_floor = candidate.rule
    target.save()
    advance_key_events(target)
    emit(product, target, "local_mapping_fix")
    finish_correction(ctx, scope)
    return "local_mapping_fix"


def finish_correction(ctx, scope):
    actor(ctx.actor_id, "review_observation")
    now = clock()
    for op, fields in [("review", RAW), ("retain_normalized", RAW)]:
        applicable(ctx.product, op, scope, fields, now, ctx.grants)
    for op, target, fields in ctx.checks:
        applicable(ctx.product, op, target, fields, now, ctx.grants)
