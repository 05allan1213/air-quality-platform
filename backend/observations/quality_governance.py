"""Adjudication of the complete locked observation ledger using registered evidence."""

import re
from urllib.parse import urlencode

from accounts.governance import command, receipt, replay
from accounts.models import GovernanceAction
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
from .s04_http import fingerprint, params

PAGE = 50
EVIDENCE = "observations.quality.evidence.v1"
ACTION_REASONS = {
    "claim": "claim_review",
    "hold": "retain_isolation",
    "close": "review_complete",
    "correct_source": "verified_correction",
    "correct_mapping": "registered_mapping",
}
ACTIONS = set(ACTION_REASONS)
REASONS = set(ACTION_REASONS.values())
CORRECTIONS = {"correct_source", "correct_mapping"}


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
        raise Denied("mapping_evidence_mismatch")
    try:
        cert = signing.loads(token, salt=EVIDENCE, max_age=1800)
    except signing.BadSignature:
        raise Denied("mapping_evidence_mismatch") from None
    if cert[:2] != [event.pk, user.pk] or cert[3] != kind:
        raise Denied("mapping_evidence_mismatch")
    return cert


def proof_parameter(token, cert, previous):
    if token is None:
        return ""
    # Older rows fingerprinted the verified claims. New rows distinguish the
    # exact token from another preview of those same claims signed later.
    if previous and previous.parameter and not previous.parameter.startswith("v2:"):
        return fingerprint(cert)
    return "v2:" + fingerprint(token)


def mapping_selection(product, key, current, grants, now, target_code):
    candidate = mapping_candidate(product, key, current, now, code=target_code)
    ctx = WriteContext(product, current.actor_id, None, grants, set())
    rows, identity, _ = locate(ctx, candidate, for_write=False)
    target = rows.objects.filter(**identity).first()
    if target != key and active_candidates(key).exclude(kind="mapping_conflict").exists():
        raise Denied("mapping_evidence_mismatch")
    entity = identity[tables(product.level)[2]]
    geography = entity.geography if isinstance(key, CityObservation) else entity.city.geography
    scope = Scope(
        geography,
        candidate.data.object_code,
        key.indicator,
        key.source_local_date,
        key_scope(key).end,
    )
    mapping_rights(product, scope, grants, now)
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
    refs = {}
    if event.status != "resolved" and user.has_perm("accounts.adjudicate_quality"):
        correction = correction_evidence(product, key, current, candidates)
        if correction and event.code in SOURCE_CONFLICTS:
            refs["correct_source"] = sign_ref(event, user, "correct_source", edition, correction.pk)
        try:
            checks(event, product, key, grants, now, raw=True)
            _, selector, _, _, _ = mapping_selection(
                product, key, current, grants, now, target_code
            )
        except Denied as exc:
            if exc.code == "database_unavailable":
                raise
        else:
            refs["correct_mapping"] = sign_ref(event, user, "correct_mapping", edition, selector)
    return {
        "current_revision": current.pk if current else None,
        "watermark": key.watermark,
        "qualification_version": key.qualification_version,
        "disputed": key.disputed,
        "candidate_revisions": [c.pk for c in candidates],
        "evidence": refs,
    }


def basic_actions(event, user):
    if event.status == "open":
        return ("claim", "hold", "close")
    if event.status == "in_review" and event.handler_id == user.pk:
        return ("hold", "close")
    return ()


def evidence_links(event, product, key):
    # checks() validated this event's product, key, batch and review/retention basis.
    # A keyed event proves batch readability only if this batch contains that key.
    domain = {"product": product.pk, "scope": "synthetic" if product.synthetic else "formal"}
    route = "/api/v1/admin/observation-evidence/?"
    links = {}
    if key:
        selector = f"{product.level}_key"
        links["key"] = route + urlencode(domain | {selector: key.pk})
    if event.batch_id and (
        key is None
        or event.batch.receipts.filter(**{f"{product.level}_revision__key": key}).exists()
    ):
        links["batch"] = route + urlencode(domain | {"batch": event.batch_id})
    return links


def visible_scope(event, product, key, grants, now):
    try:
        return checks(event, product, key, grants, now)
    except Denied as exc:
        if exc.code == "use_denied":
            raise Denied("not_found") from None
        raise


def mapping_rights(product, scope, grants, now):
    try:
        review_retained(product, scope, RAW, now, grants)
    except Denied as exc:
        if exc.code == "use_denied":
            raise Denied("mapping_evidence_mismatch") from None
        raise


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


