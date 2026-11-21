from celery import shared_task
from celery.utils.log import get_task_logger

from .services import consume

logger = get_task_logger(__name__)


class ProcessingFailure(Exception):
    """Public task error, safe for the Celery log."""


@shared_task(name="samples.process_fixture", ignore_result=True, throws=(ProcessingFailure,))
def process_fixture(run_id):
    try:
        outcome = consume(run_id)
    except Exception:
        logger.error("fixture_end run=%s reason=processing_failed", run_id)
        raise ProcessingFailure("processing_failed") from None
    logger.info("fixture_end run=%s reason=%s", run_id, outcome)
