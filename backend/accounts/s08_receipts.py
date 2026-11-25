"""Resolve one committed self receipt without calling any command entry point."""

import re
from uuid import UUID

from collection import cnemc_jobs, control, s08_schedule, services
from collection.admission import LEDGER, bounds, lock_job, parse_hour, selected_use
from collection.models import ControlEvent, Run
from django.core import signing
from django.core.exceptions import ObjectDoesNotExist
from django.db import DatabaseError
from django.urls import path
from django.utils.crypto import salted_hmac
from django.views.decorators.cache import never_cache
from observations import catalog_governance, quality_governance
from observations.models import (
    CatalogChange,
    Product,
    QualityDecision,
    QualityIssue,
    Source,
    Station,
    UseGrant,
)
from observations.policy import RAW, Denied, Scope, actor, locked_catalog_product
from observations.s04_http import fingerprint
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .api import error
from .governance import command, replay
from .limits import limited
from .models import GovernanceAction
from .research_session import request_transaction


def found(value):
    if value is None:
        raise Denied("not_found")
    return value


def identifier(value):
    if type(value) is not int or not 0 < value < 2**63:
        raise Denied("not_found")
    return value


def uuid(value, *, canonical=True):
    if not isinstance(value, str) or not 1 <= len(value) <= 45:
        raise Denied("not_found")
    try:
        result = UUID(value)
    except ValueError:
        raise Denied("not_found") from None
    if canonical and str(result) != value:
        raise Denied("not_found")
    return result


def governance_result(record):
    if record.outcome != "applied":
        raise Denied("not_found")
    return {
        "state": record.after_state,
        "action": record.action,
        "recorded_at": record.created_at.isoformat(),
        "action_id": str(record.pk),
        "version": record.after_version,
    }


def owned_command(request, args, **identity):
    # Do not lock arbitrary guessed business objects before finding a self receipt.
    return found(
        GovernanceAction.objects.filter(
            actor_id=request.user.pk,
            request_id=args["request_id"],
            action=args["action"],
            reason_code=args["reason_code"],
            before_version=args["expected_version"],
            outcome="applied",
            **identity,
        ).first()
    )


def run_status(run):
    return {
        "run_id": str(run.pk),
        "window_id": run.window_id,
        "dispatch": run.dispatch,
        "status": run.status,
    }


def run_use(run, now):
    """Disclosure of one's submission is distinct from permission to fetch again."""
    job, window = run.job, run.window
    intent = services.intent(run)
    if (
        job.disposed
        or window.disposed
        or run.request_state == "disposed"
        or cnemc_jobs.endpoint_for(job, intent) != run.endpoint
    ):
        raise Denied("not_found")
    actor(run.actor_id, "run_collection")
    actor(run.executor_id, "run_collection")
    if intent == "review":
        actor(run.actor_id, "review_observation")
    selected_use(
        job,
        window.target_start,
        window.target_end,
        "retain_normalized",
        LEDGER,
        now,
        job.product.usegrant_set.filter(revoked_at=None),
    )


def manual_run(request, target, data):
    if (
        set(data) != {"job", "request_id", "reason", "target"}
        or identifier(data["job"]) != target
        or data["reason"] not in ("manual", "review", "backfill")
    ):
        raise Denied("not_found")
    when = parse_hour(data["target"])
    request_id = uuid(data["request_id"], canonical=False)
    run_id = found(
        Run.objects.filter(
            actor_id=request.user.pk,
            request_id=request_id,
            job_id=target,
            reason=data["reason"],
        )
        .values_list("pk", flat=True)
        .first()
    )
    run = services.locked(run_id)
    identity = (request.user.pk, request_id, target, data["reason"])
    if (run.actor_id, run.request_id, run.job_id, run.reason) != identity or (
        run.window.target_start,
        run.window.target_end,
    ) != bounds(run.job, when):
        raise Denied("not_found")
    session = control.lock_principals(request, {run.executor_id})
    run_use(run, session.finish(()))
    return {
        "state": "submitted",
        "action": run.reason,
        "recorded_at": run.created_at.isoformat(),
        **run_status(run),
    }


def control_record(record, job, *, run=None):
    return found(
        ControlEvent.objects.filter(
            receipt=record,
            job=job,
            actor_id=record.actor_id,
            action=record.action,
            occurred_at=record.created_at,
            run=run,
            legacy_protocol=False,
        ).first()
    )


