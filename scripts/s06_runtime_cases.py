"""Run the PG-backed V15 window and V12 file cases in the private process namespace."""

import os
from datetime import timedelta
from uuid import uuid4

from s01_runtime import wait_for
from s06_guard import IsolationError, require

COLLECTION_RUNS = "/api/v1/admin/collection-runs/"
HEALTH = "/api/v1/admin/operations/health/"


def verify_window_wait(runtime, web, user, checks):
    from collection.models import Job, Run
    from django.conf import settings
    from django.contrib.auth.models import Permission
    from django.utils.dateparse import parse_datetime
    from observations.models import City, CityIdentifier, Source
    from observations.policy import clock
    from psycopg.types.range import TimestamptzRange
    from s02_support import BOUNDARY, START, allow, create_product
    from s03_runtime import LIMITS

    user.user_permissions.add(
        *Permission.objects.filter(
            content_type__app_label="observations",
            codename__in=("manage_grants", "run_collection", "review_observation"),
        )
    )
    limits = LIMITS | {"max_requests": 1, "period_seconds": 600}
    source = Source.objects.create(
        namespace="s03.fixture", publisher="S06 F quota", collection_limits=limits
    )
    product = create_product("C", "C", source)
    product.contract_version = "s03.f.v1"
    product.save(update_fields=["contract_version"])
    city = City.objects.create(source=source, name="S06 Wait F", geography="fixture-wait")
    CityIdentifier.objects.create(
        source=source,
        city=city,
        code="801",
        evidence="F",
        active_period=TimestamptzRange(START, None),
    )
    for op in ("acquire_online", "process_local", "retain_normalized", "review"):
        options = {"collection_limits": limits} if op == "acquire_online" else {}
        allow(user, product, op, fields=["*"], **options)
    job = Job.objects.create(
        product=product,
        city=city,
        executor=user,
        enabled=True,
        profile="f-exact-v1",
        expected_codes=["801"],
        roster_basis="F",
    )
    result = checks["window_wait"] = {}

    def submit(at):
        status, body = web.call(
            COLLECTION_RUNS,
            {
                "job": job.pk,
                "reason": "manual",
                "request_id": str(uuid4()),
                "target": at.isoformat(),
            },
        )
        require(status == 202, "wait_run_submission")
        return Run.objects.get(pk=body["run_id"])

    def state(run, attribute):
        run.refresh_from_db()
        return getattr(run, attribute)

    runtime.stop("beat")
    # No origin runs in this case. One admitted loopback transport failure spends quota.
    first = submit(BOUNDARY)
    wait_for("quota setup reservation", lambda: state(first, "status") == "failed", seconds=8)
    require(first.reserved_at and first.error_code == "upstream_transient", "quota_setup_run")
    target = BOUNDARY + timedelta(hours=1)
    quota = submit(target)
    wait_for(
        "Worker quota decision", lambda: state(quota, "error_code") == "budget_exhausted", seconds=8
    )
    require(
        quota.status == "pending"
        and quota.request_state == "unsent"
        and quota.next_retry_at > clock(),
        "quota_wait_state",
    )
    runtime.stop("worker")
    waiting = submit(target)
    require(
        waiting.window_id == quota.window_id
        and waiting.dispatch == "sent"
        and waiting.started_at is None,
        "dispatch_wait_state",
    )
    status, payload = web.call(f"{HEALTH}?window={quota.window_id}")
    require(status == 200, "window_wait_http")
    window = payload["window"]
    require(
        window["visible_quota_wait"] == 1
        and window["visible_sent_not_started"] == 1
        and window["visible_pending"] == 2
        and window["visible_unconfirmed_dispatch"] == 0
        and parse_datetime(window["next_visible_quota_check_at"]) == quota.next_retry_at
        and window["oldest_visible_pending_age_seconds"] >= 0,
        "window_wait_projection",
    )
    queue = settings.CELERY_TASK_ROUTES["collection.execute_run"]["queue"]
    with runtime.broker() as broker:
        messages = broker.llen(queue)
    require(messages == 1, "window_wait_broker_count")
    result["with_worker_stopped"] = window | {"http": status, "redis_ready_messages": messages}
    runtime.worker()
    wait_for(
        "Worker to evaluate accumulated Run",
        lambda: state(waiting, "error_code") == "budget_exhausted",
        seconds=8,
    )
    status, payload = web.call(f"{HEALTH}?window={quota.window_id}")
    require(status == 200, "window_wait_recovered_http")
    window = payload["window"]
    require(
        window["visible_quota_wait"] == 2 and window["visible_sent_not_started"] == 0,
        "window_wait_worker_recovered",
    )
    result["with_worker_restored"] = window | {"http": status}
    runtime.beat()


