"""
Benchmark jobs:

* ``run_benchmark`` (Celery, queue ``benchmark``) — executes one run against
  the API layer (``BenchmarkService.execute_run``). Not retried: a partial
  run is recorded as failed instead of being re-executed.
* ``benchmark.fail_stale`` (APScheduler) — runs whose runner died are failed.
"""

from app.core.logger import get_logger
from app.workers.celery_app import celery_app
from app.workers.scheduler import run_async, scheduled_job

logger = get_logger(__name__)

QUEUE = "benchmark"


async def _run(run_id: str) -> str:
    import uuid

    from app.db.postgres.connection import get_session_maker
    from app.services.benchmark.service import BenchmarkService

    async with get_session_maker()() as session:
        run = await BenchmarkService.for_session(session).execute_run(uuid.UUID(run_id))
        await session.commit()
    return run.status.value if run else "skipped"


@celery_app.task(
    name="app.workers.tasks.benchmark_tasks.run_benchmark",
    bind=True,
    acks_late=False,
    soft_time_limit=6 * 3600,
)
def run_benchmark(self, run_id: str) -> str:
    """Execute one benchmark run."""
    return run_async(_run(run_id))


def enqueue_benchmark_run(run_id: str) -> bool:
    """Queue a run; False when the broker is unavailable. Never raises."""
    try:
        run_benchmark.apply_async(kwargs={"run_id": run_id}, queue=QUEUE)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"enqueue_benchmark_run failed: {exc}")
        return False


async def _fail_stale() -> int:
    from app.db.postgres.connection import get_session_maker
    from app.services.benchmark.service import BenchmarkService

    async with get_session_maker()() as session:
        count = await BenchmarkService.for_session(session).fail_stale_runs()
        await session.commit()
    return count


@scheduled_job("benchmark.fail_stale", "interval", minutes=15)
def fail_stale_benchmarks() -> None:
    """Mark runs whose runner stopped reporting as failed."""
    try:
        count = run_async(_fail_stale())
        if count:
            logger.warning(f"marked {count} stale benchmark run(s) failed")
    except Exception as exc:  # noqa: BLE001
        logger.error(f"benchmark stale check failed: {exc}")
