/**
 * Network latency hooks: live RTT per node (polled), RTT history and the request timing
 * breakdown (stale-while-revalidate like the other metrics hooks).
 */

'use client';

import { useQuery } from '@tanstack/react-query';
import { getLatencyHistory, getLiveLatency, getRequestTiming } from '@/lib/api/latency';
import type { LatencyHistoryParams, RequestTimingParams } from '@/types/latency';

export const latencyKeys = {
  all: ['latency'] as const,
  live: (nodeId?: string) => [...latencyKeys.all, 'live', nodeId ?? null] as const,
  history: (params: LatencyHistoryParams) => [...latencyKeys.all, 'history', params] as const,
  timing: (params: RequestTimingParams) => [...latencyKeys.all, 'timing', params] as const,
};

export function useLiveLatency(nodeId?: string, enabled = true) {
  return useQuery({
    queryKey: latencyKeys.live(nodeId),
    queryFn: () => getLiveLatency(nodeId),
    enabled,
    staleTime: 5_000,
    refetchInterval: 10_000,
    placeholderData: (prev) => prev,
  });
}

export function useLatencyHistory(params: LatencyHistoryParams, enabled = true) {
  return useQuery({
    queryKey: latencyKeys.history(params),
    queryFn: () => getLatencyHistory(params),
    enabled,
    staleTime: 30_000,
    refetchInterval: 60_000,
    placeholderData: (prev) => prev,
  });
}

export function useRequestTiming(params: RequestTimingParams, enabled = true) {
  return useQuery({
    queryKey: latencyKeys.timing(params),
    queryFn: () => getRequestTiming(params),
    enabled,
    staleTime: 30_000,
    refetchInterval: 60_000,
    placeholderData: (prev) => prev,
  });
}
