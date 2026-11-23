"""Adjudication of the complete locked observation ledger using registered evidence."""

import re

from accounts.governance import command, receipt, replay
from accounts.research_session import SessionCheck, request_transaction
from django.contrib.auth.models import Group
from django.core import signing
from django.db.models import Q
from samples.normalization import Rejected

from .models import (
    CandidateClearance,
    CityObservation,
    QualityDecision,
    QualityIssue,
    Receipt,
    Source,
    Station,
)
from .normalization import BatchContext, ProductProfile, normalize
from .policy import (
    RAW,
    REQUIRED,
    Denied,
    Scope,
    actor,
    applicable,
    batch_issue_fields,
    locked_product,
)
from .quality_events import (
    MAPPING_CONFLICTS,
    SOURCE_CONFLICTS,
    active_candidates,
    advance_key_events,
    check_detail,
    collection_issue,
    issue_linkage,
    keyed_issues,
)
from .revisions import (
    Candidate,
    WriteContext,
    correct_mapping,
    emit,
    key_scope,
    locate,
    mapping_time,
    tables,
)
from .s04_http import fingerprint

PAGE = 50
EVIDENCE = "observations.quality.evidence.v1"
REASONS = {
    "claim_review",
    "retain_isolation",
    "review_complete",
    "verified_correction",
    "registered_mapping",
}
ACTIONS = {"claim", "hold", "close", "correct_source", "correct_mapping"}


def user_permission(user_id, permission):
    user = actor(user_id)
    # User is already locked by SessionCheck. Group permissions have the same fence as P01.
    list(Group.objects.select_for_update().filter(user=user).order_by("pk"))
    if not user.has_perm("observations.review_observation") or (
        permission and not user.has_perm(permission)
    ):
        raise Denied("permission_denied")
    return user


def product_lock(product_id):
    from .models import Product

    source_id = Product.objects.filter(pk=product_id).values_list("source_id", flat=True).first()
    if source_id is None:
        raise Denied()
    Source.objects.select_for_update().get(pk=source_id)
    product = locked_product(product_id)
    if product.source_id != source_id:
        raise Denied("input_changed")
    return product


def checks(event, product, key, grants, now, *, raw=False):
    issue_linkage(event)
    if key and key.product_id != product.pk:
        raise Denied()
    if key and event.city_key_id and product.level != "city":
        raise Denied()
    if key and event.station_key_id and product.level != "station":
        raise Denied()
    scope = key_scope(key) if key else Scope.from_issue(event.scope)
    fields = RAW if raw else REQUIRED if key else batch_issue_fields(product)
    review_retained(product, scope, fields, now, grants)
    if collection_issue(event):
        check_detail(product, scope, now, grants, review=True)
    return scope


def review_retained(product, scope, fields, now, grants):
    for operation in ("review", "retain_normalized"):
        applicable(product, operation, scope, fields, now, grants)


def lock_key(product, event, target_code=None):
    key_id = event.city_key_id or event.station_key_id
    if key_id is None:
        return None
    rows, _, _, _, _ = tables(product.level)
    keys = rows.objects.filter(pk=key_id, product=product)
    if event.station_key_id:
        station_ids = keys.values("site_epoch__station_id")
        selection = Q(pk__in=station_ids)
        if target_code is not None:
            targets = Station.objects.filter(
                source=product.source,
                stationidentifier__kind="code",
                stationidentifier__code=target_code,
            ).values("pk")
            selection |= Q(pk__in=targets)
        list(Station.objects.select_for_update().filter(selection).order_by("pk"))
    key = keys.select_for_update().first()
    if key is None:
        raise Denied()
    return key


def project_scope(product, key, scope):
    result = {"product": product.pk, "synthetic": product.synthetic, **scope.serialize()}
    if key:
        fk = "city_id" if isinstance(key, CityObservation) else "site_epoch_id"
        result.update(
            {
                fk: getattr(key, fk),
                "indicator": key.indicator,
                "report_kind": key.report_kind,
                "window_definition": key.window_definition,
                "standard_series": key.standard_series,
                "observed_at": key.observed_at_utc,
                "query_types": ["observation_detail", "analysis", "analysis_details", "export"],
            }
        )
    return result


def event_result(event, product, key, scope):
    return {
        "id": event.pk,
        "event_version": event.event_version,
        "status": event.status,
        "severity": event.severity,
        "code": event.code,
        "handler": event.handler_id,
        "rule_version": event.rule_version,
        "created_at": event.created_at,
        "resolved_at": event.resolved_at,
        "run": event.run_id,
        "run_item": event.run_item_id,
        "window": event.run.window_id if event.run_id else None,
        "window_start": event.run.window.target_start if event.run_id else None,
        "window_end": event.run.window.target_end if event.run_id else None,
        "batch": event.batch_id,
        "receipt": event.receipt_id,
        "affected": project_scope(product, key, scope),
    }


