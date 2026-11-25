"""Register triggers, fence worker ownership and reserve at most one access per Run."""

from datetime import timedelta
from random import SystemRandom
from uuid import UUID, uuid4

from django.conf import settings
from django.db import DatabaseError, transaction
from django.db.models import BooleanField, Q
from django.db.models.expressions import RawSQL
from django_celery_beat.models import IntervalSchedule, PeriodicTask
from kombu.exceptions import OperationalError
from observations.policy import Denied, actor, applicable, clock
from observations.quality_events import check_detail, record_run_issue

from . import cnemc_jobs, control, schedule_segments
from .admission import (
    HOUR,
    LEDGER,
    BudgetExhausted,
    admission,
    bounds,
    budget,
    can_fetch,
    data_scope,
    expected,
    lock_job,
    parse_hour,
    schedule_fixture_caps,
    target_code,
)
from .models import ControlEvent, Job, Run, WorkWindow

LEASE = timedelta(seconds=60)
MAX_EXECUTION = timedelta(minutes=10)
MAX_ATTEMPTS = 3
MAX_DELIVERIES = 8
WIRE_QUALITY_CODES = frozenset(
    {
        "invalid_body",
        "invalid_shape",
        "invalid_row",
        "duplicate_json_key",
        "response_too_large",
        "response_too_deep",
        "response_too_many_fields",
        "response_field_too_long",
        "response_too_many_rows",
        "response_too_many_hours",
        "invalid_identity",
        "identity_unresolved",
        "invalid_identifier",
        "invalid_city_code",
        "invalid_time",
        "invalid_source_offset",
        "source_time_conflict",
        "city_mismatch",
        "station_mismatch",
        "identity_window_crossing",
        "exact_target_mismatch",
        "too_many_duplicates",
    }
)
PAGE = 64


def wake_scanner():
    interval, _ = IntervalSchedule.objects.get_or_create(every=5, period=IntervalSchedule.SECONDS)
    PeriodicTask.objects.update_or_create(
        name="s03.reconcile",
        defaults=dict(
            interval=interval,
            task="collection.reconcile_due",
            queue=settings.CELERY_TASK_ROUTES["collection.reconcile_due"]["queue"],
            enabled=True,
        ),
    )


def enqueue(run):
    transaction.on_commit(lambda: publish(run.pk))


def intent(run):
    # Only automatic retries inherit the trigger. An independent review may also
    # point to a former acquisition for lineage, without inheriting its intent.
    seen = set()
    while run.reason == "retry":
        if not run.predecessor_id or run.pk in seen:
            raise Denied("invalid_retry_lineage")
        seen.add(run.pk)
        # A repaired legacy chain can also contain historical unsent quota denials.
        run = run.predecessor
    return run.reason


def allows_closed(window, reason):
    if reason == "review":
        return True
    if (
        reason != "backfill"
        or window.profile not in ("f-exact-v1", cnemc_jobs.PROFILE)
        or window.expected is None
    ):
        return False
    accepted = set(
        window.items.filter(accepted=True, receipts__isnull=False).values_list(
            "item_key", flat=True
        )
    )
    return bool(set(window.expected) - accepted)


