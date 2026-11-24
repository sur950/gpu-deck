"""
Benchmark service: benchmark definitions, runs, the load-test runner (jobs
process, Celery queue ``benchmark``), auto-tune and applying a recommendation.

Runner (``execute_run``): for each launch-flag variant (auto-tune) and each
concurrency level, drive the serving API at ``BENCHMARK_TARGET_URL`` (default
``PUBLIC_SERVING_URL``) with the deployment's serving name, record TTFT /
e2e / TPOT percentiles and throughput, check the SLO, store results after
every level (progress), and finish with ``best_concurrency``, ``slo_met`` and
a recommendation. Auto-tune applies each variant as a rolling update of the
deployment's launch flags, waits for the fresh instance to be ready, sweeps,
and restores the original flags at the end (``apply`` makes a variant
permanent). Cancel is honoured between requests.
"""

from __future__ import annotations

import asyncio
import csv
import io
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx
from app.core.exceptions import ConflictError, ResourceNotFoundError, ValidationError
from app.core.logger import get_logger
from app.core.settings import settings
from app.db.postgres.connection import get_db
from app.db.postgres.models.generated_models import BenchmarkRuns, Benchmarks
from app.db.postgres.repositories.benchmark_repository import (
    BenchmarkRepository,
    BenchmarkRunRepository,
)
from app.schemas.benchmarks_schema import (
    ApplyRecommendationRequest,
    Benchmark,
    BenchmarkCreate,
    BenchmarkRun,
    BenchmarkStartRequest,
    BenchmarkUpdate,
)
from app.schemas.deployments_schema import Deployment, DeploymentUpdate
from app.services.benchmark import stats
from app.services.benchmark.loadgen import LoadGenerator, endpoint_for
from app.services.catalog.secrets import resolve_credential_value
from app.services.deployments.external import ensure_managed, is_external
from app.services.deployments.service import DeploymentService, spec_hash
from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

