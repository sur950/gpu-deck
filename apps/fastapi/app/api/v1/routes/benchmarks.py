"""
Benchmark routes (admin.yaml tag "Benchmarks").

GET/POST          /benchmarks
GET/PATCH/DELETE  /benchmarks/{benchmark_id}
POST              /benchmarks/{benchmark_id}/start | auto-tune
GET               /benchmarks/{benchmark_id}/runs
GET               /benchmark-runs/{run_id}
POST              /benchmark-runs/{run_id}/cancel | apply
GET               /benchmark-runs/{run_id}/export   (CSV)
"""

import uuid
from typing import Optional

from app.core.logger import get_logger
from app.schemas.benchmarks_schema import (
    ApplyRecommendationRequest,
    BenchmarkCreate,
    BenchmarkStartRequest,
    BenchmarkUpdate,
)
from app.services.benchmark.service import BenchmarkService
from app.services.catalog.auth import admin_id, get_current_admin
from app.utils.api_wrapper import handle_exceptions, paginated_meta, success_response
from fastapi import APIRouter, Body, Depends, Query, Request, Response, status

logger = get_logger(__name__)

router = APIRouter()
TAGS = ["Benchmarks"]


@router.get("/benchmarks", summary="List benchmarks")
@handle_exceptions
async def list_benchmarks(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    search: Optional[str] = Query(None),
    sort: Optional[str] = Query(None),
    deployment_id: Optional[uuid.UUID] = Query(None),
    admin=Depends(get_current_admin),
    service: BenchmarkService = Depends(BenchmarkService.as_dependency),
):
    rows, total = await service.list_benchmarks(
        page=page,
        page_size=page_size,
        search=search,
        sort=sort,
        deployment_id=deployment_id,
    )
    return success_response(data=rows, meta=paginated_meta(total, page, page_size))


@router.post("/benchmarks", status_code=status.HTTP_201_CREATED, summary="Create a benchmark")
@handle_exceptions
async def create_benchmark(
    request: Request,
    body: BenchmarkCreate,
    admin=Depends(get_current_admin),
    service: BenchmarkService = Depends(BenchmarkService.as_dependency),
):
    return success_response(data=await service.create_benchmark(body, created_by=admin_id(admin)))


@router.get("/benchmarks/{benchmark_id}", summary="Get a benchmark")
@handle_exceptions
async def get_benchmark(
    request: Request,
    benchmark_id: uuid.UUID,
    admin=Depends(get_current_admin),
    service: BenchmarkService = Depends(BenchmarkService.as_dependency),
):
    return success_response(data=await service.get_benchmark(benchmark_id))


@router.patch("/benchmarks/{benchmark_id}", summary="Update a benchmark")
@handle_exceptions
async def update_benchmark(
    request: Request,
    benchmark_id: uuid.UUID,
    body: BenchmarkUpdate,
    admin=Depends(get_current_admin),
    service: BenchmarkService = Depends(BenchmarkService.as_dependency),
):
    return success_response(data=await service.update_benchmark(benchmark_id, body))


@router.delete(
    "/benchmarks/{benchmark_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a benchmark and its runs",
)
@handle_exceptions
async def delete_benchmark(
    request: Request,
    benchmark_id: uuid.UUID,
    admin=Depends(get_current_admin),
    service: BenchmarkService = Depends(BenchmarkService.as_dependency),
):
    await service.delete_benchmark(benchmark_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/benchmarks/{benchmark_id}/start",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start a run",
)
@handle_exceptions
async def start_benchmark(
    request: Request,
    benchmark_id: uuid.UUID,
    body: Optional[BenchmarkStartRequest] = Body(None),
    admin=Depends(get_current_admin),
    service: BenchmarkService = Depends(BenchmarkService.as_dependency),
):
    """The load test runs in the jobs process against the API layer URL."""
    run = await service.start(
        benchmark_id, body or BenchmarkStartRequest(), created_by=admin_id(admin)
    )
    return success_response(data=run)


@router.post(
    "/benchmarks/{benchmark_id}/auto-tune",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start an auto-tune run (concurrency + launch-flag sweep against the SLO)",
)
@handle_exceptions
async def auto_tune_benchmark(
    request: Request,
    benchmark_id: uuid.UUID,
    body: Optional[BenchmarkStartRequest] = Body(None),
    admin=Depends(get_current_admin),
    service: BenchmarkService = Depends(BenchmarkService.as_dependency),
):
    run = await service.start(
        benchmark_id,
        body or BenchmarkStartRequest(),
        force_auto_tune=True,
        created_by=admin_id(admin),
    )
    return success_response(data=run)


@router.get("/benchmarks/{benchmark_id}/runs", summary="Runs of a benchmark")
@handle_exceptions
async def list_benchmark_runs(
    request: Request,
    benchmark_id: uuid.UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    admin=Depends(get_current_admin),
    service: BenchmarkService = Depends(BenchmarkService.as_dependency),
):
    rows, total = await service.list_runs(benchmark_id, page, page_size)
    return success_response(data=rows, meta=paginated_meta(total, page, page_size))


@router.get("/benchmark-runs/{run_id}", summary="Get a run with results")
@handle_exceptions
async def get_benchmark_run(
    request: Request,
    run_id: uuid.UUID,
    admin=Depends(get_current_admin),
    service: BenchmarkService = Depends(BenchmarkService.as_dependency),
):
    return success_response(data=await service.get_run(run_id))


@router.post("/benchmark-runs/{run_id}/cancel", summary="Cancel a running benchmark")
@handle_exceptions
async def cancel_benchmark_run(
    request: Request,
    run_id: uuid.UUID,
    admin=Depends(get_current_admin),
    service: BenchmarkService = Depends(BenchmarkService.as_dependency),
):
    return success_response(data=await service.cancel_run(run_id))


@router.post("/benchmark-runs/{run_id}/apply", summary="Apply the run's recommendation")
@handle_exceptions
async def apply_benchmark_recommendation(
    request: Request,
    run_id: uuid.UUID,
    body: Optional[ApplyRecommendationRequest] = Body(None),
    admin=Depends(get_current_admin),
    service: BenchmarkService = Depends(BenchmarkService.as_dependency),
):
    return success_response(
        data=await service.apply_recommendation(run_id, body or ApplyRecommendationRequest())
    )


@router.get("/benchmark-runs/{run_id}/export", summary="Results as CSV")
@handle_exceptions
async def export_benchmark_run(
    request: Request,
    run_id: uuid.UUID,
    admin=Depends(get_current_admin),
    service: BenchmarkService = Depends(BenchmarkService.as_dependency),
):
    content, filename = await service.export_csv(run_id)
    return Response(
        content=content,
        media_type="text/csv",
        headers={"content-disposition": f'attachment; filename="{filename}"'},
    )
