"""
Celery task auto-discovery and the APScheduler job registry.
"""

import pytest
from app.workers.celery_app import celery_app, discover_task_modules, queue_names
from app.workers.scheduler import _REGISTRY, registered_jobs, scheduled_job


@pytest.mark.unit
def test_task_modules_are_discovered_and_routed():
    modules = discover_task_modules()
    assert "app.workers.tasks.system_tasks" in modules
    assert all(m.endswith("_tasks") for m in modules)
    assert celery_app.conf.task_routes["app.workers.tasks.system_tasks.*"] == {"queue": "system"}
    assert "system" in queue_names() and "default" in queue_names()


@pytest.mark.unit
def test_tasks_are_registered_with_celery():
    assert "app.workers.tasks.system_tasks.heartbeat" in celery_app.tasks


@pytest.mark.unit
def test_beat_schedule_is_merged_with_queue_option():
    entry = celery_app.conf.beat_schedule["system-heartbeat"]
    assert entry["task"] == "app.workers.tasks.system_tasks.heartbeat"
    assert entry["options"]["queue"] == "system"


@pytest.mark.unit
def test_redis_key_prefix_applied_to_broker():
    assert celery_app.conf.broker_transport_options["global_keyprefix"].startswith("gpu-deck")
    assert celery_app.conf.result_backend_transport_options["global_keyprefix"].startswith(
        "gpu-deck"
    )


@pytest.mark.unit
def test_scheduled_job_registry():
    ids = {job.job_id for job in registered_jobs()}
    assert "system.scheduler_heartbeat" in ids

    @scheduled_job("test.unique_job", "interval", seconds=5)
    def _job():
        return None

    try:
        job = next(j for j in registered_jobs() if j.job_id == "test.unique_job")
        assert job.trigger == "interval" and job.trigger_kwargs == {"seconds": 5}

        with pytest.raises(RuntimeError):

            @scheduled_job("test.unique_job", "interval", seconds=5)
            def _other():
                return None

    finally:
        _REGISTRY.pop("test.unique_job", None)
