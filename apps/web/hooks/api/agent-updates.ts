'use client';

/**
 * Agent update hooks (agent protocol §14) over lib/api/agent-updates.ts: status polling that
 * speeds up while an update runs, the Update button, rolling updates and the cluster policy.
 * Mutations refresh the node / cluster queries so badges and steps move at once.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import * as api from '@/lib/api/agent-updates';
import { fleetKeys } from '@/hooks/api/fleet';
import { isRunActive, type AgentUpdatePolicy, type AgentUpdateRequest } from '@/types/agent-updates';

export const agentUpdateKeys = {
  all: ['agent-updates'] as const,
  overview: ['agent-updates', 'overview'] as const,
  node: (id: string) => ['agent-updates', 'node', id] as const,
  cluster: (id: string) => ['agent-updates', 'cluster', id] as const,
  runs: (params: object) => ['agent-updates', 'runs', params] as const,
};

/** Fast while something moves (steps show live), slow otherwise. */
const ACTIVE_MS = 2_000;
const IDLE_MS = 15_000;

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'Request failed';
}

export function useAgentUpdateOverview() {
  return useQuery({ queryKey: agentUpdateKeys.overview, queryFn: api.getAgentUpdateOverview, staleTime: 30_000 });
}

export function useNodeAgentUpdate(nodeId: string) {
  return useQuery({
    queryKey: agentUpdateKeys.node(nodeId),
    queryFn: () => api.getNodeAgentUpdate(nodeId),
    enabled: !!nodeId,
    refetchInterval: (q) => (q.state.data?.status === 'updating' || q.state.data?.status === 'staging' ? ACTIVE_MS : IDLE_MS),
  });
}

export function useAgentUpdateRuns(params: { node_id?: string; cluster_id?: string; rollout_id?: string; page_size?: number }, enabled = true) {
  return useQuery({
    queryKey: agentUpdateKeys.runs(params),
    queryFn: () => api.listAgentUpdateRuns(params),
    enabled,
    refetchInterval: (q) => (q.state.data?.items.some((r) => isRunActive(r.state)) ? ACTIVE_MS : IDLE_MS),
  });
}

export function useClusterAgentUpdate(clusterId: string) {
  return useQuery({
    queryKey: agentUpdateKeys.cluster(clusterId),
    queryFn: () => api.getClusterAgentUpdate(clusterId),
    enabled: !!clusterId,
    refetchInterval: (q) => (q.state.data?.rollout?.state === 'running' || (q.state.data?.counts.updating ?? 0) > 0 ? ACTIVE_MS : IDLE_MS),
  });
}

function useRefresh() {
  const qc = useQueryClient();
  return () => {
    qc.invalidateQueries({ queryKey: agentUpdateKeys.all });
    qc.invalidateQueries({ queryKey: fleetKeys.nodes });
    qc.invalidateQueries({ queryKey: fleetKeys.clusters });
  };
}

export function useUpdateNodeAgent() {
  const refresh = useRefresh();
  return useMutation({
    mutationFn: ({ nodeId, body }: { nodeId: string; body?: AgentUpdateRequest }) => api.updateNodeAgent(nodeId, body),
    onSuccess: (run) => {
      refresh();
      toast.success(`Updating the agent to ${run.to_version}`, { description: 'Running models keep serving; the node rolls back by itself if the new agent does not come up.' });
    },
    onError: (error) => toast.error(`Could not start the update: ${errorMessage(error)}`),
  });
}

export function useStageNodeAgentUpdate() {
  const refresh = useRefresh();
  return useMutation({
    mutationFn: ({ nodeId }: { nodeId: string }) => api.stageNodeAgentUpdate(nodeId),
    onSuccess: (out) => {
      refresh();
      toast.success(`Downloading agent ${out.version} to the node`);
    },
    onError: (error) => toast.error(`Could not stage the update: ${errorMessage(error)}`),
  });
}

export function useStartClusterRollout() {
  const refresh = useRefresh();
  return useMutation({
    mutationFn: ({ clusterId }: { clusterId: string }) => api.startClusterAgentRollout(clusterId),
    onSuccess: (r) => {
      refresh();
      toast.success(`Rolling update to ${r.target_version} started`, { description: 'One node at a time; it stops at the first node that rolls back.' });
    },
    onError: (error) => toast.error(`Could not start the rolling update: ${errorMessage(error)}`),
  });
}

export function useCancelRollout() {
  const refresh = useRefresh();
  return useMutation({
    mutationFn: ({ rolloutId }: { rolloutId: string }) => api.cancelAgentRollout(rolloutId),
    onSuccess: () => {
      refresh();
      toast.success('Rolling update cancelled', { description: 'A node already swapping finishes first.' });
    },
    onError: (error) => toast.error(`Could not cancel: ${errorMessage(error)}`),
  });
}

export function useSetAgentUpdatePolicy() {
  const refresh = useRefresh();
  return useMutation({
    mutationFn: ({ clusterId, policy }: { clusterId: string; policy: AgentUpdatePolicy }) => api.setClusterAgentUpdatePolicy(clusterId, policy),
    onSuccess: (policy) => {
      refresh();
      toast.success(policy.mode === 'auto' ? 'Automatic updates in the maintenance window' : 'Agent updates are manual');
    },
    onError: (error) => toast.error(`Could not save the policy: ${errorMessage(error)}`),
  });
}
