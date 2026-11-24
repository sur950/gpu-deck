'use client';

/**
 * Deployment + instance hooks (React Query over lib/api/models.ts).
 * Live lists poll every 10 s; mutations toast and invalidate.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import * as api from '@/lib/api/models';
import type {
  Deployment,
  DeploymentCreate,
  DeploymentListParams,
  DeploymentUpdate,
  InstanceDrainRequest,
  InstanceListParams,
  PlacementPreviewRequest,
  ScaleRequest,
  TrafficWeightsRequest,
} from '@/types/models';

const LIVE_INTERVAL_MS = 10_000;

export const deploymentKeys = {
  all: ['deployments'] as const,
  list: (params?: DeploymentListParams) => ['deployments', 'list', params] as const,
  detail: (id: string) => ['deployments', 'detail', id] as const,
  instances: (id: string) => ['deployments', 'detail', id, 'instances'] as const,
  swaps: (id: string) => ['deployments', 'detail', id, 'swaps'] as const,
  instanceList: (params?: InstanceListParams) => ['instances', 'list', params] as const,
  instance: (id: string) => ['instances', 'detail', id] as const,
  sessions: (id: string) => ['instances', 'detail', id, 'sessions'] as const,
};

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'Request failed';
}

// ─── Queries ──────────────────────────────────────────────────────────────────

export function useDeployments(params: DeploymentListParams = {}, options: { enabled?: boolean; live?: boolean } = {}) {
  const { live = true, ...rest } = options;
  return useQuery({
    queryKey: deploymentKeys.list(params),
    queryFn: () => api.listDeployments(params),
    staleTime: 10_000,
    placeholderData: (prev) => prev,
    refetchInterval: live ? LIVE_INTERVAL_MS : false,
    ...rest,
  });
}

export function useDeployment(id: string) {
  return useQuery({
    queryKey: deploymentKeys.detail(id),
    queryFn: () => api.getDeployment(id),
    enabled: !!id,
    staleTime: 5_000,
    refetchInterval: LIVE_INTERVAL_MS,
  });
}

export function useDeploymentInstances(id: string) {
  return useQuery({
    queryKey: deploymentKeys.instances(id),
    queryFn: () => api.listDeploymentInstances(id),
    enabled: !!id,
    refetchInterval: LIVE_INTERVAL_MS,
  });
}

export function useDeploymentSwaps(id: string) {
  return useQuery({
    queryKey: deploymentKeys.swaps(id),
    queryFn: () => api.listDeploymentSwaps(id, { page_size: 20 }),
    enabled: !!id,
    refetchInterval: LIVE_INTERVAL_MS,
  });
}

export function useInstances(params: InstanceListParams = {}, options: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: deploymentKeys.instanceList(params),
    queryFn: () => api.listInstances(params),
    staleTime: 10_000,
    refetchInterval: LIVE_INTERVAL_MS,
    ...options,
  });
}

export function useInstanceSessions(id: string, enabled = true) {
  return useQuery({
    queryKey: deploymentKeys.sessions(id),
    queryFn: () => api.getInstanceSessions(id),
    enabled: enabled && !!id,
    refetchInterval: 5_000,
  });
}

// ─── Placement preview (wizard) ───────────────────────────────────────────────

export function usePlacementPreview() {
  return useMutation({
    mutationFn: (body: PlacementPreviewRequest) => api.previewPlacement(body),
    onError: (error) => toast.error(`Placement preview failed: ${errorMessage(error)}`),
  });
}

// ─── Mutations ────────────────────────────────────────────────────────────────

export function useCreateDeployment() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: DeploymentCreate) => api.createDeployment(body),
    onSuccess: (deployment, body) => {
      if (body.dry_run) return;
      qc.setQueryData(deploymentKeys.detail(deployment.id), deployment);
      qc.invalidateQueries({ queryKey: deploymentKeys.all });
      toast.success(body.start ? `Deploying "${deployment.name}"` : `Deployment "${deployment.name}" saved (not started)`);
    },
    onError: (error) => toast.error(`Could not create the deployment: ${errorMessage(error)}`),
  });
}

export function useUpdateDeployment() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: DeploymentUpdate }) => api.updateDeployment(id, body),
    onMutate: async ({ id, body }) => {
      await qc.cancelQueries({ queryKey: deploymentKeys.detail(id) });
      const previous = qc.getQueryData<Deployment>(deploymentKeys.detail(id));
      if (previous) qc.setQueryData<Deployment>(deploymentKeys.detail(id), { ...previous, ...body } as Deployment);
      return { previous };
    },
    onError: (error, { id }, ctx) => {
      if (ctx?.previous) qc.setQueryData(deploymentKeys.detail(id), ctx.previous);
      toast.error(`Could not update the deployment: ${errorMessage(error)}`);
    },
    onSuccess: (deployment) => {
      qc.setQueryData(deploymentKeys.detail(deployment.id), deployment);
      toast.success('Deployment updated');
    },
    onSettled: (_d, _e, { id }) => {
      qc.invalidateQueries({ queryKey: deploymentKeys.all });
      qc.invalidateQueries({ queryKey: deploymentKeys.instances(id) });
    },
  });
}

export function useDeleteDeployment() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.deleteDeployment(id),
    onSuccess: (_, id) => {
      qc.removeQueries({ queryKey: deploymentKeys.detail(id) });
      qc.invalidateQueries({ queryKey: deploymentKeys.all });
      toast.success('Deployment deleted — instances are being unloaded');
    },
    onError: (error) => toast.error(`Could not delete the deployment: ${errorMessage(error)}`),
  });
}

/** Shared shape for the start / stop / restart / scale actions. */
function useDeploymentAction<V extends { id: string }>(
  fn: (vars: V) => Promise<unknown>,
  optimisticStatus: Deployment['status'] | null,
  successText: string,
  failText: string,
) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onMutate: async ({ id }) => {
      if (!optimisticStatus) return { previous: undefined };
      await qc.cancelQueries({ queryKey: deploymentKeys.detail(id) });
      const previous = qc.getQueryData<Deployment>(deploymentKeys.detail(id));
      if (previous) qc.setQueryData<Deployment>(deploymentKeys.detail(id), { ...previous, status: optimisticStatus });
      return { previous };
    },
    onError: (error, { id }, ctx) => {
      if (ctx?.previous) qc.setQueryData(deploymentKeys.detail(id), ctx.previous);
      toast.error(`${failText}: ${errorMessage(error)}`);
    },
    onSuccess: () => toast.success(successText),
    onSettled: (_d, _e, { id }) => {
      qc.invalidateQueries({ queryKey: deploymentKeys.detail(id) });
      qc.invalidateQueries({ queryKey: deploymentKeys.instances(id) });
      qc.invalidateQueries({ queryKey: deploymentKeys.swaps(id) });
      qc.invalidateQueries({ queryKey: deploymentKeys.all });
    },
  });
}