def receipt_impact(record, product, event, key, scope, after, proof, grants, now):
    """Project only retained action-linked evidence; current candidates prove no past outcome."""
    if after is None:
        if event.last_action_id != record.pk or (not key and not event.scope):
            return {"state": "unavailable"}
        return {
            "state": "available",
            "affected": [project_scope(product, key, scope)],
            "outcome": record.action,
        }
    affected = [project_scope(product, key, scope)]
    impact = {"state": "available", "affected": affected}
    if record.action == "correct_source":
        return impact | {"outcome": "correct_source"}
    # Only the recorded resulting revision links a correction to another key. A
    # signed candidate alone is intent and does not establish an affected target.
    selector = proof[5]
    target = after.key
    target_scope = key_scope(target)
    entity = target.city_id if isinstance(target, CityObservation) else target.site_epoch_id
    if entity != selector[2] or target_scope.serialize() != selector[5]:
        return impact
    try:
        review_retained(product, target_scope, RAW, now, grants)
    except Denied as exc:
        if exc.code == "use_denied":
            return impact
        raise
    if target.pk != key.pk:
        affected.append(project_scope(product, target, target_scope))
    if after.pk == selector[0]:
        impact["outcome"] = "mapping_review_required"
    elif after.kind in {"mapping_conflict", "local_mapping_fix"}:
        impact["outcome"] = after.kind
    return impact


def result(record):
    return {
        "action_id": str(record.pk),
        "status": record.after_state,
        "event_version": record.after_version,
    }


def act(request, event_id, *, write):
    with request_transaction():
        entry = actor(request.user.pk, "review_observation")
        if write and not entry.has_perm("accounts.adjudicate_quality"):
            raise Denied("permission_denied")
        values = params(request.query_params, set() if write else {"mapping_target"})
        target_code = values.get("mapping_target")
        if not 0 < event_id < 2**63 or (
            target_code is not None
            and (not target_code.isprintable() or not 0 < len(target_code) <= 100)
        ):
            raise Denied("invalid_request")
        # Resolve transport errors independently of whether the target exists.
        data = request.data if write else None
        initial = QualityIssue.objects.filter(pk=event_id).first()
        if initial is None:
            raise Denied("not_found")
        # A valid signed mapping can name another station. Parse it for ordered locking
        # only. Do not disclose errors until the caller's object access has been checked.
        cert = None
        evidence_error = None
        if isinstance(data, dict) and data.get("action") == "correct_mapping":
            try:
                cert = decode_ref(data.get("evidence_ref"), initial, entry, "correct_mapping")
                target_code = cert[5][1]
            except Denied as exc:
                evidence_error = exc
        try:
            product = product_lock(initial.product_id)
            key = lock_key(product, initial, target_code)
        except Denied as exc:
            if exc.code == "use_denied":
                raise Denied("not_found") from None
            raise
        if key is not None:
            list(keyed_issues(key).select_for_update().order_by("pk"))
        event = (
            QualityIssue.objects.select_for_update().filter(product=product, pk=event_id).first()
        )
        if event is None:
            raise Denied("not_found")
        auth = SessionCheck(request)
        user = user_permission(auth.user_id, "accounts.adjudicate_quality" if write else None)
        grants = list(product.usegrant_set.filter(revoked_at__isnull=True))
        scope = visible_scope(event, product, key, grants, auth.now)
        if not write:
            response = event_result(event, product, key, scope)
            links = evidence_links(event, product, key)
            if links:
                response["evidence_links"] = links
            now = auth.finish(())
            visible_scope(event, product, key, grants, now)
            if key:
                response.update(project_refs(event, product, key, user, grants, now, target_code))
            verbs = (*basic_actions(event, user), *response.get("evidence", {}))
            response["allowed_actions"] = [
                {
                    "action": verb,
                    "reason_codes": [ACTION_REASONS[verb]],
                    "evidence_required": verb in CORRECTIONS,
                }
                for verb in verbs
                if user.has_perm("accounts.adjudicate_quality")
            ]
            return response
        data = command(data, ACTIONS, REASONS, extra={name: {"evidence_ref"} for name in ACTIONS})
        action = data["action"]
        is_mapping = action == "correct_mapping"
        if action in CORRECTIONS:
            if evidence_error:
                raise evidence_error
            cert = decode_ref(data["evidence_ref"], event, user, action)
            if key is None:
                raise Denied("mapping_evidence_mismatch")
            if is_mapping:
                mapping_rights(product, scope, grants, auth.now)
        elif data["evidence_ref"] is not None:
            raise Denied("invalid_request")
        # Store only a command fingerprint and non-source codes in the generic audit.
        prior = GovernanceAction.objects.filter(actor=user, request_id=data["request_id"]).first()
        parameter = proof_parameter(data["evidence_ref"], cert, prior)
        previous = replay(user.pk, data, "quality", event.pk, parameter=parameter)
        if previous:
            now = auth.finish(())
            visible_scope(event, product, key, grants, now)
            if is_mapping:
                mapping_rights(product, scope, grants, now)
                mapping_rights(product, Scope.from_issue(cert[5][5]), grants, now)
            return result(previous)
        if data["reason_code"] != ACTION_REASONS[action]:
            raise Denied("invalid_request")
        if data["expected_version"] != event.event_version or event.status == "resolved":
            raise Denied("precondition_conflict")
        before, version = event.status, event.event_version
        if action not in CORRECTIONS and action not in basic_actions(event, user):
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
        visible_scope(event, product, key, grants, now)
        if target_scope is not None:
            mapping_rights(product, scope, grants, now)
            mapping_rights(product, target_scope, grants, now)
        return {**result(record), "affected": affected, "outcome": outcome}
