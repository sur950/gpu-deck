'use client';

/**
 * Cloud hooks — React Query over lib/api/cloud.ts. Catalog reads (regions,
 * instance types, quotas) are cached server-side too, so they stay fresh for
 * minutes; VM lists poll fast while anything is provisioning / draining.
 */

import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import * as api from '@/lib/api/cloud';
import type { CloudAccountRef } from '@/lib/api/cloud';
import { ApiError } from '@/lib/api/client';
import type { CloudNode, CloudNodeAction, CloudNodeCreate, CloudNodeListParams, CloudNodeStatus, CloudNodeUpdate, CloudProviderId, InstanceTypeFilters } from '@/types/cloud';

export const cloudKeys = {
  all: ['cloud'] as const,
  providers: ['cloud', 'providers'] as const,
  regions: (provider: string, account: CloudAccountRef) => ['cloud', 'regions', provider, account] as const,
  types: (provider: string, region: string, account: CloudAccountRef, filters: InstanceTypeFilters) => ['cloud', 'types', provider, region, account, filters] as const,
  quotas: (provider: string, region: string, account: CloudAccountRef) => ['cloud', 'quotas', provider, region, account] as const,
  nodes: ['cloud', 'nodes'] as const,
  nodeList: (params?: CloudNodeListParams) => ['cloud', 'nodes', 'list', params] as const,
  node: (id: string) => ['cloud', 'nodes', 'detail', id] as const,
};

const MOVING: CloudNodeStatus[] = ['pending', 'provisioning', 'booting', 'draining', 'stopping', 'starting', 'terminating'];

export function isMoving(status: CloudNodeStatus): boolean {
  return MOVING.includes(status);
}

/** Toast text for provider failures: the stable code tells quota from capacity from auth. */
export function cloudErrorMessage(error: unknown): string {
  if (error instanceof ApiError && error.code) {
    const hint: Record<string, string> = {
      CLOUD_QUOTA_EXCEEDED: 'Provider quota exceeded — request a quota increase or pick a smaller type.',
      CLOUD_CAPACITY_UNAVAILABLE: 'No capacity for this type right now — try another zone or region.',
      CLOUD_AUTH_FAILED: 'The provider rejected the credential.',
      CLOUD_CREDENTIAL_INVALID: 'The vault credential is not in the provider format.',
      CLOUD_MAX_NODES: 'The cluster is at its max_nodes cap.',
    };
    return hint[error.code] ? `${hint[error.code]} (${error.message})` : error.message;
  }
  return error instanceof Error ? error.message : 'Request failed';
}

export function useCloudProviders() {
  return useQuery({ queryKey: cloudKeys.providers, queryFn: api.listCloudProviders, staleTime: 10 * 60_000 });
}

function hasAccount(account: CloudAccountRef): boolean {
  return !!(account.cluster_id || account.credential_id);
}

export function useCloudRegions(provider: CloudProviderId | null, account: CloudAccountRef) {
  return useQuery({
    queryKey: cloudKeys.regions(provider ?? '', account),
    queryFn: () => api.listCloudRegions(provider as CloudProviderId, account),
    enabled: !!provider && hasAccount(account),
    staleTime: 10 * 60_000,
    retry: false,
  });
}

export function useCloudInstanceTypes(provider: CloudProviderId | null, region: string, account: CloudAccountRef, filters: InstanceTypeFilters) {
  return useQuery({
    queryKey: cloudKeys.types(provider ?? '', region, account, filters),
    queryFn: () => api.listCloudInstanceTypes(provider as CloudProviderId, region, account, filters),
    enabled: !!provider && !!region && hasAccount(account),
    staleTime: 10 * 60_000,
    placeholderData: keepPreviousData,
    retry: false,
  });
}

export function useCloudQuotas(provider: CloudProviderId | null, region: string, account: CloudAccountRef) {
  return useQuery({
    queryKey: cloudKeys.quotas(provider ?? '', region, account),
    queryFn: () => api.listCloudQuotas(provider as CloudProviderId, region, account),
    enabled: !!provider && !!region && hasAccount(account),
    staleTime: 5 * 60_000,
    retry: false,
  });
}