def register(job, actor_id, target, reason, *, request_id=None, scheduled_for=None):
    start, end = bounds(job, target)
    endpoint = cnemc_jobs.endpoint_for(job, reason)
    admission(job, actor_id, start, end, endpoint=endpoint)
    if reason == "review":
        actor(actor_id, "review_observation")
    now = clock()
    if schedule_segments.handoff_pause_pending(job, now):
        raise Denied("collection_paused")
    key = schedule_segments.scope_key(job)
    if request_id:
        prior = (
            Run.objects.filter(actor_id=actor_id, request_id=request_id)
            .select_related("window")
            .first()
        )
        if prior:
            if (prior.job_id, prior.reason, prior.window.target_end) != (job.pk, reason, end):
                raise Denied("request_conflict")
            return prior
    schedule_segments.check_owner(job, end - HOUR, now)
    # Even with E unknown, local identity and processing selectors must be independently proven.
    if (
        endpoint
        and job.selection.get("mode") == "include"
        and job.expected_codes != job.selection["objects"]
    ):
        cnemc_jobs.scopes(job, start, end)
    cells = expected(job, start, end)
    window, created = WorkWindow.objects.select_for_update().get_or_create(
        product=job.product,
        scope_key=key,
        target_start=start,
        target_end=end,
        profile=job.profile,
        defaults={
            "expected": cells,
            "roster_basis": job.roster_basis,
            "selection": job.selection,
            "standard": job.standard,
        },
    )
    if window.disposed:
        raise Denied("window_disposed")
    if not created and (
        window.expected != cells
        or window.roster_basis != job.roster_basis
        or window.selection != job.selection
        or window.standard != job.standard
    ):
        raise Denied("window_contract_conflict")
    if scheduled_for:
        prior = Run.objects.filter(job=job, scheduled_for=scheduled_for).first()
        if prior:
            return prior
    previous = window.runs.filter(reserved_at__isnull=False).order_by("-reserved_at").first()
    if endpoint == "A10":
        from .recovery import missing

        if (
            window.runs.filter(endpoint="A10")
            .exclude(request_state="unsent", error_code="backfill_unavailable")
            .exists()
        ):
            raise Denied("backfill_already_attempted")
        if can_fetch(job, end, clock(), endpoint) and not missing(window):
            raise Denied("no_eligible_gap")
    run = Run.objects.create(
        endpoint=endpoint,
        validation_grant_id=job.validation_grant_id,
        window=window,
        job=job,
        actor_id=actor_id,
        executor_id=job.executor_id,
        reason=reason,
        predecessor=previous,
        request_id=request_id,
        scheduled_for=scheduled_for,
        dispatch_due=clock(),
    )
    if not can_fetch(job, end, clock(), endpoint):
        terminal(run, "failed", "backfill_unavailable")
    elif window.closed_at and not allows_closed(window, reason):
        terminal(run, "coalesced")
    else:
        wake_scanner()
        enqueue(run)
    return run


@transaction.atomic
def submit(actor_id, data, *, request=None):
    if (
        not isinstance(data, dict)
        or set(data) != {"job", "request_id", "reason", "target"}
        or type(data["job"]) is not int
        or not 0 < data["job"] < 2**63
        or data["reason"] not in ("manual", "review", "backfill")
    ):
        raise Denied("invalid_request")
    try:
        request_id = UUID(str(data["request_id"]))
    except (ValueError, TypeError):
        raise Denied("invalid_request") from None
    job = lock_job(data["job"])
    session = control.lock_principals(request, {job.executor_id}) if request else None
    target = parse_hour(data["target"])
    try:
        run = register(job, actor_id, target, data["reason"], request_id=request_id)
        if session:
            admission(job, actor_id, *bounds(job, target), endpoint=run.endpoint)
            session.finish(())
    except Denied as exc:
        # Pausing is not a distinct HTTP submission outcome; keep the domain code
        # for the scanner/Worker and the existing use_denied response for callers.
        if session and exc.code == "collection_paused":
            raise Denied() from None
        raise
    return run


def terminal(run, status, code=""):
    run.status = status
    run.error_code = code
    run.finished_at = clock()
    run.dispatch_due = None
    run.save(update_fields=["status", "error_code", "finished_at", "dispatch_due"])


def locked(run_id):
    pair = Run.objects.filter(pk=run_id).values_list("job_id", "window_id").first()
    if not pair:
        raise Denied("unknown_run")
    job = lock_job(pair[0])
    window = WorkWindow.objects.select_for_update().get(pk=pair[1])
    run = Run.objects.select_for_update().get(pk=run_id)
    if (
        window.product_id != job.product_id
        or run.executor_id != job.executor_id
        or window.profile != job.profile
        or not (window.disposed or job.disposed)
        and (window.selection != job.selection or window.standard != job.standard)
        or run.validation_grant_id != job.validation_grant_id
        or window.scope_key != (f"city:{job.city_id}" if job.city_id else "national")
    ):
        raise Denied("window_contract_conflict")
    run.job = job
    run.window = window
    return run


def authorized(run):
    if run.window.disposed:
        raise Denied("window_disposed")
    if schedule_segments.handoff_pause_pending(run.job, clock()):
        raise Denied("collection_paused")
    if intent(run) == "review":
        actor(run.actor_id, "review_observation")
    if run.endpoint != cnemc_jobs.endpoint_for(run.job, intent(run)):
        raise Denied("window_contract_conflict")
    caps = admission(
        run.job, run.actor_id, run.window.target_start, run.window.target_end, endpoint=run.endpoint
    )
    run.rights_boundary = caps[2]
    return caps


