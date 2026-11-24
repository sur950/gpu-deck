"""
Alerts lifecycle: route Celery tasks that exhausted their retries to the
alert channels (``task.failed``, warning) via BaseTaskWithRetry's hook.
"""

from app.core.lifecycle import on_startup
from app.core.logger import get_logger

logger = get_logger(__name__)


def _task_failed(task_name: str, task_id: str, error: str) -> None:
    from app.services.alerts.service import emit_sync

    if task_name.endswith("alerts_tasks.deliver_event"):
        return  # never alert about the alert pipeline itself (loop guard)
    emit_sync(
        "task.failed",
        "warning",
        f"Background task {task_name.rsplit('.', 1)[-1]} failed",
        error,
        resource_type="celery_task",
        payload={"task": task_name, "task_id": task_id},
        dedupe_key=f"task.failed:{task_name}",
    )


@on_startup("worker")
def install_task_failure_alerts() -> None:
    from app.workers.tasks.base_task import set_failure_alert_handler

    set_failure_alert_handler(_task_failed)
    logger.info("alerts: Celery task failure alerts enabled")