def snapshot(product, key):
    current = key.revisions.filter(is_current=True).first()
    candidates = list(active_candidates(key).order_by("pk")[: PAGE + 1])
    if len(candidates) > PAGE:
        raise Denied("query_too_large")
    # Bind both identity and fairness: a candidate from any event under this key participates.
    edition = [
        product.pk,
        product.source_id,
        product.contract_version,
        key.pk,
        key.watermark,
        key.trusted_scheme,
        key.rule_floor,
        key.qualification_version,
        key.disputed,
        key.retired,
        current.pk if current else None,
        [[c.pk, c.kind, c.source_token, c.rule_version] for c in candidates],
    ]
    return current, candidates, fingerprint(edition)


def correction_evidence(product, key, current, candidates):
    """The registered F sequence supersedes every strictly earlier compatible candidate."""
    if (
        not key.disputed
        or key.retired
        or not current
        or not candidates
        or not product.synthetic
        or product.family != "F"
        or key.trusted_scheme != "sequence"
        or key.watermark is None
        or current.rule_version != key.rule_floor
        or keyed_issues(key).filter(code__in=MAPPING_CONFLICTS).exists()
        or any(
            c.kind not in SOURCE_CONFLICTS
            or c.rule_version != key.rule_floor
            or c.source_token is None
            or c.source_token >= key.watermark
            for c in candidates
        )
    ):
        return None
    _, _, _, _, fk = tables(product.level)
    return (
        Receipt.objects.filter(
            batch__product=product,
            batch__redacted=False,
            source_token=key.watermark,
            rule_version=key.rule_floor,
            outcome__in=["first", "content_changed", "watermark_advanced"],
            **{fk: current},
        )
        .order_by("pk")
        .first()
    )


def mapping_candidate(product, key, current, now, *, code=None):
    if (
        not product.synthetic
        or product.family != "F"
        or key.retired
        or key.rule_floor >= 2
        or current is None
        or current.raw_value is None
        or current.raw_unit is None
        or key.indicator != "CO"
    ):
        raise Denied("mapping_evidence_mismatch")
    profile = ProductProfile(
        product.shape,
        product.source_timezone,
        current.provenance["timezone_basis"],
        key.window_definition,
        rule_version="f.co.v2",
        allow_naive_label=True,
    )
    try:
        data = normalize(
            profile,
            {
                "object_code": code or key.object_code,
                "indicator": "CO",
                "source_time": mapping_time(current),
                "raw_value": current.raw_value,
                "raw_unit": "µg/m³",
                "source_description": current.source_description,
            },
            BatchContext(now),
        )
    except (Rejected, KeyError):
        raise Denied("mapping_evidence_mismatch") from None
    if (data.time.observed_at_utc, data.time.local_report_date, data.standard_series) != (
        key.observed_at_utc,
        key.local_report_date,
        key.standard_series,
    ):
        raise Denied("mapping_evidence_mismatch")
    return Candidate(data, key.watermark, key.trusted_scheme, 2)


def sign_ref(event, user, kind, edition, selector):
    return signing.dumps(
        [event.pk, user.pk, event.event_version, kind, edition, selector],
        salt=EVIDENCE,
        compress=True,
    )


def decode_ref(token, event, user, kind):
    if (
        not isinstance(token, str)
        or len(token) > 2048
        or not re.fullmatch(r"[A-Za-z0-9:_.-]+", token)
    ):
        raise Denied("invalid_request")
    try:
        cert = signing.loads(token, salt=EVIDENCE, max_age=1800)
    except signing.BadSignature:
        raise Denied("mapping_evidence_mismatch") from None
    if cert[:2] != [event.pk, user.pk] or cert[3] != kind:
        raise Denied("mapping_evidence_mismatch")
    return cert


def mapping_selection(product, key, current, grants, now, target_code):
    candidate = mapping_candidate(product, key, current, now, code=target_code)
    ctx = WriteContext(product, current.actor_id, None, grants, set())
    rows, identity, _ = locate(ctx, candidate, for_write=False)
    target = rows.objects.filter(**identity).first()
    entity = identity[tables(product.level)[2]]
    geography = entity.geography if isinstance(key, CityObservation) else entity.city.geography
    scope = Scope(
        geography,
        candidate.data.object_code,
        key.indicator,
        key.source_local_date,
        key_scope(key).end,
    )
    review_retained(product, scope, RAW, now, grants)
    target_edition = snapshot(product, target)[2] if target else None
    selector = [
        current.pk,
        candidate.data.object_code,
        entity.pk,
        target_edition,
        candidate.rule,
        scope.serialize(),
    ]
    return candidate, selector, scope, rows, identity