def publish(run_id):
    from .tasks import execute_run

    with transaction.atomic():
        run = locked(run_id)
        if (
            run.status != "pending"
            or run.request_state != "unsent"
            or run.next_retry_at is not None
            or run.deliveries >= MAX_DELIVERIES
            or run.dispatch not in {"pending", "unconfirmed", "sent"}
            or run.dispatch_due is None
            or run.dispatch_due > clock()
        ):
            return
        try:
            authorized(run)
        except Denied as exc:
            terminal(run, "paused", exc.code)
            return
        run.deliveries += 1
        run.dispatch = "unconfirmed"
        run.dispatch_due = clock() + timedelta(seconds=min(60, 2**run.deliveries))
        run.save(update_fields=["deliveries", "dispatch", "dispatch_due"])
        delivery = run.deliveries
    try:
        execute_run.apply_async(args=[str(run_id)], task_id=str(run_id), retry=False)
    except (OperationalError, OSError):
        return
    Run.objects.filter(pk=run_id, status="pending", deliveries=delivery).update(dispatch="sent")


@transaction.atomic
def claim(run_id):
    run = locked(run_id)
    if run.status != "pending" or run.request_state != "unsent" or run.next_retry_at is not None:
        return None
    try:
        authorized(run)
    except Denied as exc:
        terminal(run, "paused", exc.code)
        return None
    w = run.window
    now = clock()
    if w.owner is not None:
        # Expiry is recovered by the scanner, including the original attempt's budget.
        if w.lease_until <= now:
            recover_owner(w, now)
        terminal(run, "coalesced")
        return None
    if not can_fetch(run.job, w.target_end, now, run.endpoint):
        terminal(run, "failed", "backfill_unavailable")
        return None
    if w.closed_at and not allows_closed(w, intent(run)):
        terminal(run, "coalesced")
        return None
    w.generation += 1
    w.owner = uuid4()
    w.lease_until = now + LEASE
    w.save(update_fields=["generation", "owner", "lease_until"])
    run.generation = w.generation
    run.owner = w.owner
    run.started_at = now
    run.status = "running"
    run.dispatch_due = None
    run.save(update_fields=["generation", "owner", "started_at", "status", "dispatch_due"])
    return w.owner


def fence(run, owner):
    w = run.window
    if (
        run.status != "running"
        or run.owner != owner
        or w.owner != owner
        or run.generation != w.generation
        or w.lease_until <= clock()
        or run.started_at + MAX_EXECUTION <= clock()
    ):
        raise Denied("stale_owner")


def guard_update(run, token, *, check_rights=True, **values):
    fence(run, token)
    # PG determines expiry again in the update that orders this commit against recovery.
    live = WorkWindow.objects.filter(
        pk=run.window_id,
        owner=token,
        generation=run.generation,
        lease_until__gt=RawSQL("clock_timestamp()", []),
    )
    deadline = getattr(run, "rights_boundary", None)
    if deadline and check_rights:
        live = live.filter(
            RawSQL("clock_timestamp() < %s", [deadline], output_field=BooleanField())
        )
    if live.update(**values) != 1:
        raise Denied(
            "rights_boundary"
            if check_rights and deadline and deadline <= clock()
            else "stale_owner"
        )


@transaction.atomic
def reserve(run_id, owner):
    run = locked(run_id)
    fence(run, owner)
    caps = authorized(run)
    if run.request_state != "unsent":
        raise Denied("already_reserved")
    now = clock()
    w = run.window
    job = run.job
    if not can_fetch(job, w.target_end, now, run.endpoint):
        raise Denied("backfill_unavailable")
    try:
        cnemc_jobs.check_total(job)
        if run.endpoint and w.runs.filter(reserved_at__isnull=False).count() >= 2 * MAX_ATTEMPTS:
            raise Denied("window_budget_exhausted")
        budget(job, caps, now)
        code = target_code(job, w.target_end - HOUR)
        final_caps = authorized(run)
        now = clock()
        if final_caps[:2] != caps[:2]:
            budget(job, final_caps, now)
    except BudgetExhausted as wait:
        # Atomically release this ownership without reserving or failing the window.
        # No source use here. A crossed rights boundary schedules immediate re-admission.
        guard_update(run, owner, check_rights=False, owner=None, lease_until=None)
        run.owner = None
        run.status = "pending"
        run.error_code = wait.code
        run.next_retry_at = quota_check_at(run, wait.check_at)
        run.save(update_fields=["owner", "status", "error_code", "next_retry_at"])
        return None
    if not can_fetch(job, w.target_end, now, run.endpoint):
        raise Denied("backfill_unavailable")
    run.request_state = "possible"
    run.reserved_at = now
    from .adapters.transport import HTTP_DEADLINE

    run.occupied_until = now + timedelta(seconds=HTTP_DEADLINE + 2)
    run.save(update_fields=["request_state", "reserved_at", "occupied_until"])
    guard_update(run, owner, lease_until=now + LEASE)
    if run.endpoint:
        return (
            run.endpoint,
            code,
            job.city.name if job.city_id else "",
            w.target_end - HOUR,
            job.product.synthetic,
        )
    return (
        job.product.shape,
        code,
        job.city.name if job.city_id else "",
        w.target_end - HOUR,
        job.profile,
    )


