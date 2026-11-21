"""Register triggers, fence worker ownership and reserve at most one access per Run."""

from datetime import timedelta
from random import SystemRandom
from uuid import UUID, uuid4

from django.db import DatabaseError, transaction
from django.db.models import BooleanField
from django.db.models.expressions import RawSQL
from django_celery_beat.models import IntervalSchedule, PeriodicTask
from kombu.exceptions import OperationalError
from observations.policy import Denied, actor, clock

from .admission import (
    HOUR,
    admission,
    bounds,
    budget,
    can_fetch,
    expected,
    lock_job,
    parse_hour,
    target_code,
)
from .models import ControlEvent, Job, Run, WorkWindow

LEASE = timedelta(seconds=60)
MAX_EXECUTION = timedelta(minutes=10)
MAX_ATTEMPTS = 3
MAX_DELIVERIES = 8
PAGE = 64


def wake_scanner():
    interval, _ = IntervalSchedule.objects.get_or_create(every=5, period=IntervalSchedule.SECONDS)
    PeriodicTask.objects.update_or_create(
        name="s03.reconcile",
        defaults=dict(
            interval=interval, task="collection.reconcile_due", queue="s03.collection", enabled=True
        ),
    )


def enqueue(run):
    transaction.on_commit(lambda: publish(run.pk))


def intent(run):
    # Only automatic retries inherit the trigger. An independent review may also
    # point to a former acquisition for lineage, without inheriting its intent.
    for _ in range(MAX_ATTEMPTS):
        if run.reason != "retry":
            return run.reason
        if not run.predecessor_id:
            break
        run = run.predecessor
    raise Denied("invalid_retry_lineage")


def allows_closed(window, reason):
    if reason == "review":
        return True
    if reason != "backfill" or window.profile != "f-exact-v1" or window.expected is None:
        return False
    accepted = set(
        window.items.filter(accepted=True, receipts__isnull=False).values_list(
            "item_key", flat=True
        )
    )
    return bool(set(window.expected) - accepted)


def register(job, actor_id, target, reason, *, request_id=None, scheduled_for=None):
    start, end = bounds(job, target)
    admission(job, actor_id, start, end)
    if reason == "review":
        actor(actor_id, "review_observation")
    key = f"city:{job.city_id}" if job.city_id else "national"
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
    cells = expected(job, start, end)
    window, created = WorkWindow.objects.select_for_update().get_or_create(
        product=job.product,
        scope_key=key,
        target_start=start,
        target_end=end,
        profile=job.profile,
        defaults={"expected": cells, "roster_basis": job.roster_basis},
    )
    if window.disposed:
        raise Denied("window_disposed")
    if not created and (window.expected != cells or window.roster_basis != job.roster_basis):
        raise Denied("window_contract_conflict")
    if scheduled_for:
        prior = Run.objects.filter(job=job, scheduled_for=scheduled_for).first()
        if prior:
            return prior
    previous = window.runs.filter(reserved_at__isnull=False).order_by("-reserved_at").first()
    run = Run.objects.create(
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
    if not can_fetch(job, end, clock()):
        terminal(run, "failed", "backfill_unavailable")
    elif window.closed_at and not allows_closed(window, reason):
        terminal(run, "coalesced")
    else:
        wake_scanner()
        enqueue(run)
    return run


@transaction.atomic
def submit(actor_id, data):
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
    return register(
        job, actor_id, parse_hour(data["target"]), data["reason"], request_id=request_id
    )


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
        or window.scope_key != (f"city:{job.city_id}" if job.city_id else "national")
    ):
        raise Denied("window_contract_conflict")
    run.job = job
    run.window = window
    return run


def authorized(run):
    if run.window.disposed:
        raise Denied("window_disposed")
    if intent(run) == "review":
        actor(run.actor_id, "review_observation")
    caps = admission(run.job, run.actor_id, run.window.target_start, run.window.target_end)
    run.rights_boundary = caps[2]
    return caps


