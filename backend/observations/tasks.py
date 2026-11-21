from celery import shared_task
from celery.utils.log import get_task_logger

from .disposition import dispose_due
from .history import consume

logger = get_task_logger(__name__)


@shared_task(name="observations.import_history", ignore_result=True)
def process_history(attempt_id):
    try:
        state = consume(attempt_id)
    except Exception:
        raise RuntimeError("history_reconciliation_required") from None
    logger.info("history_end attempt=%s state=%s", attempt_id, state)


@shared_task(name="observations.dispose", ignore_result=True)
def dispose(product_id=None):
    dispose_due(product_id)
    logger.info("disposition_end product=%s", product_id)
