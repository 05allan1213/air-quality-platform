"""Collection control versions, authorization, and receipts within ledger transactions."""

from accounts.governance import command, receipt, replay
from accounts.research_session import SessionCheck
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import transaction
from observations.policy import Denied, actor

from .admission import LEDGER, bounds, lock_job, selected_use
from .models import CollectionControl, ControlEvent, Job

JOB_ACTIONS = {"pause", "resume", "resume_source", "resume_product"}
REASONS = {"operator_pause", "operator_resume", "operator_recovery", "incident_review"}


def parse(data, actions):
    if data == {"action": "pause"} and "pause" in actions:
        return dict(action="pause", request_id=None, reason_code="", expected_version=None)
    return command(data, actions, REASONS)


def lock_principals(request, users=()):
    ids = {request.user.pk, *users}
    list(get_user_model().objects.select_for_update().filter(pk__in=ids).order_by("pk"))
    groups = Group.objects.filter(user__pk__in=ids).values("pk")
    list(Group.objects.select_for_update().filter(pk__in=groups).order_by("pk"))
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


def state(kind, target):
    if kind == "job":
        return "active" if target.enabled else "paused"
    return "paused" if target.collection_pause else "active"


def projected(target, kind):
    details = dict(
        type=kind, id=target.pk, control_version=version(kind, target), state=state(kind, target)
    )
    if kind == "source":
        details["resume_after"] = target.collection_resume_after
    details["reason"] = target.pause_reason if kind == "job" else target.collection_pause
    return details


def review_job(job, now):
    if job.disposed:
        # Retain an operable identifier and a generic control disposition.
        raise Denied("job_disposed")
    at = now.replace(minute=0, second=0, microsecond=0)
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
    manager(request.user.pk)
    result = {
        "job": job.pk,
        "targets": {
            kind: projected(target, kind)
            for kind, target in (
                ("job", job),
                ("source", job.product.source),
                ("product", job.product),
            )
        },
    }
    review_job(job, session.finish(()))
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