def project_refs(event, product, key, user, grants, now, target_code):
    current, candidates, edition = snapshot(product, key)
    correction = correction_evidence(product, key, current, candidates)
    refs = {}
    if correction and event.code in SOURCE_CONFLICTS and event.status != "resolved":
        refs["correct_source"] = sign_ref(event, user, "correct_source", edition, correction.pk)
    try:
        checks(event, product, key, grants, now, raw=True)
        _, selector, _, _, _ = mapping_selection(product, key, current, grants, now, target_code)
    except Denied:
        pass
    else:
        if event.status != "resolved":
            refs["correct_mapping"] = sign_ref(event, user, "correct_mapping", edition, selector)
    return {
        "current_revision": current.pk if current else None,
        "watermark": key.watermark,
        "qualification_version": key.qualification_version,
        "disputed": key.disputed,
        "candidate_revisions": [c.pk for c in candidates],
        "evidence": refs,
    }


def list_issues(request, values):
    with request_transaction():
        product = product_lock(values["product"])
        auth = SessionCheck(request)
        user_permission(auth.user_id, None)
        if product.synthetic != values["synthetic"]:
            raise Denied()
        rows = QualityIssue.objects.filter(product=product, pk__lt=values["before"]).order_by("-pk")
        if values["run"]:
            rows = rows.filter(run_id=values["run"])
        if values["window"]:
            rows = rows.filter(run__window_id=values["window"])
        if values["status"]:
            rows = rows.filter(status=values["status"])
        rows = rows.select_related(
            "city_key__city",
            "station_key__site_epoch__city",
            "run__job",
            "run__window",
            "run_item",
            "batch",
            "receipt",
        )
        grants = list(product.usegrant_set.filter(revoked_at__isnull=True))
        # Cursor advances on visible rows only. Scan bounded batches internally so hidden rows
        # don't disclose their IDs in a next cursor or suppress the visible suffix.
        published = []
        for event in rows.iterator(chunk_size=PAGE):
            key = event.city_key or event.station_key
            try:
                span = checks(event, product, key, grants, auth.now)
            except Denied:
                continue
            published.append((event, key, span))
            if len(published) > PAGE:
                break
        now = auth.finish(())
        for event, key, _ in published:
            checks(event, product, key, grants, now)
        return {
            "results": [
                event_result(event, product, key, span) for event, key, span in published[:PAGE]
            ],
            "next_before": published[PAGE - 1][0].pk if len(published) > PAGE else None,
        }


def close_source(product, key, candidates, correction, record):
    _, _, _, _, fk = tables(product.level)
    CandidateClearance.objects.bulk_create(
        [CandidateClearance(**{fk: c}, correction=correction, action=record) for c in candidates]
    )
    key.disputed = False
    key.qualification_version += 1
    key.save(update_fields=["disputed", "qualification_version"])
    emit(product, key, "qualification_changed")
    advance_key_events(key)
    keyed_issues(key).filter(code__in=SOURCE_CONFLICTS, resolved_at__isnull=True).update(
        status="resolved",
        resolved_at=record.created_at,
        handler_id=record.actor_id,
        last_action=record,
    )


def result(record):
    return {
        "action_id": str(record.pk),
        "status": record.after_state,
        "event_version": record.after_version,
    }


