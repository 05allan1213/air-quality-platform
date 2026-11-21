from celery import shared_task

from . import services


@shared_task(
    name="collection.execute_run", acks_late=True, reject_on_worker_lost=True, ignore_result=True
)
def execute_run(run_id):
    services.execute(run_id)


@shared_task(name="collection.reconcile_due", ignore_result=True)
def reconcile_due():
    services.reconcile_due()