def job_control(request, target, data):
    args = command(data, control.JOB_ACTIONS, control.REASONS)
    owned_command(request, args, origin=str(target))
    job = lock_job(target)
    session = control.lock_principals(request, {job.executor_id})
    kind, subject = control.target_for(job, args["action"])
    record = found(control.previous(job, args, kind, subject, session.user_id))
    control_record(record, job)
    now = session.finish(())
    control.manager(session.user_id)
    if args["action"] != "pause":
        actor(job.executor_id, "run_collection")
    control.review_job(
        job, now, target=record.created_at.replace(minute=0, second=0, microsecond=0)
    )
    return governance_result(record)


def redeliver(request, target, data):
    args = command(data, {"redeliver"}, control.REASONS)
    owned_command(request, args, target_kind="run", target=str(target))
    run = services.locked(target)
    session = control.lock_principals(request, {run.actor_id, run.executor_id})
    record = found(control.previous(run.job, args, "run", run, session.user_id))
    control_record(record, run.job, run=run)
    now = session.finish(())
    control.manager(session.user_id)
    run_use(run, now)
    return {**governance_result(record), **run_status(run)}


def schedule(request, target, data):
    creating = data.get("action") == "create_draft"
    args = s08_schedule.parse(
        data, {"create_draft"} if creating else {"propose", "activate", "discard"}
    )
    if creating and args["template"] != target:
        raise Denied("not_found")
    prior = owned_command(
        request, args, target_kind="job", origin=f"{'template' if creating else 'job'}:{target}"
    )
    template, basis, entrance, _ = s08_schedule.locate(
        template_id=target if creating else None, job_id=None if creating else target
    )
    origin, parameter = s08_schedule.command_identity(args, template, entrance)
    session = control.lock_principals(request, {basis.executor_id})
    s08_schedule.require_configure(session.user_id)
    event = found(
        ControlEvent.objects.select_related("job__city")
        .defer("schedule_response")
        .filter(
            receipt=prior,
            actor_id=session.user_id,
            action=args["action"],
            occurred_at=prior.created_at,
            job__schedule_template=template,
            job__product_id=basis.product_id,
            schedule_response__isnull=False,
            run__isnull=True,
            legacy_protocol=False,
        )
        .first()
    )
    job = event.job
    if (
        (creating and job.predecessor_id is not None)
        or (args["action"] == "propose" and job.predecessor_id != target)
        or (args["action"] in {"activate", "discard"} and job.pk != target)
    ):
        raise Denied("not_found")
    record = found(replay(session.user_id, args, "job", job.pk, parameter=parameter, origin=origin))
    now = s08_schedule.finish_configure(session)
    if entrance:
        s08_schedule.review(entrance, now, entrance.schedule_anchor_at)
    job.product = basis.product
    s08_schedule.review_command(args["action"], basis, job, now)
    return {**governance_result(record), "job": job.pk}


def submitted_proof(token, salt, length):
    # An expired preview cannot authorize writing, but can identify a committed
    # command. The caller still verifies its signature, subject, original version,
    # exact ledger fingerprint and all current-object permissions independently.
    if (
        not isinstance(token, str)
        or len(token) > 2048
        or not re.fullmatch(r"[A-Za-z0-9:_.-]+", token)
    ):
        raise Denied("not_found")
    try:
        proof = signing.loads(token, salt=salt)
    except signing.BadSignature:
        raise Denied("not_found") from None
    if not isinstance(proof, list) or len(proof) != length:
        raise Denied("not_found")
    return proof


def quality(request, target, data):
    rules = quality_governance
    args = command(
        data, rules.ACTIONS, rules.REASONS, extra={verb: {"evidence_ref"} for verb in rules.ACTIONS}
    )
    verb = args["action"]
    correcting = verb in {"correct_source", "correct_mapping"}
    mapping = verb == "correct_mapping"
    proof = None
    if correcting:
        proof = submitted_proof(args["evidence_ref"], rules.EVIDENCE, 6)
        if proof[:4] != [target, request.user.pk, args["expected_version"], verb]:
            raise Denied("not_found")
    elif args["evidence_ref"] is not None:
        raise Denied("not_found")
    owned_command(request, args, target_kind="quality", target=str(target))
    if mapping and (not isinstance(proof[5], list) or len(proof[5]) != 6):
        raise Denied("not_found")
    initial = found(QualityIssue.objects.filter(pk=target).first())
    product = rules.product_lock(initial.product_id)
    key = rules.lock_key(product, initial, proof[5][1] if mapping else None)
    event = found(
        QualityIssue.objects.select_for_update().filter(pk=target, product=product).first()
    )
    session = control.lock_principals(request)
    record = found(
        replay(
            session.user_id, args, "quality", target, parameter=fingerprint(proof) if proof else ""
        )
    )
    if correcting:
        if key is None:
            raise Denied("not_found")
        relation = "city" if event.city_key_id else "station"
        found(
            QualityDecision.objects.filter(
                action=record,
                **{
                    f"{relation}_before__key": key,
                    f"{relation}_after__key__product": product,
                },
            )
            .values_list("pk", flat=True)
            .first()
        )
    now = session.finish(())
    rules.user_permission(session.user_id, "accounts.adjudicate_quality")
    grants = list(product.usegrant_set.filter(revoked_at=None))
    rules.checks(event, product, key, grants, now, raw=mapping)
    if mapping:
        rules.review_retained(product, Scope.from_issue(proof[5][5]), RAW, now, grants)
    return governance_result(record)


