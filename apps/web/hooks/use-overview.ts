/**
 * Dashboard overview data: the `/metrics/overview` snapshot refreshed every
 * 30 s, merged with the live SSE `metrics` frame so tiles tick between polls,
 * plus the range-scoped series behind the sparklines, the traffic chart and
 * the top-models list.
 */

'use client';

import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { getOverview, mapOverviewLive } from '@/lib/api/overview';
import { getUsageMetrics, getInferenceMetrics, getGpuMetrics } from '@/lib/api/metrics';
import { readOverviewLive, useLiveEvents } from '@/hooks/use-live-events';
import type { LiveEventsState } from '@/hooks/use-live-events';
import type { Overview } from '@/types/overview';
import type { MetricsResult, MetricStep } from '@/types/metrics';

export const overviewKeys = {
  all: ['overview'] as const,
  snapshot: () => [...overviewKeys.all, 'snapshot'] as const,
  trends: (from: string, to: string, step: string) => [...overviewKeys.all, 'trends', from, to, step] as const,
};

export function useOverview() {
  const query = useQuery({
    queryKey: overviewKeys.snapshot(),
    queryFn: getOverview,
    staleTime: 30_000,
    refetchInterval: 30_000,
    placeholderData: (prev) => prev,
  });

  const live = useLiveEvents({ topics: ['metrics'], intervalSeconds: 2 });
  const liveOverview = readOverviewLive(live.latest.metrics);

  const data = useMemo<Overview | undefined>(() => {
    if (!query.data) return undefined;
    if (!liveOverview) return query.data;
    return { ...query.data, live: mapOverviewLive(liveOverview) };
  }, [query.data, liveOverview]);

  return { ...query, data, live: live as LiveEventsState };
}

export interface OverviewTrends {
  /** Per-deployment usage (requests, tokens) at the usage step. */
  usage: MetricsResult | undefined;
  /** Per-instance engine series (sessions, throughput, latency, requests). */
  inference: MetricsResult | undefined;
  /** Per-device GPU series (utilisation, VRAM, power). */
  gpu: MetricsResult | undefined;
  isPending: boolean;
  isFetching: boolean;
  isError: boolean;
}

export const OVERVIEW_ENGINE_METRICS = [
  'active_sessions',
  'waiting_sessions',
  'generation_tps',
  'ttft_ms_p50',
  'ttft_ms_p95',
  'requests_delta',
  'errors_delta',
];

export const OVERVIEW_GPU_METRICS = ['gpu_utilization_pct', 'gpu_memory_used_bytes', 'gpu_power_watts'];

/**
 * Range-scoped series. Entities are fetched un-aggregated and combined in the
 * UI (sum for counters, request-weighted mean for latencies) so the fleet
 * figures are right whatever the fleet size.
 */
export function useOverviewTrends(range: { from: string; to: string; step: MetricStep; usageStep: '1h' | '1d' }): OverviewTrends {
  const usage = useQuery({
    queryKey: [...overviewKeys.trends(range.from, range.to, range.usageStep), 'usage'],
    queryFn: () =>
      getUsageMetrics({
        from: range.from,
        to: range.to,
        step: range.usageStep,
        groupBy: 'deployment',
        metrics: ['requests', 'input_tokens', 'output_tokens', 'cached_tokens'],
      }),
    staleTime: 60_000,
    placeholderData: (prev) => prev,
  });
  const inference = useQuery({
    queryKey: [...overviewKeys.trends(range.from, range.to, range.step), 'inference'],
    queryFn: () => getInferenceMetrics({ from: range.from, to: range.to, step: range.step, aggregate: 'none', metrics: OVERVIEW_ENGINE_METRICS }),
    staleTime: 60_000,
    placeholderData: (prev) => prev,
  });
  const gpu = useQuery({
    queryKey: [...overviewKeys.trends(range.from, range.to, range.step), 'gpu'],
    queryFn: () => getGpuMetrics({ from: range.from, to: range.to, step: range.step, aggregate: 'none', metrics: OVERVIEW_GPU_METRICS }),
    staleTime: 60_000,
    placeholderData: (prev) => prev,
  });
  return {
    usage: usage.data,
    inference: inference.data,
    gpu: gpu.data,
    isPending: usage.isPending || inference.isPending || gpu.isPending,
    isFetching: usage.isFetching || inference.isFetching || gpu.isFetching,
    isError: usage.isError || inference.isError || gpu.isError,
  };
}