export function useScaleDeployment() {
  return useDeploymentAction<{ id: string; body: ScaleRequest }>(
    ({ id, body }) => api.scaleDeployment(id, body),
    'scaling',
    'Scaling — the scheduler is placing the new replica count',
    'Could not scale the deployment',
  );
}

export function useRestartDeployment() {
  return useDeploymentAction<{ id: string }>(
    ({ id }) => api.restartDeployment(id),
    'deploying',
    'Rolling restart started',
    'Could not restart the deployment',
  );
}

export function useStopDeployment() {
  return useDeploymentAction<{ id: string }>(
    ({ id }) => api.stopDeployment(id),
    'stopped',
    'Deployment stopped — instances are unloading',
    'Could not stop the deployment',
  );
}

export function useStartDeployment() {
  return useDeploymentAction<{ id: string }>(
    ({ id }) => api.startDeployment(id),
    'scheduling',
    'Deployment starting',
    'Could not start the deployment',
  );
}

export function useSetTrafficWeights() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: TrafficWeightsRequest }) => api.setTrafficWeights(id, body),
    onMutate: async ({ id, body }) => {
      await qc.cancelQueries({ queryKey: deploymentKeys.detail(id) });
      const previous = qc.getQueryData<Deployment>(deploymentKeys.detail(id));
      if (previous?.instances) {
        qc.setQueryData<Deployment>(deploymentKeys.detail(id), {
          ...previous,
          instances: previous.instances.map((i) => ({ ...i, traffic_weight: body.weights[i.id] ?? i.traffic_weight })),
        });
      }
      return { previous };
    },
    onError: (error, { id }, ctx) => {
      if (ctx?.previous) qc.setQueryData(deploymentKeys.detail(id), ctx.previous);
      toast.error(`Could not update traffic weights: ${errorMessage(error)}`);
    },
    onSuccess: (deployment) => {
      qc.setQueryData(deploymentKeys.detail(deployment.id), deployment);
      toast.success('Traffic weights applied');
    },
    onSettled: (_d, _e, { id }) => {
      qc.invalidateQueries({ queryKey: deploymentKeys.instances(id) });
      qc.invalidateQueries({ queryKey: deploymentKeys.all });
    },
  });
}

// ─── Instance actions ─────────────────────────────────────────────────────────

function useInstanceAction<V extends { id: string; deploymentId?: string }>(
  fn: (vars: V) => Promise<unknown>,
  successText: string,
  failText: string,
) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: () => toast.success(successText),
    onError: (error) => toast.error(`${failText}: ${errorMessage(error)}`),
    onSettled: (_d, _e, { deploymentId }) => {
      if (deploymentId) {
        qc.invalidateQueries({ queryKey: deploymentKeys.detail(deploymentId) });
        qc.invalidateQueries({ queryKey: deploymentKeys.instances(deploymentId) });
      }
      qc.invalidateQueries({ queryKey: ['instances'] });
      qc.invalidateQueries({ queryKey: deploymentKeys.all });
    },
  });
}

export function useRestartInstance() {
  return useInstanceAction<{ id: string; deploymentId?: string }>(
    ({ id }) => api.restartInstance(id),
    'Instance restart requested',
    'Could not restart the instance',
  );
}

export function useDrainInstance() {
  return useInstanceAction<{ id: string; deploymentId?: string; body: InstanceDrainRequest }>(
    ({ id, body }) => api.drainInstance(id, body),
    'Instance draining — no new requests will be routed to it',
    'Could not drain the instance',
  );
}

export function useStopInstance() {
  return useInstanceAction<{ id: string; deploymentId?: string }>(
    ({ id }) => api.stopInstance(id),
    'Instance stopped',
    'Could not stop the instance',
  );
}
