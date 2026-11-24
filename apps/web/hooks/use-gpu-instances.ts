'use client';

/**
 * SSH GPU instance hooks — React Query over lib/api/gpu-instances.ts.
 * Lists and details poll quickly while an instance is moving between states
 * (placing, starting, stopping) and slowly once it settles.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import * as api from '@/lib/api/gpu-instances';
import type { GpuInstance, GpuInstanceCreate, GpuInstanceListParams, GpuInstanceState, GpuInstanceStopRequest, GpuInstanceUpdate } from '@/types/gpu-instances';

export const gpuInstanceKeys = {
  all: ['gpu-instances'] as const,
  list: (params?: GpuInstanceListParams) => ['gpu-instances', 'list', params] as const,
  detail: (id: string) => ['gpu-instances', 'detail', id] as const,
  connection: (id: string) => ['gpu-instances', 'detail', id, 'connection'] as const,
};

const TRANSITIONAL: GpuInstanceState[] = ['requested', 'placing', 'starting', 'stopping'];
const FAST_MS = 2_000;
const SLOW_MS = 15_000;

export function isTransitional(state: GpuInstanceState): boolean {
  return TRANSITIONAL.includes(state);
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'Request failed';
}

export function useGpuInstances(params: GpuInstanceListParams = {}) {
  return useQuery({
    queryKey: gpuInstanceKeys.list(params),
    queryFn: () => api.listGpuInstances(params),
    placeholderData: (prev) => prev,
    refetchInterval: (query) => (query.state.data?.items.some((i) => isTransitional(i.state)) ? FAST_MS : SLOW_MS),
  });
}

export function useGpuInstance(id: string) {
  return useQuery({
    queryKey: gpuInstanceKeys.detail(id),
    queryFn: () => api.getGpuInstance(id),
    enabled: !!id,
    refetchInterval: (query) => (query.state.data && isTransitional(query.state.data.state) ? FAST_MS : SLOW_MS),
  });
}

function useSettle() {
  const qc = useQueryClient();
  return (instance: GpuInstance) => {
    qc.setQueryData(gpuInstanceKeys.detail(instance.id), instance);
    qc.invalidateQueries({ queryKey: gpuInstanceKeys.all });
  };
}

export function useCreateGpuInstance() {
  const settle = useSettle();
  return useMutation({
    mutationFn: (body: GpuInstanceCreate) => api.createGpuInstance(body),
    onSuccess: (instance) => {
      settle(instance);
      toast.success(`Instance "${instance.name}" requested`, { description: 'Placing it on a GPU — connection details appear once sshd is up.' });
    },
    onError: (error) => toast.error(`Could not request the instance: ${errorMessage(error)}`),
  });
}

export function useUpdateGpuInstance() {
  const settle = useSettle();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: GpuInstanceUpdate; successText?: string }) => api.updateGpuInstance(id, body),
    onSuccess: (instance, { successText }) => {
      settle(instance);
      toast.success(successText ?? 'Instance updated');
    },
    onError: (error) => toast.error(`Could not update the instance: ${errorMessage(error)}`),
  });
}

export function useStopGpuInstance() {
  const settle = useSettle();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body?: GpuInstanceStopRequest }) => api.stopGpuInstance(id, body),
    onSuccess: (instance) => {
      settle(instance);
      toast.success(`Stopping "${instance.name}"`, { description: 'Open SSH sessions are closed and the GPU is released.' });
    },
    onError: (error) => toast.error(`Could not stop the instance: ${errorMessage(error)}`),
  });
}

export function useStartGpuInstance() {
  const settle = useSettle();
  return useMutation({
    mutationFn: (id: string) => api.startGpuInstance(id),
    onSuccess: (instance) => {
      settle(instance);
      toast.success(`Starting "${instance.name}"`, { description: 'It gets a fresh placement and a new host port.' });
    },
    onError: (error) => toast.error(`Could not start the instance: ${errorMessage(error)}`),
  });
}

export function useDeleteGpuInstance() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, force }: { id: string; force?: boolean }) => api.deleteGpuInstance(id, force),
    onSuccess: (_, { id }) => {
      qc.removeQueries({ queryKey: gpuInstanceKeys.detail(id) });
      qc.invalidateQueries({ queryKey: gpuInstanceKeys.all });
      toast.success('Instance deleted');
    },
    onError: (error) => toast.error(`Could not delete the instance: ${errorMessage(error)}`),
  });
}
