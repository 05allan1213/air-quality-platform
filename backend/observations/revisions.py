"""Both observation tables use one ordered, transaction-local revision decision."""

from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID, uuid4

from django.core.signing import BadSignature, Signer
from django.db import transaction
from django.db.models import Max

from .catalog import resolve
from .models import (
    Batch,
    Change,
    CityObservation,
    CityRevision,
    MappingLink,
    QualityIssue,
    Receipt,
    StationObservation,
    StationRevision,
)
from .normalization import INDICATORS, BatchContext, Normalized, ProductProfile, normalize
from .policy import RAW, REQUIRED, Denied, Scope, actor, applicable, clock, locked_product


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
    batch: Batch
    grants: list
    checks: set


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
    basis = applicable(ctx.product, op, scope, fields, clock(), ctx.grants)
    ctx.checks.add((op, scope, frozenset(fields)))
    return basis


def finish(ctx):
    actor(ctx.actor_id, "import_history")
    finish_checks(ctx)


def finish_checks(ctx):
    now = clock()
    for op, scope, fields in ctx.checks:
        applicable(ctx.product, op, scope, fields, now, ctx.grants)


def locate(ctx, candidate):
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
        for_write=True,
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


def emit(product, key, kind):
    return Change.objects.create(
        product=product,
        kind=kind,
        rights_version=product.rights_version,
        qualification_version=key.qualification_version,
        **{tables(product.level)[3]: key},
    )


def issue(ctx, key, code):
    QualityIssue.objects.get_or_create(
        product=ctx.product,
        code=code,
        resolved_at__isnull=True,
        **{tables(ctx.product.level)[3]: key},
        defaults={"batch": ctx.batch},
    )


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
            for r in key.revisions.filter(candidate=True, source_token=c.token, kind=kind)
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
    current = key.revisions.filter(is_current=True).first()
    new, outcome = decide(ctx, key, candidate, current, raw)
    key.save(update_fields=["trusted_scheme", "watermark", "disputed", "qualification_version"])
    if d.issue and outcome not in {"late", "retired_mapping"}:
        issue(ctx, key, d.issue)
    Receipt.objects.create(
        batch=ctx.batch,
        ordinal=ordinal,
        rule_version=candidate.rule,
        locator=d.row_locator if raw else None,
        outcome=outcome,
        source_token=candidate.token,
        **{tables(ctx.product.level)[4]: new},
    )
    return key, new, outcome


def fixture_ticket(product_id):
    """Opaque request identity. No source value, digest or user label is signed or retained."""
    return Signer(salt="observations.synthetic.batch").sign(f"{product_id}:{uuid4()}")


def fixture_identity(product, ticket, profile):
    try:
        scope, key = Signer(salt="observations.synthetic.batch").unsign(ticket).split(":")
        if (
            int(scope) != product.pk
            or UUID(key).version != 4
            or profile != product.contract_version
        ):
            raise ValueError
        return key
    except (BadSignature, TypeError, ValueError, AttributeError):
        raise Denied("invalid_batch") from None


def lock_stations(product, candidates):
    if product.level == "station":
        # Products can share stations. Every writer takes station locks in PK order.
        from .models import Station

        codes = {candidate.data.object_code for candidate in candidates}
        station_ids = Station.objects.filter(
            source=product.source, stationidentifier__kind="code", stationidentifier__code__in=codes
        ).values_list("pk", flat=True)
        list(
            Station.objects.select_for_update()
            .filter(pk__in=station_ids)
            .order_by("pk")
            .values_list("pk", flat=True)
        )


@transaction.atomic
def ingest_batch(actor_id, product_id, identity, profile, candidates):
    product = locked_product(product_id)
    actor(actor_id, "import_history")
    candidates = list(candidates)
    if (
        not candidates
        or len(candidates) > 1152
        or not identity
        or len(identity) > 100
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
        from .import_uci import PROFILE, SHA256

        if (profile, identity) != (PROFILE, SHA256):
            raise Denied("invalid_batch")
        applicable(product, "retain_normalized", PACKAGE_SCOPE, {"batch_identity"}, clock(), grants)
    else:
        identity = fixture_identity(product, identity, profile)
    lock_stations(product, candidates)
    batch, created = Batch.objects.get_or_create(
        product=product,
        identity=identity,
        profile=profile,
        defaults={"fetched_at": candidates[0].data.fetched_at},
    )
    ctx = WriteContext(product, actor_id, batch, grants, set())
    if product.family == "H":
        check(ctx, "retain_normalized", PACKAGE_SCOPE, {"batch_identity"})
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
                product=product, batch=batch, code=exc.code, scope=scope.serialize()
            )
    finish(ctx)
    return batch, created


@transaction.atomic
def correct_mapping(
    actor_id, product_id, level, key_id, base_revision, base_watermark, candidate, reason
):
    product = locked_product(product_id)
    actor(actor_id, "review_observation")
    if product.level != level or not reason or len(reason) > 100:
        raise Denied("invalid_correction")
    obs, _, _, fk, _ = tables(level)
    key = obs.objects.select_for_update().get(product=product, pk=key_id)
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
            source_time=current.provenance["source_time"],
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
    if target.pk != key.pk:
        target_scope = key_scope(target)
        check(ctx, "review", target_scope, RAW)
        check(ctx, "retain_normalized", target_scope, RAW)
        if target.disputed or target.retired:
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
                affected.disputed = True
                affected.qualification_version += 1
                affected.save(update_fields=["disputed", "qualification_version"])
                issue(ctx, affected, "mapping_conflict")
                emit(product, affected, "qualification_changed")
            finish_correction(ctx, scope)
            return "mapping_conflict"
        key.retired = True
        key.qualification_version += 1
        key.save(update_fields=["retired", "qualification_version"])
        emit(product, key, "mapping_retired")
        if existing is None:
            target.watermark, target.trusted_scheme = key.watermark, key.trusted_scheme
    append(ctx, target, candidate, raw, "local_mapping_fix", reason=reason)
    target.disputed = False
    target.qualification_version += 1
    target.rule_floor = candidate.rule
    target.save()
    QualityIssue.objects.filter(
        **{fk: target},
        resolved_at__isnull=True,
        code__in=["mapping_conflict", "same_version_conflict", "unordered_conflict"],
    ).update(resolved_at=clock())
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
