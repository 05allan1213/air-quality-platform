"""Find bounded, eligible national station gaps in the approved operating calendar."""

from datetime import datetime

from accounts.research_session import lock_users_and_groups
from django.db import transaction
from observations.policy import Denied, clock

from . import cnemc_jobs, schedule_segments
from .admission import HOUR, lock_job
from .models import WorkWindow


def missing(window):
    if (
        window.disposed
        or window.owner
        or window.runs.filter(status__in=("pending", "running")).exists()
    ):
        return False
    if (
        window.runs.filter(endpoint="A10")
        .exclude(request_state="unsent", error_code="backfill_unavailable")
        .exists()
    ):
        return False
    if window.expected is None:
        return window.runs.filter(
            endpoint="A07",
            status__in=("expired", "failed"),
            error_code__in=("timeout", "upstream_transient", "database_error", "lease_expired"),
        ).exists()
    # A reported NA or disputed candidate is received. It is not a missing publication.
    from django.db.models import Q

    received = window.items.filter(
        Q(accepted=True, receipts__isnull=False) | Q(status="rejected")
    ).values_list("item_key", flat=True)
    return bool(set(window.expected).difference(received))


@transaction.atomic
def scan_gaps(job_id):
    from .services import register

    job = lock_job(job_id)
    now = clock()
    lock_users_and_groups({job.executor_id})
    schedule_segments.settle_handoff(job, now)
    phase = job.schedule_anchor_at or job.next_due_at
    if (
        not (job.enabled and phase and job.backfill_policy)
        or job.pause_reason
        or job.product.collection_pause
        or job.product.source.collection_pause
    ):
        return 0
    policy = job.backfill_policy
    stamp = cnemc_jobs.anchor(job, now)
    if stamp is None:
        return 0
    earliest = max(
        datetime.fromisoformat(policy["operating_from"]), stamp - HOUR * policy["lookback_hours"]
    )
    if job.effective_from:
        if now < job.effective_from:
            return 0
        earliest = max(earliest, job.effective_from)
    if job.effective_until:
        stamp = min(stamp, job.effective_until - HOUR)
    if stamp < earliest:
        return 0
    target = job.backfill_cursor
    if target is None or not earliest <= target <= stamp:
        target = earliest
    page_end = min(stamp, target + HOUR * (policy["max_windows_per_scan"] - 1))
    windows = {
        w.target_start: w
        for w in WorkWindow.objects.filter(
            product=job.product,
            scope_key="national",
            profile=job.profile,
            target_start__gte=target,
            target_start__lte=page_end,
        )
    }
    # Calendar hours, not the number of rows in a national response, define missed slots.
    schedule = schedule_segments.hour(phase)
    started = 0
    while target <= page_end and started < policy["max_requests_per_scan"]:
        window = windows.get(target)
        on_schedule = (schedule - target).total_seconds() % job.cadence_seconds == 0
        if (window and missing(window)) or (not window and on_schedule and job.expected_codes):
            try:
                register(job, job.executor_id, target, "backfill")
            except Denied:
                pass  # Revisit after one bounded pass, under the then current grants.
            else:
                started += 1
        target += HOUR
    # One transaction owns both this checkpoint and the new Run(s). Even a one-hour
    # page resumes after a denied slot following a scanner/Worker restart.
    job.backfill_cursor = target if target <= stamp else None
    job.save(update_fields=["backfill_cursor"])
    return started