def release_slot(run, now):
    deadline = run.occupied_until
    run.occupied_until = now
    if deadline is None or deadline <= now:
        return
    source_id = run.job.product.source_id

    def notify_waiters():
        # The response released concurrency sooner than its conservative deadline.
        # Only advance timers that could have depended on that slot. In particular
        # a latest-only window may close before the original occupancy deadline.
        Run.objects.filter(
            job_id__in=Job.objects.filter(product__source_id=source_id).values("pk"),
            status="pending",
            error_code="budget_exhausted",
            request_state="unsent",
            next_retry_at__gt=now,
            next_retry_at__lte=deadline,
        ).update(next_retry_at=now)

    # A failed optional early wake must not turn a committed response into another fetch.
    transaction.on_commit(notify_waiters, robust=True)


@transaction.atomic
def mark_response(run_id, owner, at):
    run = locked(run_id)
    fence(run, owner)
    authorized(run)
    run.request_state = "responded"
    run.fetched_at = at
    release_slot(run, clock())
    run.save(update_fields=["request_state", "fetched_at", "occupied_until"])
    guard_update(run, owner, lease_until=clock() + LEASE)
    return run


def retry_time(run, now):
    backoff = now + timedelta(seconds=min(30, 2**run.attempt) + SystemRandom().random())
    if run.request_state == "possible" and run.occupied_until:
        return max(backoff, run.occupied_until)
    return backoff


def recover_owner(window, now):
    old = Run.objects.select_for_update().get(window=window, owner=window.owner, status="running")
    terminal(old, "expired", "lease_expired")
    if old.attempt < MAX_ATTEMPTS and not window.disposed:
        old.next_retry_at = retry_time(old, now)
        old.save(update_fields=["next_retry_at"])
    window.owner = None
    window.lease_until = None
    if not window.closed_at:
        window.result = "partial" if window.items.filter(accepted=True).exists() else "failed"
    window.save(update_fields=["owner", "lease_until", "result"])


@transaction.atomic
def record_failure(run_id, owner, code, *, action="fail", responded=False, retry_after=None):
    run = locked(run_id)
    fence(run, owner)
    now = clock()
    w = run.window
    try:
        authorized(run)
    except Denied as exc:
        action = "deny"
        code = exc.code
        responded = False
    if responded:
        run.request_state = "responded"
        release_slot(run, now)
        run.save(update_fields=["request_state", "occupied_until"])
    event = None
    if action != "deny" and responded and code in WIRE_QUALITY_CODES:
        try:
            scope = data_scope(run.job, w.target_start, w.target_end)
            applicable(run.job.product, "retain_normalized", scope, LEDGER, clock())
        except Denied:
            pass
        else:
            event = record_run_issue(run, code, scope, rule=1)
    if action in {"source", "product"}:
        target = run.job.product.source if action == "source" else run.job.product
        before = control.state(action, target)
        target.collection_pause = code
        fields = ["collection_pause"]
        if action == "source":
            # A later event cannot shorten an existing cooldown.
            if retry_after and target.collection_resume_after:
                retry_after = max(retry_after, target.collection_resume_after)
            target.collection_resume_after = retry_after or target.collection_resume_after
            fields.append("collection_resume_after")
        target.save(update_fields=fields)
        after_version = control.bump_shared(action, target)
        control.automatic(
            run.job,
            action,
            target,
            before,
            "paused",
            after_version - 1,
            after_version,
            code,
            run=run,
        )
    if (
        action == "retry"
        and run.attempt < MAX_ATTEMPTS
        and can_fetch(run.job, w.target_end, now, run.endpoint)
    ):
        run.next_retry_at = retry_time(run, now)
        run.save(update_fields=["next_retry_at"])
    guard_update(
        run,
        owner,
        owner=None,
        lease_until=None,
        result=w.result
        if w.closed_at
        else ("partial" if w.items.filter(accepted=True).exists() else "failed"),
    )
    terminal(run, "paused" if action in {"source", "product", "deny"} else "failed", code)
    if event is not None:
        try:
            check_detail(run.job.product, scope, clock())
        except Denied:
            event.delete()


