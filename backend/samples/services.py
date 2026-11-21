from dataclasses import asdict

from django.db import transaction
from django.utils import timezone
from kombu.exceptions import OperationalError

from .models import FixtureObservation, FixtureRun
from .normalization import KEY, Rejected, load_fixture, normalize
from .policy import require_operator, require_uses

REASON = "local_verification"


def submit(actor_id, payload):
    from .tasks import process_fixture

    # The HTTP request has no outer transaction. durable=True also enforces this
    # for future callers: a real Worker must be able to see the row immediately.
    with transaction.atomic(durable=True):
        require_operator(actor_id)
        if not isinstance(payload, dict) or payload != {"fixture_key": KEY, "reason": REASON}:
            raise Rejected("invalid_request")
        require_uses("F", "process_local", "retain")
        run = FixtureRun.objects.create(actor_id=actor_id, fixture_key=KEY, reason=REASON)
    run_id = str(run.pk)
    try:
        process_fixture.apply_async(args=[run_id], task_id=run_id, retry=False)
        dispatch = FixtureRun.Dispatch.SENT
    except (OperationalError, TimeoutError):
        dispatch = FixtureRun.Dispatch.UNCONFIRMED
    # Do not save the stale `run` instance: a consumer may already be terminal.
    FixtureRun.objects.filter(pk=run_id).update(dispatch=dispatch)
    return run_id, dispatch


def store_observation(sample, fetched_at):
    fields = asdict(sample)
    key = fields.pop("fixture_key")
    row, created = FixtureObservation.objects.get_or_create(
        fixture_key=key, defaults={**fields, "fetched_at": fetched_at}
    )
    if not created and any(getattr(row, field) != value for field, value in fields.items()):
        raise Rejected("fixture_conflict")
    return row


def consume(run_id):
    started_at = timezone.now()
    try:
        with transaction.atomic():
            run = FixtureRun.objects.select_for_update().get(pk=run_id)
            if run.state != FixtureRun.State.PENDING:
                return "duplicate"
            run.started_at = started_at
            try:
                require_uses("F", "process_local", "retain")
                require_operator(run.actor_id)
                if run.fixture_key != KEY:
                    raise Rejected("invalid_fixture_key")
                fetched_at = timezone.now()
                sample = normalize(load_fixture())
                run.observation = store_observation(sample, fetched_at)
                run.state = FixtureRun.State.SUCCEEDED
            except Rejected as exc:
                run.state, run.error_code = FixtureRun.State.REJECTED, exc.code
            run.finished_at = timezone.now()
            run.save(
                update_fields=["started_at", "finished_at", "state", "observation", "error_code"]
            )
            return run.error_code or "succeeded"
    except Exception:
        # Outside the rolled-back transaction. A concurrently completed delivery wins.
        FixtureRun.objects.filter(pk=run_id, state=FixtureRun.State.PENDING).update(
            state=FixtureRun.State.FAILED,
            started_at=started_at,
            finished_at=timezone.now(),
            error_code="processing_failed",
        )
        raise
