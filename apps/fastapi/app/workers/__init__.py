"""
Background work: Celery (fire-and-forget writes, retries) and APScheduler
(periodic in-process jobs: rules, schedules, rollups, billing).

    celery -A app.workers.celery_app worker --beat --loglevel=info   # worker + beat
    python -m app.workers.scheduler                                   # APScheduler loop
"""
