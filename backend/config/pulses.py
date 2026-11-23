"""Service evidence comes from the process completing the observed work."""

from time import monotonic

from celery.signals import heartbeat_sent, task_postrun, worker_ready
from django.conf import settings
from django.db import DatabaseError, close_old_connections

_last = {}
_ready_consumer = None


def record(component, instance, *, consumer=None, starting=False):
    from accounts.models import ServicePulse
    from observations.policy import clock

    if component == "worker" and instance != settings.OPERATIONS_WORKER:
        return
    tick = monotonic()
    if (
        not starting
        and tick - _last.get(component, float("-inf")) < settings.OPERATIONS_SAMPLE_SECONDS
    ):
        return
    close_old_connections()
    try:
        fields = {"observed_at": clock(), "instance": instance}
        if component == "worker":
            task_consumer = consumer.task_consumer if consumer is not None else None
            fields["subscribed_queues"] = (
                sorted(q.name for q in task_consumer.queues) if task_consumer is not None else None
            )
            if starting:
                fields.update(consumed_at=None, queue="")
        ServicePulse.objects.update_or_create(component=component, defaults=fields)
        _last[component] = tick
    except DatabaseError:
        # The last evidence expires independently if PG is unavailable.
        close_old_connections()


@worker_ready.connect
def ready(sender, **kwargs):
    global _ready_consumer

    if sender.hostname == settings.OPERATIONS_WORKER:
        _ready_consumer = sender
        record("worker", sender.hostname, consumer=sender, starting=True)


@heartbeat_sent.connect
def worker(sender, **kwargs):
    # Celery also uses this signal for online/offline events with no heartbeat timer.
    if sender.tref is not None and _ready_consumer is not None:
        record("worker", sender.eventer.hostname, consumer=_ready_consumer)


@task_postrun.connect
def consumed(task, state, **kwargs):
    from accounts.models import ServicePulse
    from observations.policy import clock

    delivered = task.request.delivery_info or {}
    queue = delivered.get("routing_key")
    queues = {
        settings.CELERY_TASK_DEFAULT_QUEUE,
        *(r["queue"] for r in settings.CELERY_TASK_ROUTES.values()),
    }
    if (
        state != "SUCCESS"
        or queue not in queues
        or task.request.hostname != settings.OPERATIONS_WORKER
    ):
        return
    close_old_connections()
    try:
        ServicePulse.objects.filter(component="worker", instance=task.request.hostname).update(
            consumed_at=clock(), queue=queue
        )
    except DatabaseError:
        close_old_connections()
