'use client';

/**
 * Benchmark hooks — benchmark definitions, runs, start / auto-tune / cancel
 * and applying a recommendation to the deployment.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import * as api from '@/lib/api/models';
import { deploymentKeys } from '@/hooks/api/models-deployments';
import type {
  ApplyRecommendationRequest,
  BenchmarkCreate,
  BenchmarkListParams,
  BenchmarkRun,
  BenchmarkStartRequest,
  BenchmarkUpdate,
} from '@/types/models';

export const benchmarkKeys = {
  all: ['benchmarks'] as const,
  list: (params?: BenchmarkListParams) => ['benchmarks', 'list', params] as const,
  detail: (id: string) => ['benchmarks', 'detail', id] as const,
  runs: (id: string) => ['benchmarks', 'detail', id, 'runs'] as const,
  run: (runId: string) => ['benchmark-runs', runId] as const,
};

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'Request failed';
}

const isActive = (run: BenchmarkRun | undefined) => run?.status === 'pending' || run?.status === 'running';

export function useBenchmarks(params: BenchmarkListParams = {}, options: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: benchmarkKeys.list(params),
    queryFn: () => api.listBenchmarks(params),
    staleTime: 15_000,
    placeholderData: (prev) => prev,
    refetchInterval: (query) => (query.state.data?.items.some((b) => isActive(b.last_run ?? undefined)) ? 5_000 : false),
    ...options,
  });
}

export function useBenchmark(id: string) {
  return useQuery({
    queryKey: benchmarkKeys.detail(id),
    queryFn: () => api.getBenchmark(id),
    enabled: !!id,
    staleTime: 15_000,
    refetchInterval: (query) => (isActive(query.state.data?.last_run ?? undefined) ? 5_000 : false),
  });
}

export function useBenchmarkRuns(id: string) {
  return useQuery({
    queryKey: benchmarkKeys.runs(id),
    queryFn: () => api.listBenchmarkRuns(id, { page_size: 50 }),
    enabled: !!id,
    staleTime: 5_000,
    refetchInterval: (query) => (query.state.data?.items.some(isActive) ? 5_000 : 30_000),
  });
}

export function useBenchmarkRun(runId: string) {
  return useQuery({
    queryKey: benchmarkKeys.run(runId),
    queryFn: () => api.getBenchmarkRun(runId),
    enabled: !!runId,
    refetchInterval: (query) => (isActive(query.state.data) ? 3_000 : false),
  });
}

export function useCreateBenchmark() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: BenchmarkCreate) => api.createBenchmark(body),
    onSuccess: (benchmark) => {
      qc.setQueryData(benchmarkKeys.detail(benchmark.id), benchmark);
      qc.invalidateQueries({ queryKey: benchmarkKeys.all });
      toast.success(`Benchmark "${benchmark.name}" created`);
    },
    onError: (error) => toast.error(`Could not create the benchmark: ${errorMessage(error)}`),
  });
}

export function useUpdateBenchmark() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: BenchmarkUpdate }) => api.updateBenchmark(id, body),
    onSuccess: (benchmark) => {
      qc.setQueryData(benchmarkKeys.detail(benchmark.id), benchmark);
      qc.invalidateQueries({ queryKey: benchmarkKeys.all });
      toast.success('Benchmark updated');
    },
    onError: (error) => toast.error(`Could not update the benchmark: ${errorMessage(error)}`),
  });
}

export function useDeleteBenchmark() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.deleteBenchmark(id),
    onSuccess: (_, id) => {
      qc.removeQueries({ queryKey: benchmarkKeys.detail(id) });
      qc.invalidateQueries({ queryKey: benchmarkKeys.all });
      toast.success('Benchmark deleted');
    },
    onError: (error) => toast.error(`Could not delete the benchmark: ${errorMessage(error)}`),
  });
}

export function useStartBenchmark() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body, autoTune }: { id: string; body?: BenchmarkStartRequest; autoTune?: boolean }) =>
      autoTune ? api.autoTuneBenchmark(id, body) : api.startBenchmark(id, body),
    onSuccess: (run, { id, autoTune }) => {
      qc.setQueryData(benchmarkKeys.run(run.id), run);
      qc.invalidateQueries({ queryKey: benchmarkKeys.runs(id) });
      qc.invalidateQueries({ queryKey: benchmarkKeys.detail(id) });
      qc.invalidateQueries({ queryKey: benchmarkKeys.all });
      toast.success(autoTune ? 'Auto-tune run started' : 'Benchmark run started');
    },
    onError: (error) => toast.error(`Could not start the run: ${errorMessage(error)}`),
  });
}

export function useCancelBenchmarkRun() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ runId }: { runId: string; benchmarkId: string }) => api.cancelBenchmarkRun(runId),
    onSuccess: (run, { benchmarkId }) => {
      qc.setQueryData(benchmarkKeys.run(run.id), run);
      qc.invalidateQueries({ queryKey: benchmarkKeys.runs(benchmarkId) });
      qc.invalidateQueries({ queryKey: benchmarkKeys.detail(benchmarkId) });
      toast.success('Run cancelled');
    },
    onError: (error) => toast.error(`Could not cancel the run: ${errorMessage(error)}`),
  });
}

export function useApplyRecommendation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ runId, body }: { runId: string; body: ApplyRecommendationRequest }) =>
      api.applyBenchmarkRecommendation(runId, body),
    onSuccess: (deployment) => {
      qc.setQueryData(deploymentKeys.detail(deployment.id), deployment);
      qc.invalidateQueries({ queryKey: deploymentKeys.all });
      toast.success(`Recommendation applied to "${deployment.name}"`);
    },
    onError: (error) => toast.error(`Could not apply the recommendation: ${errorMessage(error)}`),
  });
}
