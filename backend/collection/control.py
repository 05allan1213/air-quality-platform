"""Collection control versions, authorization, and receipts within ledger transactions."""

from accounts.governance import command, receipt, replay
from accounts.research_session import SessionCheck, lock_users_and_groups
from django.db import transaction
from observations.policy import Denied, actor, clock

from .admission import LEDGER, bounds, lock_job, selected_use
from .models import CollectionControl, ControlEvent, Job
from .schedule_segments import handoff_pause_pending

JOB_ACTIONS = {"pause", "resume", "resume_source", "resume_product"}
REASONS = {"operator_pause", "operator_resume", "operator_recovery", "incident_review"}


ACTION_REASONS = {
    "pause": "operator_pause",
    "resume": "operator_resume",
    "resume_source": "operator_recovery",
    "resume_product": "operator_recovery",
    "redeliver": "operator_recovery",
}


def check_new_reason(data):
    if data["request_id"] is not None and data["reason_code"] != ACTION_REASONS[data["action"]]:
        raise Denied("invalid_request")


def parse(data, actions):
    if data == {"action": "pause"} and "pause" in actions:
        return dict(action="pause", request_id=None, reason_code="", expected_version=None)
    return command(data, actions, REASONS)


def lock_principals(request, users=()):
    lock_users_and_groups({request.user.pk, *users})
    return SessionCheck(request)


def version(kind, target):
    if kind in {"job", "run"}:
        return target.control_version
    return (
        CollectionControl.objects.filter(**{kind: target}).values_list("version", flat=True).first()
        or 0
    )


def bump_shared(kind, target):
    # The caller already owns the defining Source or Product lock.
    control, _ = CollectionControl.objects.get_or_create(**{kind: target})
    control.version += 1
    control.save(update_fields=["version"])
    return control.version


def event(
    job,
    data,
    kind,
    target,
    before,
    after,
    before_version,
    after_version,
    *,
    operator_id=None,
    run=None,
):
    record = receipt(
        operator_id,
        data,
        kind,
        target.pk,
        before,
        after,
        before_version,
        after_version,
        origin=str(job.pk),
    )
    ControlEvent.objects.create(
        job=job,
        actor_id=operator_id,
        action=data["action"],
        occurred_at=record.created_at,
        run=run,
        receipt=record,
        legacy_protocol=data["action"] == "pause" and data["request_id"] is None,
        response_enabled=job.enabled if operator_id and kind != "run" else None,
        response_reason=job.pause_reason if operator_id and kind != "run" else "",
    )
    return record


def automatic(job, kind, target, before, after, before_version, after_version, reason, *, run=None):
    return event(
        job,
        dict(action="auto_pause", request_id=None, reason_code=reason),
        kind,
        target,
        before,
        after,
        before_version,
        after_version,
        run=run,
    )


def target_for(job, action):
    if action == "resume_source":
        return "source", job.product.source
    if action == "resume_product":
        return "product", job.product
    return "job", job


def state(kind, target, *, now=None):
    if kind == "job":
        if not target.enabled or (
            target.predecessor_id
            and not target.handoff_checked
            and handoff_pause_pending(target, now if now is not None else clock())
        ):
            return "paused"
        return "active"
    return "paused" if target.collection_pause else "active"


def projected(target, kind, now):
    inherited = kind == "job" and handoff_pause_pending(target, now)
    details = dict(
        type=kind,
        id=target.pk,
        control_version=version(kind, target) + int(inherited),
        state=("active" if target.enabled and not inherited else "paused")
        if kind == "job"
        else state(kind, target),
    )
    if kind == "source":
        details["resume_after"] = target.collection_resume_after
    details["reason"] = (
        ("predecessor_paused" if inherited else target.pause_reason)
        if kind == "job"
        else target.collection_pause
    )
    return details


def review_job(job, now, *, target=None):
    if job.disposed:
        # Retain an operable identifier and a generic control disposition.
        raise Denied("job_disposed")
    at = target or now.replace(minute=0, second=0, microsecond=0)
    start, end = bounds(job, at)
    rows = list(job.product.usegrant_set.filter(revoked_at=None))
    for operation in ("retain_normalized", "review"):
        selected_use(job, start, end, operation, LEDGER, now, rows)


def manager(user_id):
    return actor(user_id, "manage_collection")


def previous(job, data, kind, target, user_id):
    if data["request_id"] is None:
        return None
    return replay(user_id, data, kind, target.pk, origin=str(job.pk))


def job_result(job, record=None):
    if record is not None:
        original = ControlEvent.objects.get(receipt=record)
        return dict(job=job.pk, enabled=original.response_enabled, reason=original.response_reason)
    return dict(job=job.pk, enabled=job.enabled, reason=job.pause_reason)


@transaction.atomic
def job_diagnostic(request, job_id):
    job = lock_job(job_id)
    session = lock_principals(request)
    now = session.finish(())
    manager(request.user.pk)
    review_job(job, now)
    result = {
        "job": job.pk,
        "targets": {
            kind: projected(target, kind, now)
            for kind, target in (
                ("job", job),
                ("source", job.product.source),
                ("product", job.product),
            )
        },
    }
    return result


def dispose_jobs(ids, now):
    for job in Job.objects.select_for_update().filter(pk__in=ids, disposed=False).order_by("pk"):
        before, v = state("job", job), job.control_version
        Job.objects.filter(pk=job.pk).update(
            disposed=True,
            enabled=False,
            next_due_at=None,
            expected_codes=None,
            selection={},
            standard={},
            backfill_policy={},
            backfill_cursor=None,
            roster_basis="",
            pause_reason="permission_lost",
            changed_at=now,
        )
        job.refresh_from_db(fields=["control_version"])
        automatic(job, "job", job, before, "disposed", v, job.control_version, "permission_lost")