def station_change(request, target, data):
    rules = catalog_governance
    args = command(data, rules.ACTIONS, rules.REASONS, extra=rules.FIELDS)
    proof = submitted_proof(args["evidence_ref"], rules.PROFILE, 6)
    if (
        proof[:3] != [request.user.pk, target, args["expected_version"]]
        or not isinstance(proof[3], list)
        or len(proof[3]) != 5
        or proof[3][1] != args["action"]
        or not isinstance(proof[5], list)
        or not 1 <= len(proof[5]) <= 2
    ):
        raise Denied("not_found")
    owned_command(request, args, target_kind="station", target=str(target))
    source_id = found(Station.objects.filter(pk=target).values_list("source_id", flat=True).first())
    found(Source.objects.select_for_update().filter(pk=source_id).first())
    product = found(
        Product.objects.select_for_update()
        .filter(pk=proof[3][0], source_id=source_id, level="station")
        .first()
    )
    found(Station.objects.select_for_update().filter(pk=target, source_id=source_id).first())
    session = control.lock_principals(request)
    parameter = salted_hmac(rules.PROFILE, fingerprint(proof), algorithm="sha256").hexdigest()
    record = found(replay(session.user_id, args, "station", target, parameter=parameter))
    if record.after_state == "applied":
        found(
            CatalogChange.objects.filter(action=record, identifier__station_id=target)
            .filter(change__product=product)
            .values_list("pk", flat=True)
            .first()
        )
    # A blocked edit changes no directory row. Its optional conflict issues can
    # later be adjudicated, so their mutable last_action cannot identify the command.
    elif record.after_state != "mapping_review_required":
        raise Denied("not_found")
    now = session.finish(())
    rules.user_permission(session.user_id, "accounts.manage_catalog")
    rules.allowed(product, [Scope.from_issue(span) for span in proof[5]], now)
    return governance_result(record)


def grant_withdraw(request, target, data):
    args = command(data, {"withdraw"}, {"terms_ended", "retention_withdrawn", "rights_review"})
    owned_command(request, args, target_kind="use_grant", target=str(target))
    product_id = found(
        UseGrant.objects.filter(pk=target).values_list("product_id", flat=True).first()
    )
    product, _ = locked_catalog_product(product_id)
    found(
        UseGrant.objects.filter(
            pk=target,
            product=product,
            revoked_at__isnull=False,
            revoked_by_id=request.user.pk,
            revoke_reason=args["reason_code"],
        ).first()
    )
    session = control.lock_principals(request)
    record = found(replay(session.user_id, args, "use_grant", target))
    session.finish(())
    actor(session.user_id, "manage_grants")
    return governance_result(record)


LOOKUPS = {
    "manual_run": manual_run,
    "job_control": job_control,
    "redeliver": redeliver,
    "schedule": schedule,
    "quality": quality,
    "station_change": station_change,
    "grant_withdraw": grant_withdraw,
}


@never_cache
@api_view(["POST"])
def lookup(request):
    if limited("collection", request.user.pk):
        return error("rate_limited", 429)
    envelope = request.data
    if (
        request.query_params
        or not isinstance(envelope, dict)
        or set(envelope) != {"family", "target", "command"}
        or not isinstance(envelope["family"], str)
        or envelope["family"] not in LOOKUPS
        or not isinstance(envelope["command"], dict)
    ):
        return error("invalid_request", 400)
    family = envelope["family"]
    try:
        target = (uuid if family == "redeliver" else identifier)(envelope["target"])
        with request_transaction():
            result = LOOKUPS[family](request, target, envelope["command"])
    except Denied as exc:
        if exc.code == "not_authenticated":
            return error("not_authenticated", 403)
        if exc.code == "database_unavailable":
            return error("database_unavailable", 503)
        return error("not_found", 404)
    except ObjectDoesNotExist:
        return error("not_found", 404)
    except DatabaseError:
        return error("database_unavailable", 503)
    return Response(result)


urlpatterns = [path("admin/command-receipts/lookup/", lookup)]