export function useCheckCloudCredential() {
  return useMutation({
    mutationFn: ({ provider, credentialId }: { provider: CloudProviderId; credentialId: string }) => api.checkCloudCredential(provider, credentialId),
    onError: (error) => toast.error(`Credential check failed: ${cloudErrorMessage(error)}`),
  });
}

export function useCloudNodes(params: CloudNodeListParams = {}) {
  return useQuery({
    queryKey: cloudKeys.nodeList(params),
    queryFn: () => api.listCloudNodes(params),
    placeholderData: (prev) => prev,
    refetchInterval: (query) => (query.state.data?.items.some((n) => isMoving(n.status)) ? 3_000 : 20_000),
  });
}

export function useCloudNode(id: string) {
  return useQuery({
    queryKey: cloudKeys.node(id),
    queryFn: () => api.getCloudNode(id),
    enabled: !!id,
    refetchInterval: (query) => (query.state.data && isMoving(query.state.data.status) ? 3_000 : 20_000),
  });
}

export function usePreviewCloudNodes(body: CloudNodeCreate | null) {
  return useQuery({
    queryKey: ['cloud', 'preview', body],
    queryFn: () => api.previewCloudNodes(body as CloudNodeCreate),
    enabled: !!body,
    staleTime: 60_000,
    retry: false,
    placeholderData: keepPreviousData,
  });
}

function useSettle() {
  const qc = useQueryClient();
  return (node: CloudNode) => {
    qc.setQueryData(cloudKeys.node(node.id), node);
    qc.invalidateQueries({ queryKey: cloudKeys.nodes });
  };
}

export function useCreateCloudNodes() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: CloudNodeCreate) => api.createCloudNodes(body),
    onSuccess: (nodes) => {
      qc.invalidateQueries({ queryKey: cloudKeys.nodes });
      toast.success(`Provisioning ${nodes.length === 1 ? nodes[0].name : `${nodes.length} VMs`}`, { description: 'cloud-init installs the agent and joins with a one-time token.' });
    },
    onError: (error) => toast.error(`Could not provision: ${cloudErrorMessage(error)}`),
  });
}

export function useUpdateCloudNode() {
  const settle = useSettle();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: CloudNodeUpdate }) => api.updateCloudNode(id, body),
    onSuccess: (node) => {
      settle(node);
      toast.success('Saved');
    },
    onError: (error) => toast.error(`Could not save: ${cloudErrorMessage(error)}`),
  });
}

type Action = 'stop' | 'start' | 'terminate' | 'refresh';

const ACTION_TEXT: Record<Action, (n: CloudNode) => string> = {
  stop: (n) => `Stopping ${n.name}${n.status === 'draining' ? ' — draining the node first' : ''}`,
  start: (n) => `Starting ${n.name}`,
  terminate: (n) => `Terminating ${n.name}${n.status === 'draining' ? ' — draining the node first' : ''}`,
  refresh: () => 'Polled the provider',
};

export function useCloudNodeAction() {
  const settle = useSettle();
  return useMutation({
    mutationFn: ({ id, action, body }: { id: string; action: Action; body?: CloudNodeAction }) => {
      const opts: CloudNodeAction = body ?? { force: false };
      switch (action) {
        case 'stop':
          return api.stopCloudNode(id, opts);
        case 'start':
          return api.startCloudNode(id);
        case 'terminate':
          return api.terminateCloudNode(id, opts);
        case 'refresh':
          return api.refreshCloudNode(id);
      }
    },
    onSuccess: (node, { action }) => {
      settle(node);
      toast.success(ACTION_TEXT[action](node));
    },
    onError: (error, { action }) => toast.error(`Could not ${action.replace(/-/g, ' ')}: ${cloudErrorMessage(error)}`),
  });
}

export function useDeleteCloudNode() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.deleteCloudNode(id),
    onSuccess: (_, id) => {
      qc.removeQueries({ queryKey: cloudKeys.node(id) });
      qc.invalidateQueries({ queryKey: cloudKeys.nodes });
      toast.success('Record removed');
    },
    onError: (error) => toast.error(`Could not remove the record: ${cloudErrorMessage(error)}`),
  });
}
