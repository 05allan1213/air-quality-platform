"""Shared slot ownership for collection admission, Beat, and gap recovery."""

from datetime import timedelta

from django.db.models import Q
from observations.policy import Denied

from .models import ControlEvent, Job, WorkWindow

HOUR = timedelta(hours=1)


def hour(stamp):
    return stamp.replace(minute=0, second=0, microsecond=0)


def scope_key(job):
    return f"city:{job.city_id}" if job.city_id else "national"


def same_scope(job):
    return Job.objects.filter(product_id=job.product_id, city_id=job.city_id, profile=job.profile)


def check_owner(job, target, now):
    if job.plan_state == "legacy":
        if same_scope(job).filter(plan_state="active", effective_from__lte=target).exists():
            raise Denied("precondition_conflict")
        return
    if (
        job.plan_state != "active"
        or target < job.effective_from
        or now < job.effective_from
        or job.effective_until is not None
        and target >= job.effective_until
    ):
        raise Denied("precondition_conflict")


def phase_at_or_before(job, limit):
    phase = job.schedule_anchor_at
    cadence = timedelta(seconds=job.cadence_seconds)
    return phase + ((limit - phase) // cadence) * cadence


def overlapping_clocks(job, *, exclude=()):
    return (
        same_scope(job)
        .exclude(pk__in=exclude)
        .filter(Q(plan_state="active") | Q(plan_state="legacy", next_due_at__isnull=False))
    )


def check_no_overlap(job, boundary, *, replace=None):
    qs = same_scope(job).exclude(pk=job.pk)
    if replace:
        qs = qs.exclude(pk=replace.pk)
    if qs.filter(
        Q(plan_state="legacy", enabled=True, next_due_at__isnull=False)
        | Q(plan_state="active", effective_until__isnull=True)
        | Q(plan_state="active", effective_until__gt=boundary)
    ).exists():
        raise Denied("precondition_conflict")


def future_ledger(job, boundary):
    return WorkWindow.objects.filter(
        product_id=job.product_id,
        scope_key=scope_key(job),
        profile=job.profile,
        target_end__gte=boundary + HOUR,
    )


def inherited_at(job, when):
    """Reconstruct operator state at each boundary, even after a multi-segment outage."""
    while job is not None:
        events = ControlEvent.objects.filter(
            job=job, occurred_at__lte=when, action__in=("pause", "resume")
        )
        last = events.order_by("-occurred_at", "-pk").values_list("action", flat=True).first()
        if last:
            return last == "pause"
        if job.predecessor_id is None:
            return False
        when = job.effective_from
        job = job.predecessor
    return False


def handoff_due(job, now):
    return (
        job.plan_state == "active"
        and not job.handoff_checked
        and job.predecessor_id is not None
        and job.effective_from <= now
    )


def handoff_pause_pending(job, now):
    return (
        job.enabled and handoff_due(job, now) and inherited_at(job.predecessor, job.effective_from)
    )


def effective_control_version(job, now):
    # Settlement changes enabled/pause_reason in one update. Reads can expose the
    # version the control trigger will assign before a writer records that change.
    return job.control_version + int(handoff_pause_pending(job, now))


def settle_handoff(job, now):
    # All callers hold the defining Source lock, including mutations of the predecessor.
    if not handoff_due(job, now):
        return
    from . import control

    inherited = handoff_pause_pending(job, now)
    job.handoff_checked = True
    if not inherited:
        job.save(update_fields=["handoff_checked"])
        return
    version = job.control_version
    job.enabled = False
    job.pause_reason = "predecessor_paused"
    job.save(update_fields=["handoff_checked", "enabled", "pause_reason"])
    job.refresh_from_db(fields=["control_version"])
    control.automatic(
        job, "job", job, "active", "paused", version, job.control_version, job.pause_reason
    )