def quota_check_at(run, available_at):
    if run.job.profile == "f-latest-v1" or run.endpoint and run.endpoint != "A10":
        return min(available_at, run.window.target_end)
    return available_at


def resume_quota_wait(run):
    w = run.window
    try:
        caps = authorized(run)
        now = clock()
        if not can_fetch(run.job, w.target_end, now, run.endpoint):
            terminal(run, "failed", "backfill_unavailable")
            return
        if w.closed_at and not allows_closed(w, intent(run)):
            terminal(run, "coalesced")
            return
        cnemc_jobs.check_total(run.job)
        budget(run.job, caps, now)
    except BudgetExhausted as wait:
        run.next_retry_at = quota_check_at(run, wait.check_at)
        run.save(update_fields=["next_retry_at"])
        return
    except Denied as exc:
        terminal(run, "paused", exc.code)
        return
    # A previous delivery reached a Worker. Start a new broker-delivery cycle
    # even if the source slot is won by a different Run before the next claim.
    Run.objects.filter(pk=run.pk).update(
        status="pending",
        error_code="",
        owner=None,
        finished_at=None,
        dispatch="pending",
        deliveries=0,
        dispatch_due=now,
    )
    enqueue(run)


@transaction.atomic
def retry_due(run_id):
    run = locked(run_id)
    w = run.window
    now = clock()
    if not run.next_retry_at or run.next_retry_at > now:
        return
    run.next_retry_at = None
    run.save(update_fields=["next_retry_at"])
    # Also resume an outstanding unsent quota denial written by an older Worker.
    if (
        run.status in ("pending", "failed")
        and run.error_code == "budget_exhausted"
        and run.request_state == "unsent"
    ):
        resume_quota_wait(run)
        return
    if run.status not in ("failed", "expired"):
        return
    if w.disposed or run.attempt >= MAX_ATTEMPTS:
        return
    # A later ordinary completion supersedes work left by an older generation.
    if w.closed_at and (not run.started_at or w.closed_at >= run.started_at):
        return
    new = Run.objects.create(
        endpoint=run.endpoint,
        validation_grant_id=run.validation_grant_id,
        job=run.job,
        window=w,
        actor_id=run.actor_id,
        executor_id=run.executor_id,
        predecessor=run,
        reason="retry",
        attempt=run.attempt + 1,
        dispatch_due=now,
    )
    try:
        authorized(new)
        if not can_fetch(run.job, w.target_end, now, run.endpoint):
            raise Denied("backfill_unavailable")
    except Denied as exc:
        terminal(new, "failed", exc.code)
    else:
        if w.closed_at and not allows_closed(w, intent(new)):
            terminal(new, "coalesced")
        else:
            enqueue(new)


