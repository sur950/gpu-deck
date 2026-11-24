"""
Base task class with retry logic, exponential backoff, and failure alerts.
"""

from typing import Callable, Optional

import httpx
from app.core.logger import get_logger
from app.core.observability import celery_tasks_total
from app.core.settings import settings
from celery import Task

logger = get_logger(__name__)

# The alerts domain sets this to route final task failures to Slack/webhook channels.
_failure_alert_handler: Optional[Callable[[str, str, str], None]] = None


def set_failure_alert_handler(
    handler: Optional[Callable[[str, str, str], None]],
) -> None:
    """Register ``handler(task_name, task_id, error_message)`` for exhausted-retry failures."""
    global _failure_alert_handler
    _failure_alert_handler = handler


class BaseTaskWithRetry(Task):
    """
    Base Celery task with:
    - exponential backoff retry on network/transient errors only
    - Prometheus counters per outcome
    - an alert hook on final failure (after all retries)
    """

    autoretry_for = (
        httpx.ConnectError,
        httpx.TimeoutException,
        httpx.NetworkError,
        ConnectionError,
        TimeoutError,
    )
    max_retries = settings.CELERY_MAX_RETRIES
    retry_backoff = True
    retry_backoff_max = 600
    retry_jitter = True

    def on_success(self, retval, task_id, args, kwargs):
        celery_tasks_total.labels(task=self.name, state="success").inc()
        super().on_success(retval, task_id, args, kwargs)

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        super().on_failure(exc, task_id, args, kwargs, einfo)
        celery_tasks_total.labels(task=self.name, state="failure").inc()
        logger.error(f"Task {self.name} [{task_id}] failed after {self.max_retries} retries: {exc}")
        if _failure_alert_handler is not None:
            try:
                _failure_alert_handler(self.name, task_id, f"{exc.__class__.__name__}: {exc}")
            except Exception as alert_error:  # noqa: BLE001
                logger.error(f"Failure alert handler raised for task {task_id}: {alert_error}")

    def on_retry(self, exc, task_id, args, kwargs, einfo):
        super().on_retry(exc, task_id, args, kwargs, einfo)
        celery_tasks_total.labels(task=self.name, state="retry").inc()
        logger.warning(
            f"Task {self.name} [{task_id}] retry {self.request.retries}/{self.max_retries}: {exc}"
        )

    def retry(
        self,
        args=None,
        kwargs=None,
        exc=None,
        throw=True,
        eta=None,
        countdown=None,
        max_retries=None,
        **options,
    ):
        """Exponential backoff: 60s, 120s, 240s ... capped at retry_backoff_max."""
        if countdown is None and eta is None:
            retry_count = self.request.retries
            countdown = min(60 * (2**retry_count), self.retry_backoff_max)
            logger.info(f"Scheduling retry {retry_count + 1}/{self.max_retries} in {countdown}s")
        return super().retry(
            args=args,
            kwargs=kwargs,
            exc=exc,
            throw=throw,
            eta=eta,
            countdown=countdown,
            max_retries=max_retries,
            **options,
        )