def verify_file_disposition(runtime, user, checks):
    import fcntl

    from accounts.models import ServicePulse
    from django.contrib.auth.models import Permission
    from django.db import transaction
    from observations.controlled_storage import register_volume
    from observations.disposition import PACKAGE_SCOPE, private_dir
    from observations.models import DispositionJob, StagedInput
    from observations.policy import Denied, clock, require_use
    from observations.tasks import dispose
    from s02_support import allow, create_product, observe_db, reject_observation_write

    user.user_permissions.add(
        Permission.objects.get(content_type__app_label="observations", codename="manage_grants")
    )
    terms = {
        "data_from": PACKAGE_SCOPE.start,
        "data_until": PACKAGE_SCOPE.end,
        "fields": ["archive"],
    }
    content = b"F scope check"
    result = checks["worker_disposition"] = {}

    def register_stage(product):
        with transaction.atomic():
            require_use(user.pk, product, "retain_raw", PACKAGE_SCOPE, {"archive"})
            stage = StagedInput.objects.create(
                product=product,
                owner=user,
                state="ready",
                expires_at=clock() + timedelta(minutes=10),
                volume_generation=register_volume(private_dir()),
            )
            path = private_dir() / str(stage.pk)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(content)
        return stage, path

    def read_stage(product, path):
        with transaction.atomic():
            require_use(user.pk, product, "retain_raw", PACKAGE_SCOPE, {"archive"})
            with path.open("rb") as stream:
                fcntl.flock(stream.fileno(), fcntl.LOCK_SH)
                return stream.read()

    def reject_code(operation):
        try:
            operation()
        except Denied as exc:
            return exc.code
        raise IsolationError("old_or_hard_expiry_use_allowed")

    runtime.stop("beat")
    runtime.stop("worker")
    target = create_product("v12-expiring-file")
    independent = create_product("v12-independent-file")
    hard = create_product("v12-hard-file")
    allow(user, independent, "retain_raw", **terms)
    keep, keep_path = register_stage(independent)
    allow(user, hard, "retain_raw", retain_until=clock() + timedelta(minutes=5), **terms)
    existing_paths = set(private_dir().iterdir())
    result["hard_cutoff_denied"] = reject_code(lambda: register_stage(hard))
    require(
        not StagedInput.objects.filter(product=hard).exists()
        and existing_paths == set(private_dir().iterdir()),
        "hard_cutoff_staged",
    )
    cutoff = clock() + timedelta(seconds=4)
    grant = allow(
        user, target, "retain_raw", valid_until=cutoff, expiry_mode="recoverable", **terms
    )
    stage, path = register_stage(target)
    require(read_stage(target, path) == content, "old_file_unreadable_before_cutoff")
    with observe_db() as sql:
        sql.execute(
            "SELECT pg_sleep(greatest(0, extract(epoch FROM %s-clock_timestamp()))+.02)", [cutoff]
        )
    result["after_cutoff_denied"] = reject_code(lambda: read_stage(target, path))
    replacement = allow(user, target, "retain_raw", valid_from=grant.valid_from, **terms)
    job = DispositionJob.objects.get(boundary_grant=grant)
    require(
        job.completed_at is None and replacement.pk not in job.boundary_basis,
        "expiry_basis_changed",
    )
    result["after_regrant_denied"] = reject_code(lambda: read_stage(target, path))
    require(path.exists() and read_stage(independent, keep_path) == content, "file_control_missing")
    result["before_restart"] = {
        "cutoff_at": cutoff.isoformat(),
        "file_present": True,
        "pending": True,
    }

    def job_state(field):
        job.refresh_from_db()
        return getattr(job, field)

    with path.open("rb") as held:
        fcntl.flock(held.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
        runtime.worker()
        dispose.apply_async(args=[target.pk], retry=False)
        wait_for(
            "Worker to reject outstanding reader",
            lambda: job_state("error_code") == "cleanup_failed",
            seconds=10,
        )
        require(
            job.completed_at is None and path.exists() and os.fstat(held.fileno()).st_nlink == 1,
            "reader_open_but_disposition_complete",
        )
    result["worker_open_reader"] = {"pending": True, "error": "cleanup_failed", "links": 1}
    with reject_observation_write(DispositionJob, "UPDATE", "NEW.completed_at IS NOT NULL"):
        dispose.apply_async(args=[target.pk], retry=False)
        wait_for("Worker to unlink before injected rollback", lambda: not path.exists(), seconds=10)
        with observe_db() as sql:
            sql.execute("SET LOCAL lock_timeout = '3s'")
            stage_state = sql.execute(
                "SELECT state FROM observations_stagedinput WHERE id=%s FOR UPDATE", [stage.pk]
            ).fetchone()
            job_state_sql = sql.execute(
                "SELECT completed_at, error_code FROM observations_dispositionjob WHERE id=%s",
                [job.pk],
            ).fetchone()
        require(
            stage_state == ("ready",) and job_state_sql == (None, "cleanup_failed"),
            "disposition_rollback_published_completion",
        )
    result["after_injected_pg_rollback"] = {
        "path_present": False,
        "pg_stage_state": "ready",
        "pg_job_completed": False,
    }
    dispose.apply_async(args=[target.pk], retry=False)
    complete = wait_for(
        "Worker to reconcile expired file", lambda: job_state("completed_at"), seconds=10
    )
    with observe_db() as sql:
        states = sql.execute(
            "SELECT id, state FROM observations_stagedinput WHERE id IN (%s,%s)",
            [stage.pk, keep.pk],
        ).fetchall()
    require(
        dict(states) == {stage.pk: "cleared", keep.pk: "ready"}
        and not os.path.lexists(path)
        and read_stage(independent, keep_path) == content,
        "worker_file_disposition_incomplete",
    )
    after = clock()
    dispose.apply_async(args=[target.pk], retry=False)
    history_queue = runtime.queue.removesuffix(".fixture") + ".history"
    wait_for(
        "Worker to complete idempotent rescan",
        lambda: ServicePulse.objects.filter(
            component="worker", queue=history_queue, consumed_at__gte=after
        ).exists(),
        seconds=10,
    )
    require(
        job_state("completed_at") == complete and not os.path.lexists(path),
        "repeat_file_disposition",
    )
    result["after_worker_sweeps"] = {
        "completed_at": complete.isoformat(),
        "old_path_present": False,
        "old_pg_state": "cleared",
        "independent_pg_state": "ready",
        "independent_readable": True,
        "idempotent": True,
    }
    runtime.beat()