def publish(run_id):
    from .tasks import execute_run

    with transaction.atomic():
        run = locked(run_id)
        if (
            run.status != "pending"
            or run.request_state != "unsent"
            or run.deliveries >= MAX_DELIVERIES
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
    if run.status != "pending" or run.request_state != "unsent":
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
    if not can_fetch(run.job, w.target_end, now):
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


def guard_update(run, token, **values):
    fence(run, token)
    # PG determines expiry again in the update that orders this commit against recovery.
    live = WorkWindow.objects.filter(
        pk=run.window_id,
        owner=token,
        generation=run.generation,
        lease_until__gt=RawSQL("clock_timestamp()", []),
    )
    deadline = getattr(run, "rights_boundary", None)
    if deadline:
        live = live.filter(
            RawSQL("clock_timestamp() < %s", [deadline], output_field=BooleanField())
        )
    if live.update(**values) != 1:
        raise Denied("stale_owner" if not deadline or deadline > clock() else "rights_boundary")


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
    if not can_fetch(job, w.target_end, now):
        raise Denied("backfill_unavailable")
    budget(job, caps, now)
    code = target_code(job, w.target_end - HOUR)
    final_caps = authorized(run)
    now = clock()
    if final_caps[:2] != caps[:2]:
        budget(job, final_caps, now)
    if not can_fetch(job, w.target_end, now):
        raise Denied("backfill_unavailable")
    run.request_state = "possible"
    run.reserved_at = now
    from .adapters.transport import HTTP_DEADLINE

    run.occupied_until = now + timedelta(seconds=HTTP_DEADLINE + 2)
    run.save(update_fields=["request_state", "reserved_at", "occupied_until"])
    guard_update(run, owner, lease_until=now + LEASE)
    return (
        job.product.shape,
        code,
        job.city.name if job.city_id else "",
        w.target_end - HOUR,
        job.profile,
    )


@transaction.atomic
def mark_response(run_id, owner, at):
    run = locked(run_id)
    fence(run, owner)
    authorized(run)
    run.request_state = "responded"
    run.fetched_at = at
    run.occupied_until = clock()
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
        run.occupied_until = now
        run.save(update_fields=["request_state", "occupied_until"])
    if action == "source":
        src = run.job.product.source
        src.collection_pause = code
        src.collection_resume_after = retry_after
        src.save(update_fields=["collection_pause", "collection_resume_after"])
    elif action == "product":
        run.job.product.collection_pause = code
        run.job.product.save(update_fields=["collection_pause"])
    if action == "retry" and run.attempt < MAX_ATTEMPTS and can_fetch(run.job, w.target_end, now):
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


@transaction.atomic
def retry_due(run_id):
    run = locked(run_id)
    w = run.window
    now = clock()
    if not run.next_retry_at or run.next_retry_at > now or run.status not in ("failed", "expired"):
        return
    run.next_retry_at = None
    run.save(update_fields=["next_retry_at"])
    if w.disposed or run.attempt >= MAX_ATTEMPTS:
        return
    # A later ordinary completion supersedes work left by an older generation.
    if w.closed_at and (not run.started_at or w.closed_at >= run.started_at):
        return
    new = Run.objects.create(
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
        if not can_fetch(run.job, w.target_end, now):
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
    now = clock()
    if not job.enabled or not job.next_due_at or job.next_due_at > now:
        return
    due = job.next_due_at
    job.next_due_at = due + timedelta(seconds=job.cadence_seconds)
    job.save(update_fields=["next_due_at"])
    try:
        register(
            job,
            job.executor_id,
            due.replace(minute=0, second=0, microsecond=0),
            "scheduled",
            scheduled_for=due,
        )
    except Denied as exc:
        job.enabled = False
        job.pause_reason = exc.code
        job.save(update_fields=["enabled", "pause_reason"])


def reconcile_due():
    now = clock()
    for pk in (
        Job.objects.filter(enabled=True, next_due_at__lte=now)
        .order_by("next_due_at")
        .values_list("pk", flat=True)[:PAGE]
    ):
        scheduled(pk)
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


@transaction.atomic
def change_job(actor_id, job_id, action):
    if action not in ("pause", "resume", "resume_source", "resume_product"):
        raise Denied("invalid_request")
    job = lock_job(job_id)
    actor(actor_id, "manage_collection")
    now = clock()
    if action != "pause":
        target = now.replace(minute=0, second=0, microsecond=0)
        caps = admission(job, job.executor_id, *bounds(job, target), allow_paused=True)
        src = job.product.source
        if (
            action == "resume_source"
            and src.collection_resume_after
            and src.collection_resume_after > now
        ):
            raise Denied("resume_too_early")
        budget(job, caps, now)
        if action == "resume_source":
            src.collection_pause = ""
            src.collection_resume_after = None
            src.save(update_fields=["collection_pause", "collection_resume_after"])
        elif action == "resume_product":
            job.product.collection_pause = ""
            job.product.save(update_fields=["collection_pause"])
        else:
            job.enabled = True
            job.pause_reason = ""
        wake_scanner()
    else:
        job.enabled = False
        job.pause_reason = "operator_pause"
    ControlEvent.objects.create(job=job, actor_id=actor_id, action=action, occurred_at=now)
    job.changed_by_id = actor_id
    job.changed_at = now
    job.save(update_fields=["enabled", "pause_reason", "changed_at", "changed_by"])
    return job


def execute(run_id):
    from . import adapters, writer

    owner = claim(run_id)
    if owner is None:
        return
    try:
        args = reserve(run_id, owner)
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
        blocks = list(writer.chunks(writer.manifest(run_id, owner, parsed)))
        ordinal = 0
        for i, block in enumerate(blocks):
            ordinal = writer.commit_chunk(run_id, owner, block, ordinal, i == len(blocks) - 1)
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
        if code == "budget_exhausted":
            action = "retry"
    except DatabaseError:
        code, action, responded, retry_after = "database_error", "retry", False, None
    try:
        record_failure(
            run_id, owner, code, action=action, responded=responded, retry_after=retry_after
        )
    except Denied as exc:
        if exc.code != "stale_owner":
            raise


@transaction.atomic
def redeliver(actor_id, run_id):
    run = locked(run_id)
    actor(actor_id, "manage_collection")
    authorized(run)
    if (
        run.status != "pending"
        or run.request_state != "unsent"
        or run.owner
        or run.dispatch != "manual"
    ):
        raise Denied("invalid_redelivery")
    ControlEvent.objects.create(job=run.job, actor_id=actor_id, action="redeliver", run=run)
    run.dispatch = "pending"
    run.deliveries = 0
    run.dispatch_due = clock()
    run.save(update_fields=["dispatch", "deliveries", "dispatch_due"])
    enqueue(run)
    return run


@transaction.atomic
def register_job(
    actor_id, executor_id, product_id, city_id, profile, cadence, first_due, codes, basis
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
    due = parse_hour(first_due) if first_due else None
    job = Job(
        product=product,
        city=city,
        profile=profile,
        executor_id=executor_id,
        cadence_seconds=cadence,
        expected_codes=codes,
        roster_basis=basis,
        next_due_at=due,
        changed_by_id=actor_id,
    )
    target = due or clock().replace(minute=0, second=0, microsecond=0)
    start, end = bounds(job, target)
    admission(job, executor_id, start, end, allow_paused=True)
    expected(job, start, end)
    job.save()
    ControlEvent.objects.create(job=job, actor_id=actor_id, action="register")
    return job