@transaction.atomic
def scheduled(job_id):
    job = lock_job(job_id)
    from accounts.research_session import lock_users_and_groups

    now = clock()
    lock_users_and_groups({job.executor_id})
    schedule_segments.settle_handoff(job, now)
    if not job.enabled or not job.next_due_at or job.next_due_at > now:
        return
    due = job.next_due_at
    cadence = timedelta(seconds=job.cadence_seconds)
    boundary = job.effective_until
    if boundary and due >= boundary:
        job.next_due_at = None
        job.save(update_fields=["next_due_at"])
        return
    if cnemc_jobs.managed(job) or job.plan_state == "active":
        limit = min(now, boundary - timedelta(microseconds=1)) if boundary else now
        latest_due = due + ((limit - due) // cadence) * cadence
        if latest_due > due:
            ControlEvent.objects.create(
                job=job,
                actor_id=job.executor_id,
                action="skipped_slots",
                slot_start=due,
                slot_end=latest_due,
            )
            due = latest_due
    next_due = due + cadence
    job.next_due_at = None if boundary and next_due >= boundary else next_due
    job.save(update_fields=["next_due_at"])
    if job.plan_state == "active" and (
        job.product.collection_pause or job.product.source.collection_pause
    ):
        if job.next_due_at:
            ControlEvent.objects.create(
                job=job,
                actor_id=job.executor_id,
                action="skipped_slots",
                slot_start=due,
                slot_end=job.next_due_at,
            )
        return
    try:
        register(
            job,
            job.executor_id,
            due.replace(minute=0, second=0, microsecond=0),
            "scheduled",
            scheduled_for=due,
        )
    except Denied as exc:
        before, version = control.state("job", job), job.control_version
        job.enabled = False
        job.pause_reason = exc.code
        job.save(update_fields=["enabled", "pause_reason"])
        job.refresh_from_db(fields=["control_version"])
        control.automatic(job, "job", job, before, "paused", version, job.control_version, exc.code)


def reconcile_due():
    now = clock()
    for pk in (
        Job.objects.filter(enabled=True, next_due_at__lte=now)
        .order_by("next_due_at")
        .values_list("pk", flat=True)[:PAGE]
    ):
        scheduled(pk)
    from .recovery import scan_gaps

    for pk in (
        Job.objects.filter(
            profile=cnemc_jobs.PROFILE,
            enabled=True,
            disposed=False,
        )
        .filter(Q(next_due_at__isnull=False) | Q(plan_state="active", effective_from__lte=now))
        .filter(Q(effective_until=None) | Q(effective_until__gt=now - 25 * HOUR))
        .exclude(backfill_policy={})
        .order_by("pk")
        .values_list("pk", flat=True)[:PAGE]
    ):
        scan_gaps(pk)
    for pk in Run.objects.filter(status="running", window__lease_until__lte=now).values_list(
        "pk", flat=True
    )[:PAGE]:
        with transaction.atomic():
            run = locked(pk)
            w = run.window
            if run.status == "running" and w.owner == run.owner and w.lease_until <= clock():
                recover_owner(w, clock())
    for pk in (
        Run.objects.filter(next_retry_at__lte=now)
        .order_by("next_retry_at")
        .values_list("pk", flat=True)[:PAGE]
    ):
        retry_due(pk)
    for pk in (
        Run.objects.filter(status="pending", dispatch_due__lte=now)
        .order_by("dispatch_due")
        .values_list("pk", flat=True)[:PAGE]
    ):
        with transaction.atomic():
            run = locked(pk)
            if run.status != "pending" or not run.dispatch_due or run.dispatch_due > clock():
                continue
            if run.deliveries >= MAX_DELIVERIES:
                run.dispatch = "manual"
                run.dispatch_due = None
                run.save(update_fields=["dispatch", "dispatch_due"])
            else:
                enqueue(run)


def authorize_resume(job):
    now = clock()
    target = now.replace(minute=0, second=0, microsecond=0)
    return admission(job, job.executor_id, *bounds(job, target), allow_paused=True)


@transaction.atomic
def change_job(actor_id, job_id, action, *, request=None, data=None):
    if action not in control.JOB_ACTIONS:
        raise Denied("invalid_request")
    job = lock_job(job_id)
    session = control.lock_principals(request, {job.executor_id}) if request else None
    actor(actor_id, "manage_collection")
    if job.plan_state in {"draft", "discarded"}:
        raise Denied("precondition_conflict")
    now = clock()
    kind, target = control.target_for(job, action)
    version = (
        schedule_segments.effective_control_version(job, now)
        if kind == "job"
        else control.version(kind, target)
    )
    before = control.state(kind, target, now=now)
    if data is None:
        data = dict(action=action, request_id=None, reason_code="", expected_version=None)
    modern = data["request_id"] is not None
    if action != "pause":
        caps = authorize_resume(job)
        cnemc_jobs.check_total(job)
    elif modern:
        control.review_job(job, now)

    previous = control.previous(job, data, kind, target, actor_id)
    if previous:
        session.finish(())
        if action == "pause":
            control.review_job(job, clock())
        else:
            authorize_resume(job)
        return control.job_result(job, previous)
    if modern and version != data["expected_version"]:
        raise Denied("precondition_conflict")
    if action == "pause" and before == "paused":
        if modern:
            raise Denied("precondition_conflict")
        schedule_segments.settle_handoff(job, now)
        if session:
            session.finish(())
        return control.job_result(job) if request else job
    if action != "pause":
        if modern and before != "paused":
            raise Denied("precondition_conflict")
        if (
            kind == "source"
            and target.collection_resume_after
            and target.collection_resume_after > now
        ):
            raise Denied("resume_too_early")
        budget(job, caps, now)
        if kind == "job" and cnemc_jobs.managed(job):
            cnemc_jobs.ensure_schedule(job)
        elif (
            kind == "job"
            and job.plan_state == "legacy"
            and job.next_due_at
            and (schedule_segments.overlapping_clocks(job).filter(plan_state="active").exists())
        ):
            raise Denied("precondition_conflict")
    schedule_segments.settle_handoff(job, now)
    if kind == "job":
        job.enabled = action != "pause"
        job.pause_reason = "operator_pause" if action == "pause" else ""
        job.changed_by_id = actor_id
        job.changed_at = now
        job.save(update_fields=["enabled", "pause_reason", "changed_at", "changed_by"])
        job.refresh_from_db(fields=["control_version"])
        after_version = job.control_version
    else:
        target.collection_pause = ""
        fields = ["collection_pause"]
        if kind == "source":
            target.collection_resume_after = None
            fields.append("collection_resume_after")
        target.save(update_fields=fields)
        after_version = control.bump_shared(kind, target)
    control.event(
        job,
        data,
        kind,
        target,
        before,
        control.state(kind, target, now=now),
        version,
        after_version,
        operator_id=actor_id,
    )
    if action != "pause":
        wake_scanner()
    if session:
        session.finish(())
    if action != "pause":
        authorize_resume(job)
    elif modern:
        control.review_job(job, clock())
    return control.job_result(job) if request else job


def execute(run_id):
    from . import adapters, writer

    owner = claim(run_id)
    if owner is None:
        return
    try:
        args = reserve(run_id, owner)
        if args is None:
            return
        if args[0] in cnemc_jobs.cnemc.ENDPOINTS:
            endpoint, code, name, target, synthetic = args
            body, fetched = cnemc_jobs.cnemc.fetch(
                endpoint, code, name, target, synthetic=synthetic
            )
            run = mark_response(run_id, owner, fetched)
            parsed = cnemc_jobs.cnemc.parse(
                endpoint,
                body,
                fetched,
                start=run.window.target_start,
                end=run.window.target_end,
                city_code=code,
                city_name=name,
                source_timezone=run.job.product.source_timezone,
                selection=run.window.selection,
                standard=run.window.standard,
            )
        else:
            body, fetched = adapters.fetch(*args)
            run = mark_response(run_id, owner, fetched)
            parsed = adapters.parse(
                args[0],
                body,
                fetched,
                start=run.window.target_start,
                end=run.window.target_end,
                city_code=args[1],
                city_name=args[2],
                source_timezone=run.job.product.source_timezone,
                allowed_codes=run.job.expected_codes,
            )
        del body
        cells, response_anomaly = writer.manifest(run_id, owner, parsed)
        blocks = list(writer.chunks(cells))
        ordinal = 0
        for i, block in enumerate(blocks):
            ordinal = writer.commit_chunk(
                run_id,
                owner,
                block,
                ordinal,
                i == len(blocks) - 1,
                response_anomaly=response_anomaly,
            )
        return
    except adapters.WireError as exc:
        code = exc.code
        action = (
            "retry"
            if code in {"upstream_transient", "timeout"}
            else "source"
            if code
            in {
                "upstream_rate_limited",
                "upstream_denied",
                "unexpected_redirect",
                "transport_security",
            }
            else "product"
        )
        responded, retry_after = exc.responded, exc.retry_after
    except Denied as exc:
        code, action, responded, retry_after = exc.code, "deny", False, None
        if code == "stale_owner":
            return
    except DatabaseError:
        code, action, responded, retry_after = "database_error", "retry", False, None
    try:
        record_failure(
            run_id, owner, code, action=action, responded=responded, retry_after=retry_after
        )
    except Denied as exc:
        if exc.code != "stale_owner":
            raise


def authorize_delivery(run):
    caps = authorized(run)
    now = clock()
    if not can_fetch(run.job, run.window.target_end, now, run.endpoint):
        raise Denied("backfill_unavailable")
    cnemc_jobs.check_total(run.job)
    return caps, now


@transaction.atomic
def redeliver(actor_id, run_id, *, request=None, data=None):
    run = locked(run_id)
    session = control.lock_principals(request, {run.actor_id, run.executor_id}) if request else None
    actor(actor_id, "manage_collection")
    caps, now = authorize_delivery(run)
    version = run.control_version
    if data is None:
        data = dict(action="redeliver", request_id=None, reason_code="", expected_version=None)
    modern = data["request_id"] is not None
    previous = control.previous(run.job, data, "run", run, actor_id)
    if previous:
        session.finish(())
        authorize_delivery(run)
        return run
    if (
        modern
        and version != data["expected_version"]
        or run.status != "pending"
        or run.request_state != "unsent"
        or run.owner
        or run.dispatch != "manual"
        or run.next_retry_at is not None
        or run.window.owner
        or run.window.closed_at
        and not allows_closed(run.window, intent(run))
    ):
        raise Denied("precondition_conflict" if modern else "invalid_redelivery")
    budget(run.job, caps, now)
    run.dispatch = "pending"
    run.deliveries = 0
    run.dispatch_due = clock()
    run.save(update_fields=["dispatch", "deliveries", "dispatch_due"])
    run.refresh_from_db(fields=["control_version"])
    control.event(
        run.job,
        data,
        "run",
        run,
        "manual",
        run.dispatch,
        version,
        run.control_version,
        operator_id=actor_id,
        run=run,
    )
    if session:
        session.finish(())
    authorize_delivery(run)
    enqueue(run)
    return run


@transaction.atomic
def register_job(
    actor_id,
    executor_id,
    product_id,
    city_id,
    profile,
    cadence,
    first_due,
    codes,
    basis,
    *,
    selection=None,
    standard=None,
    backfill_policy=None,
    validation_grant_id=None,
    reuse=False,
):
    from observations.models import City, Product, Source
    from observations.policy import locked_product

    actor(actor_id, "manage_collection")
    if (
        type(cadence) is not int
        or not 1 <= cadence <= 86400
        or not isinstance(basis, str)
        or len(basis) > 100
    ):
        raise Denied("invalid_job")
    source_id = Product.objects.filter(pk=product_id).values_list("source_id", flat=True).first()
    if not source_id:
        raise Denied("invalid_job")
    Source.objects.select_for_update().get(pk=source_id)
    product = locked_product(product_id)
    city = (
        City.objects.filter(source_id=source_id, pk=city_id).first()
        if city_id is not None
        else None
    )
    if city_id is not None and city is None:
        raise Denied("invalid_job")
    parse_due = cnemc_jobs.due_time if profile == cnemc_jobs.PROFILE else parse_hour
    due = parse_due(first_due) if first_due else None
    job = Job(
        product=product,
        city=city,
        profile=profile,
        executor_id=executor_id,
        cadence_seconds=cadence,
        expected_codes=codes,
        selection={} if selection is None else selection,
        standard={} if standard is None else standard,
        backfill_policy={} if backfill_policy is None else backfill_policy,
        validation_grant_id=validation_grant_id,
        roster_basis=basis,
        next_due_at=due,
        changed_by_id=actor_id,
    )
    validate_job_registration(job, due or clock())
    if due and schedule_segments.overlapping_clocks(job).filter(plan_state="active").exists():
        raise Denied("duplicate_schedule")
    if cnemc_jobs.managed(job):
        if reuse and (prior := cnemc_jobs.same_job(job)):
            return prior
        cnemc_jobs.ensure_schedule(job)
    job.save()
    ControlEvent.objects.create(job=job, actor_id=actor_id, action="register")
    return job


def validate_job_registration(job, stamp, *, registration=True, fixture_preview=False):
    """Apply the same registration contract to host Jobs and schedule candidates."""
    if cnemc_jobs.managed(job):
        cnemc_jobs.configure(job)
    elif any((job.selection, job.standard, job.backfill_policy, job.validation_grant_id)):
        raise Denied("invalid_job")
    start, end = bounds(job, schedule_segments.hour(stamp))
    caps = (
        schedule_fixture_caps(job, start, end)
        if fixture_preview
        else admission(
            job, job.executor_id, start, end, allow_paused=True, registration=registration
        )
    )
    expected(job, start, end)
    if cnemc_jobs.managed(job) and job.selection.get("mode") == "include":
        cnemc_jobs.scopes(job, start, end)
    return caps