def act(request, event_id, data=None, *, target_code=None):
    with request_transaction():
        actor(request.user.pk, "review_observation")
        initial = QualityIssue.objects.filter(pk=event_id).first()
        if initial is None:
            raise Denied("not_found")
        cert = None
        is_mapping = False
        if data is not None:
            data = command(
                data, ACTIONS, REASONS, extra={name: {"evidence_ref"} for name in ACTIONS}
            )
            action = data["action"]
            is_mapping = action == "correct_mapping"
            if action in {"correct_source", "correct_mapping"}:
                cert = decode_ref(data["evidence_ref"], initial, request.user, action)
                if is_mapping:
                    target_code = cert[5][1]
            elif data["evidence_ref"] is not None:
                raise Denied("invalid_request")
        product = product_lock(initial.product_id)
        key = lock_key(product, initial, target_code)
        if key is not None:
            list(keyed_issues(key).select_for_update().order_by("pk"))
        event = (
            QualityIssue.objects.select_for_update().filter(product=product, pk=event_id).first()
        )
        if event is None:
            raise Denied("not_found")
        auth = SessionCheck(request)
        user = user_permission(
            auth.user_id, "accounts.adjudicate_quality" if data is not None else None
        )
        grants = list(product.usegrant_set.filter(revoked_at__isnull=True))
        scope = checks(event, product, key, grants, auth.now)
        if data is None:
            response = event_result(event, product, key, scope)
            now = auth.finish(())
            checks(event, product, key, grants, now)
            if key:
                response.update(project_refs(event, product, key, user, grants, now, target_code))
            return response
        if cert:
            if key is None:
                raise Denied("invalid_request")
            checks(event, product, key, grants, auth.now, raw=is_mapping)
        # Store only a command fingerprint and non-source codes in the generic audit.
        parameter = fingerprint(cert) if cert else ""
        previous = replay(user.pk, data, "quality", event.pk, parameter=parameter)
        if previous:
            now = auth.finish(())
            checks(event, product, key, grants, now, raw=is_mapping)
            if is_mapping:
                review_retained(product, Scope.from_issue(cert[5][5]), RAW, now, grants)
            return result(previous)
        if data["expected_version"] != event.event_version or event.status == "resolved":
            raise Denied("precondition_conflict")
        before, version = event.status, event.event_version
        if action == "claim" and before != "open":
            raise Denied("precondition_conflict")
        if action in {"hold", "close"} and before == "in_review" and event.handler_id != user.pk:
            raise Denied("precondition_conflict")
        status = "resolved" if action == "close" else "in_review"
        affected = [project_scope(product, key, scope)]
        outcome = action
        target_scope = None
        if cert:
            current, candidates, edition = snapshot(product, key)
            if cert[2] != version or cert[4] != edition:
                raise Denied("precondition_conflict")
            prior_qualification = key.qualification_version
            resulting_revision = current
            if action == "correct_source":
                correction = correction_evidence(product, key, current, candidates)
                if (
                    event.code not in SOURCE_CONFLICTS
                    or correction is None
                    or correction.pk != cert[5]
                ):
                    raise Denied("mapping_evidence_mismatch")
                record = receipt(
                    user.pk,
                    data,
                    "quality",
                    event.pk,
                    before,
                    "resolved",
                    version,
                    version + 1,
                    parameter=parameter,
                )
                close_source(product, key, candidates, correction, record)
                status = "resolved"
            else:
                try:
                    candidate, selector, target_scope, rows, identity = mapping_selection(
                        product, key, current, grants, auth.now, cert[5][1]
                    )
                except Denied as exc:
                    if exc.code in {"identity_unresolved", "identity_window_crossing"}:
                        raise Denied("precondition_conflict") from None
                    raise
                if selector != cert[5]:
                    raise Denied("precondition_conflict")
                outcome = correct_mapping(
                    user.pk,
                    product.pk,
                    product.level,
                    key.pk,
                    current.pk,
                    key.watermark,
                    candidate,
                    data["reason_code"],
                )
                key.refresh_from_db(fields=["disputed", "qualification_version"])
                status = (
                    "resolved"
                    if outcome == "local_mapping_fix" and not key.disputed
                    else "in_review"
                )
                target = rows.objects.filter(**identity).first()
                if target is not None and target.pk != key.pk:
                    affected.append(project_scope(product, target, target_scope))
                if outcome == "mapping_conflict":
                    resulting_revision = target.revisions.filter(kind="mapping_conflict").latest(
                        "revision_no"
                    )
                elif outcome == "local_mapping_fix":
                    resulting_revision = target.revisions.get(is_current=True)
                record = receipt(
                    user.pk,
                    data,
                    "quality",
                    event.pk,
                    before,
                    status,
                    version,
                    version + 1,
                    parameter=parameter,
                )
            QualityDecision.objects.create(
                action=record,
                qualification_before=prior_qualification,
                qualification_after=key.qualification_version,
                **{
                    f"{product.level}_before": current,
                    f"{product.level}_after": resulting_revision,
                },
            )
        else:
            record = receipt(
                user.pk,
                data,
                "quality",
                event.pk,
                before,
                status,
                version,
                version + 1,
                parameter=parameter,
            )
        QualityIssue.objects.filter(pk=event.pk).update(
            status=status,
            resolved_at=record.created_at if status == "resolved" else None,
            handler_id=user.pk,
            last_action=record,
            event_version=version + 1,
        )
        now = auth.finish(())
        checks(event, product, key, grants, now, raw=is_mapping)
        if target_scope is not None:
            review_retained(product, target_scope, RAW, now, grants)
        return {**result(record), "affected": affected, "outcome": outcome}