AUTO_TUNE_IDLE_SECONDS = 300
CSV_COLUMNS = (
    "variant",
    "concurrency",
    "requests",
    "errors",
    "rps",
    "generation_tps",
    "prompt_tps",
    "ttft_ms_p50",
    "ttft_ms_p95",
    "ttft_ms_p99",
    "e2e_ms_p50",
    "e2e_ms_p95",
    "e2e_ms_p99",
    "tpot_ms_p50",
    "tpot_ms_p95",
    "slo_met",
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class BenchmarkService:
    """Benchmarks + runs + runner."""

    def __init__(
        self,
        session: AsyncSession,
        benchmarks: BenchmarkRepository,
        runs: BenchmarkRunRepository,
        deployments: DeploymentService,
    ) -> None:
        self.session = session
        self.benchmarks = benchmarks
        self.runs = runs
        self.deployments = deployments

    @staticmethod
    async def as_dependency(
        request: Request, db: AsyncSession = Depends(get_db)
    ) -> "BenchmarkService":
        return BenchmarkService.for_session(db)

    @classmethod
    def for_session(
        cls, db: AsyncSession, deployments: Optional[DeploymentService] = None
    ) -> "BenchmarkService":
        return cls(
            db,
            BenchmarkRepository(db),
            BenchmarkRunRepository(db),
            deployments or DeploymentService.for_session(db),
        )

    # ============================================================ CRUD

    async def list_benchmarks(self, **filters: Any) -> Tuple[List[Benchmark], int]:
        rows, total = await self.benchmarks.list_benchmarks(**filters)
        return await self._benchmarks_out(rows), total

    async def get_benchmark(self, benchmark_id: uuid.UUID) -> Benchmark:
        return (await self._benchmarks_out([await self._benchmark_or_404(benchmark_id)]))[0]

    async def create_benchmark(
        self, data: BenchmarkCreate, created_by: Optional[uuid.UUID] = None
    ) -> Benchmark:
        if await self.benchmarks.get_by_name(data.name):
            raise ConflictError(f"A benchmark named {data.name!r} already exists")
        dep = await self.deployments.deployments.get_active(data.deployment_id)
        if dep is None:
            raise ResourceNotFoundError(resource_type="Deployment")
        ensure_managed(dep)  # observed (external) servers are never benchmarked
        ctx = await self.deployments.context(dep)
        _check_model_type(ctx.model.type)
        _check_levels(data.concurrency_levels)
        row = await self.benchmarks.create(
            name=data.name,
            description=data.description,
            deployment_id=data.deployment_id,
            target_slo=data.target_slo,
            concurrency_levels=data.concurrency_levels,
            workload=data.workload,
            duration_seconds=data.duration_seconds,
            auto_tune=data.auto_tune,
            created_by=created_by,
        )
        return await self.get_benchmark(row.id)

    async def update_benchmark(self, benchmark_id: uuid.UUID, data: BenchmarkUpdate) -> Benchmark:
        row = await self._benchmark_or_404(benchmark_id)
        changes = data.model_dump(exclude_unset=True)
        if "name" in changes and changes["name"].lower() != row.name.lower():
            if await self.benchmarks.get_by_name(changes["name"]):
                raise ConflictError(f"A benchmark named {changes['name']!r} already exists")
        if changes.get("concurrency_levels"):
            _check_levels(changes["concurrency_levels"])
        if changes.get("workload") is not None:
            changes["workload"] = {**(row.workload or {}), **changes["workload"]}
        await self.benchmarks.update(
            row.id,
            **{k: v for k, v in changes.items() if v is not None or k == "description"},
        )
        return await self.get_benchmark(row.id)

    async def delete_benchmark(self, benchmark_id: uuid.UUID) -> None:
        row = await self._benchmark_or_404(benchmark_id)
        if await self.runs.active_for_benchmark(row.id):
            raise ConflictError("A run of this benchmark is in progress; cancel it first")
        await self.benchmarks.soft_delete(row.id)

    async def list_runs(
        self, benchmark_id: uuid.UUID, page: int, page_size: int
    ) -> Tuple[List[BenchmarkRun], int]:
        bench = await self._benchmark_or_404(benchmark_id)
        rows, total = await self.runs.list_for_benchmark(bench.id, page, page_size)
        return [_run_out(r, bench.name) for r in rows], total

    async def get_run(self, run_id: uuid.UUID) -> BenchmarkRun:
        run = await self._run_or_404(run_id)
        bench = await self.benchmarks.get_by_id(run.benchmark_id)
        return _run_out(run, bench.name if bench else None)

    # ============================================================ runs

    async def start(
        self,
        benchmark_id: uuid.UUID,
        req: BenchmarkStartRequest,
        *,
        force_auto_tune: bool = False,
        created_by: Optional[uuid.UUID] = None,
        enqueue: bool = True,
    ) -> BenchmarkRun:
        bench = await self._benchmark_or_404(benchmark_id)
        dep = await self.deployments.deployments.get_active(bench.deployment_id)
        if dep is None:
            raise ConflictError("The benchmark's deployment no longer exists")
        ensure_managed(dep)
        if await self.runs.active_for_deployment(dep.id):
            raise ConflictError("Another benchmark run is active on this deployment")
        auto_tune = (
            True
            if force_auto_tune
            else (req.auto_tune if req.auto_tune is not None else bench.auto_tune)
        )
        ctx = await self.deployments.context(dep)
        _check_model_type(ctx.model.type)
        instance = await self._pick_instance(dep.id, req.instance_id)
        variants: List[Dict[str, Any]] = list(req.flag_variants or [])
        if auto_tune:
            if not variants:
                variants = stats.default_variants(ctx.engine.name, bench.concurrency_levels or [])
            if not variants:
                raise ValidationError(
                    f"No launch-flag variants to auto-tune for {ctx.engine.name}; "
                    "pass flag_variants"
                )
            if dep.last_request_at and utcnow() - dep.last_request_at < timedelta(
                seconds=AUTO_TUNE_IDLE_SECONDS
            ):
                raise ConflictError(
                    "Auto-tune restarts instances with different launch flags; the deployment "
                    f"served traffic in the last {AUTO_TUNE_IDLE_SECONDS // 60} minutes"
                )
        run = await self.runs.create(
            benchmark_id=bench.id,
            instance_id=instance.id,
            node_id=instance.node_id,
            status="pending",
            progress_pct=0,
            results=[],
            recommendation=(
                {"auto_tune": bool(auto_tune), "flag_variants": variants[:8]} if auto_tune else {}
            ),
            created_by=created_by,
        )
        _audit(
            "benchmark.started",
            resource_id=bench.id,
            resource_name=bench.name,
            auto_tune=bool(auto_tune),
        )
        if enqueue:
            await self.runs.release_connection()
            if not _enqueue(run.id):
                await self.runs.set_fields(
                    run.id,
                    status="failed",
                    error_message="jobs queue unavailable",
                    finished_at=utcnow(),
                )
        return await self.get_run(run.id)

    async def cancel_run(self, run_id: uuid.UUID) -> BenchmarkRun:
        run = await self._run_or_404(run_id)
        if run.status not in ("pending", "running"):
            raise ConflictError(f"Run is {run.status}")
        await self.runs.set_fields(
            run.id,
            status="cancelled",
            finished_at=utcnow(),
            error_message="cancelled by admin",
        )
        return await self.get_run(run.id)

    async def apply_recommendation(
        self, run_id: uuid.UUID, req: ApplyRecommendationRequest
    ) -> Deployment:
        run = await self._run_or_404(run_id)
        if run.status != "completed":
            raise ConflictError("Only completed runs have a recommendation")
        rec = run.recommendation or {}
        if rec.get("concurrency") is None:
            raise ConflictError("The run found no configuration meeting the SLO")
        bench = await self.benchmarks.get_by_id(run.benchmark_id)
        if bench is None:
            raise ResourceNotFoundError(resource_type="Benchmark")
        dep = await self.deployments.deployments.get_active(bench.deployment_id)
        if dep is None:
            raise ResourceNotFoundError(resource_type="Deployment")
        ensure_managed(dep)
        update: Dict[str, Any] = {}
        if req.apply_launch_flags and rec.get("launch_flags") is not None:
            update["launch_flags"] = {
                **(dep.launch_flags or {}),
                **(rec.get("launch_flags") or {}),
            }
        if req.apply_replicas and rec.get("replicas"):
            update["replicas"] = int(rec["replicas"])
            update["max_replicas"] = max(int(dep.max_replicas), int(rec["replicas"]))
        if req.apply_traffic_config and rec.get("per_instance_concurrency"):
            update["traffic_config"] = {
                **(dep.traffic_config or {}),
                "max_concurrent_per_instance": int(rec["per_instance_concurrency"]),
            }
        if not update:
            return await self.deployments.get_deployment(dep.id)
        _audit(
            "benchmark.recommendation_applied",
            resource_id=bench.id,
            resource_name=bench.name,
            applied=list(update),
        )
        return await self.deployments.update_deployment(dep.id, DeploymentUpdate(**update))

    async def export_csv(self, run_id: uuid.UUID) -> Tuple[str, str]:
        run = await self._run_or_404(run_id)
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(CSV_COLUMNS)
        for r in run.results or []:
            writer.writerow(
                [
                    (stats._flags_key(r.get("launch_flags")) if r.get("launch_flags") else ""),
                    r.get("concurrency"),
                    r.get("requests"),
                    r.get("errors"),
                    r.get("rps"),
                    r.get("generation_tps"),
                    r.get("prompt_tps"),
                    (r.get("ttft_ms") or {}).get("p50"),
                    (r.get("ttft_ms") or {}).get("p95"),
                    (r.get("ttft_ms") or {}).get("p99"),
                    (r.get("e2e_ms") or {}).get("p50"),
                    (r.get("e2e_ms") or {}).get("p95"),
                    (r.get("e2e_ms") or {}).get("p99"),
                    (r.get("tpot_ms") or {}).get("p50"),
                    (r.get("tpot_ms") or {}).get("p95"),
                    r.get("slo_met"),
                ]
            )
        return buf.getvalue(), f"benchmark-run-{run.id}.csv"

    # ============================================================ runner

    async def execute_run(
        self,
        run_id: uuid.UUID,
        *,
        transport: Optional[httpx.AsyncBaseTransport] = None,
        poll_seconds: float = 5.0,
    ) -> Optional[BenchmarkRun]:
        """Run a pending benchmark run to completion (jobs process)."""
        run = await self.runs.get_by_id(run_id)
        if run is None or run.status != "pending":
            return None
        bench = await self.benchmarks.get_by_id(run.benchmark_id)
        dep = await self.deployments.deployments.get_active(bench.deployment_id) if bench else None
        if dep is not None and is_external(dep):
            dep = None  # never run load against an observed server
        if bench is None or dep is None:
            await self._finish(run.id, "failed", error="benchmark or deployment was deleted")
            return await self.get_run(run.id)
        await self.runs.set_fields(run.id, status="running", started_at=utcnow(), progress_pct=0)
        await self.session.commit()
        run_id = run.id
        dep_id = dep.id
        original_flags = dict(dep.launch_flags or {})
        flags_changed = False
        results: List[Dict[str, Any]] = []
        try:
            ctx = await self.deployments.context(dep)
            url, key = await self._target()
            gen = LoadGenerator(
                url,
                key,
                ctx.model.name,
                ctx.model.type,
                timeout_seconds=settings.BENCHMARK_REQUEST_TIMEOUT_SECONDS,
                transport=transport,
                verify=bool(getattr(settings, "BENCHMARK_VERIFY_TLS", True)),
            )
            levels = [
                min(int(c), settings.BENCHMARK_MAX_CONCURRENCY)
                for c in (bench.concurrency_levels or [1])
            ]
            levels = sorted(set(levels))
            tune = (run.recommendation or {}).get("auto_tune")
            variants: List[Optional[Dict[str, Any]]] = [None]
            if tune:
                variants = list((run.recommendation or {}).get("flag_variants") or [])[:8]
            steps = max(len(levels) * len(variants), 1)
            done = 0
            workload = dict(bench.workload or {})
            slo = dict(bench.target_slo or {})
            for variant in variants:
                if variant is not None:
                    await self.deployments.update_deployment(
                        dep.id,
                        DeploymentUpdate(launch_flags={**original_flags, **variant}),
                    )
                    flags_changed = True
                    await self.session.commit()
                    await self._wait_rolled(dep.id, poll_seconds)
                for level in levels:
                    if await self._cancelled(run.id):
                        return await self.get_run(run.id)
                    level_result = await gen.run_level(
                        level,
                        workload,
                        time_budget_seconds=float(bench.duration_seconds),
                        should_stop=lambda: self._cancelled(run.id),
                        launch_flags=dict(variant) if variant is not None else None,
                    )
                    results.append(stats.summarize(level_result, slo))
                    done += 1
                    await self.runs.set_fields(
                        run.id,
                        results=results,
                        progress_pct=round(100.0 * done / steps, 1),
                    )
                    await self.session.commit()
            if await self._cancelled(run.id):
                return await self.get_run(run.id)
            ready = [i for i in await self.deployments.instances.for_deployment(dep.id, ("ready",))]
            best = stats.best_concurrency(results)
            rec = stats.recommend(
                results,
                current_replicas=int(dep.replicas),
                ready_replicas=len(ready),
                slo=slo,
                base_flags=original_flags,
            )
            rec["auto_tune"] = bool(tune)
            await self._finish(
                run.id,
                "completed",
                results=results,
                best=best,
                slo_met=best is not None,
                recommendation=rec,
            )
        except Exception as exc:  # noqa: BLE001 - record any failure on the run
            logger.exception(f"benchmark run {run_id} failed: {exc}")
            await self.session.rollback()
            await self._finish(
                run_id,
                "failed",
                results=results,
                error=f"{type(exc).__name__}: {exc}"[:2048],
            )
        finally:
            if flags_changed:
                try:
                    await self.deployments.update_deployment(
                        dep_id, DeploymentUpdate(launch_flags=original_flags)
                    )
                    await self.session.commit()
                except Exception as exc:  # noqa: BLE001
                    logger.error(f"benchmark run {run_id}: restoring launch flags failed: {exc}")
        return await self.get_run(run_id)

    async def _finish(
        self,
        run_id: uuid.UUID,
        status: str,
        *,
        results: Optional[List[Dict[str, Any]]] = None,
        best: Optional[int] = None,
        slo_met: Optional[bool] = None,
        recommendation: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
    ) -> None:
        values: Dict[str, Any] = {
            "status": status,
            "finished_at": utcnow(),
            "error_message": error,
        }
        if results is not None:
            values["results"] = results
        if status == "completed":
            values.update(
                progress_pct=100.0,
                best_concurrency=best,
                slo_met=slo_met,
                recommendation=recommendation or {},
            )
        await self.runs.set_fields(run_id, **values)
        await self.session.commit()

    async def _cancelled(self, run_id: uuid.UUID) -> bool:
        """Cancel flag from the DB (workers share the session: serialized and throttled)."""
        lock = getattr(self, "_cancel_lock", None)
        if lock is None:
            lock = self._cancel_lock = asyncio.Lock()
        async with lock:
            now = asyncio.get_running_loop().time()
            cached = getattr(self, "_cancel_cache", None)
            if cached is not None and cached[0] == run_id and now - cached[1] < 1.0:
                return cached[2]
            await self.session.commit()
            cancelled = await self.runs.status_of(run_id) == "cancelled"
            self._cancel_cache = (run_id, now, cancelled)
            return cancelled

    async def _wait_rolled(self, deployment_id: uuid.UUID, poll_seconds: float) -> None:
        deadline = utcnow() + timedelta(seconds=settings.BENCHMARK_READY_WAIT_SECONDS)
        while True:
            await self.session.commit()
            # Sessions use expire_on_commit=False: without this the identity map keeps the
            # first state it saw (e.g. the new instance as "loading") and the wait never ends.
            # Only instance rows are expired (they are re-read right below); expiring
            # everything would lazy-load other objects outside a greenlet.
            for obj in list(self.session.identity_map.values()):
                if type(obj).__name__ == "Instances":
                    self.session.expire(obj)
            dep = await self.deployments.deployments.get_active(deployment_id)
            if dep is None:
                raise RuntimeError("deployment deleted during auto-tune")
            want = spec_hash(dep)
            live = await self.deployments.instances.for_deployment(
                dep.id, ("pending", "loading", "ready")
            )
            fresh_ready = [
                i
                for i in live
                if i.state == "ready" and (i.resources or {}).get("spec_hash") == want
            ]
            stale = [i for i in live if (i.resources or {}).get("spec_hash") != want]
            if fresh_ready and not stale:
                return
            if utcnow() > deadline:
                raise TimeoutError(
                    "instances did not become ready with the new launch flags in time"
                )
            await asyncio.sleep(poll_seconds)

    async def _target(self) -> Tuple[str, Optional[str]]:
        url = settings.BENCHMARK_TARGET_URL or settings.PUBLIC_SERVING_URL
        if not url:
            raise ValidationError("BENCHMARK_TARGET_URL / PUBLIC_SERVING_URL is not set")
        key = await resolve_credential_value(self.session, settings.BENCHMARK_API_KEY_CREDENTIAL)
        key = key or settings.BENCHMARK_API_KEY
        if not key:
            raise ValidationError(
                f"No API key for benchmarks: store one in the vault as "
                f"{settings.BENCHMARK_API_KEY_CREDENTIAL!r} or set BENCHMARK_API_KEY"
            )
        return url, key

    async def _pick_instance(self, deployment_id: uuid.UUID, instance_id: Optional[uuid.UUID]):
        ready = await self.deployments.instances.for_deployment(deployment_id, ("ready",))
        if instance_id is not None:
            for inst in ready:
                if inst.id == instance_id:
                    return inst
            raise ConflictError("The instance is not a ready instance of this deployment")
        if not ready:
            raise ConflictError("The deployment has no ready instance; start it first")
        return sorted(
            ready,
            key=lambda i: (
                i.last_request_at or datetime.min.replace(tzinfo=timezone.utc),
                i.created_at,
            ),
        )[0]

    async def fail_stale_runs(self) -> int:
        cutoff = utcnow() - timedelta(minutes=int(settings.BENCHMARK_STALE_RUN_MINUTES))
        rows = await self.runs.stale_running(cutoff)
        for r in rows:
            await self.runs.set_fields(
                r.id,
                status="failed",
                finished_at=utcnow(),
                error_message="runner stopped reporting progress",
            )
        return len(rows)

    # ============================================================ output

    async def _benchmarks_out(self, rows: List[Benchmarks]) -> List[Benchmark]:
        ids = [r.id for r in rows]
        counts, last = await self.runs.counts_and_last(ids)
        names = await self.benchmarks.deployment_names([r.deployment_id for r in rows])
        return [
            Benchmark(
                id=r.id,
                name=r.name,
                description=r.description,
                deployment_id=r.deployment_id,
                deployment_name=names.get(r.deployment_id),
                target_slo=r.target_slo or {},
                concurrency_levels=list(r.concurrency_levels or []),
                workload=r.workload or {},
                duration_seconds=int(r.duration_seconds),
                auto_tune=bool(r.auto_tune),
                last_run=_run_out(last[r.id], r.name) if r.id in last else None,
                run_count=counts.get(r.id, 0),
                created_at=r.created_at,
                updated_at=r.updated_at,
            )
            for r in rows
        ]

    async def _benchmark_or_404(self, benchmark_id: uuid.UUID) -> Benchmarks:
        row = await self.benchmarks.get_active(benchmark_id)
        if row is None:
            raise ResourceNotFoundError(resource_type="Benchmark")
        return row

    async def _run_or_404(self, run_id: uuid.UUID) -> BenchmarkRuns:
        row = await self.runs.get_by_id(run_id)
        if row is None:
            raise ResourceNotFoundError(resource_type="Benchmark run")
        return row


def _run_out(r: BenchmarkRuns, name: Optional[str]) -> BenchmarkRun:
    return BenchmarkRun(
        id=r.id,
        benchmark_id=r.benchmark_id,
        benchmark_name=name,
        instance_id=r.instance_id,
        node_id=r.node_id,
        status=r.status,
        progress_pct=float(r.progress_pct or 0),
        results=list(r.results or []),
        best_concurrency=r.best_concurrency,
        slo_met=r.slo_met,
        recommendation=r.recommendation or {},
        error_message=r.error_message,
        started_at=r.started_at,
        finished_at=r.finished_at,
        created_at=r.created_at,
    )


def _check_model_type(model_type: str) -> None:
    try:
        endpoint_for(model_type)
    except ValueError as exc:
        raise ValidationError(str(exc))


def _check_levels(levels: List[int]) -> None:
    too_big = [c for c in levels if c > settings.BENCHMARK_MAX_CONCURRENCY]
    if too_big:
        raise ValidationError(
            "concurrency levels above BENCHMARK_MAX_CONCURRENCY "
            f"({settings.BENCHMARK_MAX_CONCURRENCY}): {too_big}"
        )


def _enqueue(run_id: uuid.UUID) -> bool:
    try:
        from app.workers.tasks.benchmark_tasks import enqueue_benchmark_run

        return enqueue_benchmark_run(str(run_id))
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"benchmark enqueue failed for {run_id}: {exc}")
        return False


def _audit(
    event_type: str, resource_id: Any, resource_name: Optional[str], **metadata: Any
) -> None:
    try:
        from app.services.audit import audit

        audit.record(
            event_type,
            resource_type="benchmark",
            resource_id=resource_id,
            resource_name=resource_name,
            metadata=metadata,
        )
    except Exception:  # noqa: BLE001
        pass
