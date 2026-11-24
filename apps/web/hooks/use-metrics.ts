/**
 * Metrics hooks for the observability screens (React Query key factory,
 * stale-while-revalidate so a range change keeps the previous chart on
 * screen at reduced opacity instead of flashing a skeleton).
 */

'use client';

import { useQuery } from '@tanstack/react-query';
import {
  getDeployments,
  getGpuMetrics,
  getInferenceMetrics,
  getInstance,
  getInstanceSessions,
  getInstances,
  getLiveSessions,
  getSessionMetrics,
} from '@/lib/api/metrics';
import type { GpuMetricsParams, InferenceMetricsParams, SessionMetricsParams } from '@/types/metrics';

export const metricsKeys = {
  all: ['metrics'] as const,
  gpu: (params: GpuMetricsParams) => [...metricsKeys.all, 'gpu', params] as const,
  inference: (params: InferenceMetricsParams) => [...metricsKeys.all, 'inference', params] as const,
  sessions: (params: SessionMetricsParams) => [...metricsKeys.all, 'sessions', params] as const,
  liveSessions: (scope: Record<string, string | undefined>) => [...metricsKeys.all, 'live-sessions', scope] as const,
  instances: (scope: Record<string, string | undefined>) => [...metricsKeys.all, 'instances', scope] as const,
  instance: (id: string) => [...metricsKeys.all, 'instance', id] as const,
  instanceSessions: (id: string) => [...metricsKeys.all, 'instance-sessions', id] as const,
  deployments: () => [...metricsKeys.all, 'deployments'] as const,
};

export function useGpuMetrics(params: GpuMetricsParams, enabled = true) {
  return useQuery({
    queryKey: metricsKeys.gpu(params),
    queryFn: () => getGpuMetrics(params),
    enabled,
    staleTime: 30_000,
    refetchInterval: 60_000,
    placeholderData: (prev) => prev,
  });
}

export function useInferenceMetrics(params: InferenceMetricsParams, enabled = true) {
  return useQuery({
    queryKey: metricsKeys.inference(params),
    queryFn: () => getInferenceMetrics(params),
    enabled,
    staleTime: 30_000,
    refetchInterval: 60_000,
    placeholderData: (prev) => prev,
  });
}

export function useSessionMetrics(params: SessionMetricsParams, enabled = true) {
  return useQuery({
    queryKey: metricsKeys.sessions(params),
    queryFn: () => getSessionMetrics(params),
    enabled: enabled && Boolean(params.instanceId),
    staleTime: 15_000,
    refetchInterval: 30_000,
    placeholderData: (prev) => prev,
  });
}

export function useLiveSessions(scope: { deploymentId?: string; nodeId?: string; instanceId?: string } = {}) {
  return useQuery({
    queryKey: metricsKeys.liveSessions(scope),
    queryFn: () => getLiveSessions(scope),
    staleTime: 5_000,
    refetchInterval: 5_000,
    placeholderData: (prev) => prev,
  });
}

export function useInstances(scope: { deploymentId?: string; nodeId?: string; state?: string } = {}) {
  return useQuery({
    queryKey: metricsKeys.instances(scope),
    queryFn: () => getInstances(scope),
    staleTime: 15_000,
    refetchInterval: 30_000,
    placeholderData: (prev) => prev,
  });
}

export function useInstance(instanceId: string | null) {
  return useQuery({
    queryKey: metricsKeys.instance(instanceId ?? ''),
    queryFn: () => getInstance(instanceId as string),
    enabled: Boolean(instanceId),
    staleTime: 15_000,
  });
}

export function useInstanceSessions(instanceId: string | null) {
  return useQuery({
    queryKey: metricsKeys.instanceSessions(instanceId ?? ''),
    queryFn: () => getInstanceSessions(instanceId as string),
    enabled: Boolean(instanceId),
    staleTime: 4_000,
    refetchInterval: 4_000,
    placeholderData: (prev) => prev,
  });
}

export function useDeployments() {
  return useQuery({
    queryKey: metricsKeys.deployments(),
    queryFn: getDeployments,
    staleTime: 15_000,
    refetchInterval: 30_000,
    placeholderData: (prev) => prev,
  });
}
