'use client';

/**
 * Fleet hooks — React Query wrappers over lib/api/fleet.ts for clusters,
 * nodes, join tokens, devices and slices. Key factory + toasts +
 * invalidation live here; components never call the API
 * module directly.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import * as api from '@/lib/api/fleet';
import type {
  Cluster,
  ClusterCreate,
  ClusterListParams,
  ClusterUpdate,
  DeviceListParams,
  DeviceSliceCreate,
  DeviceSliceResult,
  DeviceSliceUpdate,
  DrainRequest,
  JoinTokenCreate,
  JoinTokenListParams,
  Node,
  NodeFileCommandResult,
  NodeHashRequest,
  NodeListParams,
  NodeScanRequest,
  NodeUpdate,
} from '@/types/fleet';
import { isTerminalCommand } from '@/types/fleet';

const LIVE_INTERVAL_MS = 10_000;

export const fleetKeys = {
  clusters: ['clusters'] as const,
  clusterList: (params?: ClusterListParams) => ['clusters', 'list', params] as const,
  cluster: (id: string) => ['clusters', 'detail', id] as const,
  clusterNodes: (id: string) => ['clusters', 'detail', id, 'nodes'] as const,

  nodes: ['nodes'] as const,
  nodeList: (params?: NodeListParams) => ['nodes', 'list', params] as const,
  node: (id: string) => ['nodes', 'detail', id] as const,
  nodeDevices: (id: string) => ['nodes', 'detail', id, 'devices'] as const,
  nodeInstances: (id: string) => ['nodes', 'detail', id, 'instances'] as const,
  nodeCache: (id: string) => ['nodes', 'detail', id, 'cache'] as const,
  nodeCommands: (id: string) => ['nodes', 'detail', id, 'commands'] as const,
  nodeCommand: (id: string, commandId: string) => ['nodes', 'detail', id, 'commands', commandId] as const,
  nodeCertificates: (id: string) => ['nodes', 'detail', id, 'certificates'] as const,
  nodeEvents: (id: string, level?: string) => ['nodes', 'detail', id, 'events', level ?? ''] as const,

  joinTokens: ['join-tokens'] as const,
  joinTokenList: (params?: JoinTokenListParams) => ['join-tokens', 'list', params] as const,

  devices: ['devices'] as const,
  deviceList: (params?: DeviceListParams) => ['devices', 'list', params] as const,
  device: (id: string) => ['devices', 'detail', id] as const,
  deviceSlices: (id: string, includeReleased: boolean) => ['devices', 'detail', id, 'slices', includeReleased] as const,
  allSlices: (params?: object) => ['device-slices', params] as const,

  agentBuilds: ['agent-downloads'] as const,
  credentials: (type?: string) => ['vault', 'picker', type] as const,
};

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'Request failed';
}

// ─── Clusters ─────────────────────────────────────────────────────────────────

export function useClusters(params: ClusterListParams = {}, options: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: fleetKeys.clusterList(params),
    queryFn: () => api.listClusters(params),
    staleTime: 30_000,
    placeholderData: (prev) => prev,
    ...options,
  });
}

export function useCluster(id: string) {
  return useQuery({
    queryKey: fleetKeys.cluster(id),
    queryFn: () => api.getCluster(id),
    enabled: !!id,
    staleTime: 30_000,
  });
}

export function useClusterNodes(id: string) {
  return useQuery({
    queryKey: fleetKeys.clusterNodes(id),
    queryFn: () => api.listClusterNodes(id, { page_size: 200 }),
    enabled: !!id,
    refetchInterval: LIVE_INTERVAL_MS,
  });
}

export function useCreateCluster() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ClusterCreate) => api.createCluster(body),
    onSuccess: (cluster) => {
      qc.setQueryData(fleetKeys.cluster(cluster.id), cluster);
      qc.invalidateQueries({ queryKey: fleetKeys.clusters });
      toast.success(`Cluster "${cluster.name}" created`);
    },
    onError: (error) => toast.error(`Could not create the cluster: ${errorMessage(error)}`),
  });
}

export function useUpdateCluster() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: ClusterUpdate }) => api.updateCluster(id, body),
    onMutate: async ({ id, body }) => {
      await qc.cancelQueries({ queryKey: fleetKeys.cluster(id) });
      const previous = qc.getQueryData<Cluster>(fleetKeys.cluster(id));
      if (previous) qc.setQueryData<Cluster>(fleetKeys.cluster(id), { ...previous, ...body } as Cluster);
      return { previous };
    },
    onError: (error, { id }, ctx) => {
      if (ctx?.previous) qc.setQueryData(fleetKeys.cluster(id), ctx.previous);
      toast.error(`Could not update the cluster: ${errorMessage(error)}`);
    },
    onSuccess: (cluster) => {
      qc.setQueryData(fleetKeys.cluster(cluster.id), cluster);
      toast.success('Cluster updated');
    },
    onSettled: () => qc.invalidateQueries({ queryKey: fleetKeys.clusters }),
  });
}

export function useDeleteCluster() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.deleteCluster(id),
    onSuccess: (_, id) => {
      qc.removeQueries({ queryKey: fleetKeys.cluster(id) });
      qc.invalidateQueries({ queryKey: fleetKeys.clusters });
      toast.success('Cluster deleted');
    },
    onError: (error) => toast.error(`Could not delete the cluster: ${errorMessage(error)}`),
  });
}

// ─── Nodes ────────────────────────────────────────────────────────────────────

export function useNodes(params: NodeListParams = {}, options: { enabled?: boolean; live?: boolean } = {}) {
  const { live = true, ...rest } = options;
  return useQuery({
    queryKey: fleetKeys.nodeList(params),
    queryFn: () => api.listNodes(params),
    staleTime: 10_000,
    placeholderData: (prev) => prev,
    refetchInterval: live ? LIVE_INTERVAL_MS : false,
    ...rest,
  });
}

export function useNode(id: string) {
  return useQuery({
    queryKey: fleetKeys.node(id),
    queryFn: () => api.getNode(id),
    enabled: !!id,
    staleTime: 10_000,
    refetchInterval: LIVE_INTERVAL_MS,
  });
}

export function useNodeDevices(id: string) {
  return useQuery({
    queryKey: fleetKeys.nodeDevices(id),
    queryFn: () => api.listNodeDevices(id),
    enabled: !!id,
    refetchInterval: LIVE_INTERVAL_MS,
  });
}

export function useNodeInstances(id: string) {
  return useQuery({
    queryKey: fleetKeys.nodeInstances(id),
    queryFn: () => api.listNodeInstances(id),
    enabled: !!id,
    refetchInterval: LIVE_INTERVAL_MS,
  });
}

export function useNodeCache(id: string) {
  return useQuery({
    queryKey: fleetKeys.nodeCache(id),
    queryFn: () => api.getNodeCache(id),
    enabled: !!id,
    staleTime: 30_000,
  });
}

export function useNodeCommands(id: string) {
  return useQuery({
    queryKey: fleetKeys.nodeCommands(id),
    queryFn: () => api.listNodeCommands(id, { page_size: 25 }),
    enabled: !!id,
    refetchInterval: LIVE_INTERVAL_MS,
  });
}

/** Poll one agent command (scan / hash results) every second until it reaches a terminal state. */
export function useNodeCommand(nodeId: string, commandId: string | null | undefined) {
  const qc = useQueryClient();
  return useQuery({
    queryKey: fleetKeys.nodeCommand(nodeId, commandId ?? ''),
    queryFn: async () => {
      const result = await api.getNodeCommand(nodeId, commandId as string);
      // The command list polls every 10 s; refresh it as soon as this one settles.
      if (isTerminalCommand(result.state)) void qc.invalidateQueries({ queryKey: fleetKeys.nodeCommands(nodeId), exact: true });
      return result;
    },
    enabled: !!nodeId && !!commandId,
    staleTime: 1_000,
    refetchInterval: (query) => (query.state.data && isTerminalCommand(query.state.data.state) ? false : 1_000),
  });
}

function useFileCommandMutation<B>(fn: (nodeId: string, body: B) => Promise<NodeFileCommandResult>, failText: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ nodeId, body }: { nodeId: string; body: B }) => fn(nodeId, body),
    onSuccess: (result) => {
      // Seed the poller so the panel renders the 200/202 answer without a round trip.
      qc.setQueryData(fleetKeys.nodeCommand(result.node_id, result.command_id), result);
      qc.invalidateQueries({ queryKey: fleetKeys.nodeCommands(result.node_id), exact: true });
    },
    onError: (error) => toast.error(`${failText}: ${errorMessage(error)}`),
  });
}

/** Agent `scan_path`: list model candidates under a path on the node's disk. */
export function useScanNodePath() {
  return useFileCommandMutation<NodeScanRequest>(api.scanNodePath, 'Could not start the scan');
}

/** Agent `hash_files`: sha256 of files on the node's disk (streams progress). */
export function useHashNodeFiles() {
  return useFileCommandMutation<NodeHashRequest>(api.hashNodeFiles, 'Could not start hashing');
}

export function useNodeCertificates(id: string) {
  return useQuery({
    queryKey: fleetKeys.nodeCertificates(id),
    queryFn: () => api.listNodeCertificates(id),
    enabled: !!id,
    staleTime: 60_000,
  });
}

export function useNodeEvents(id: string, level?: string) {
  return useQuery({
    queryKey: fleetKeys.nodeEvents(id, level),
    queryFn: () => api.listNodeEvents(id, { level }),
    enabled: !!id,
    refetchInterval: 15_000,
  });
}

export function useUpdateNode() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: NodeUpdate }) => api.updateNode(id, body),
    onSuccess: (node) => {
      qc.setQueryData(fleetKeys.node(node.id), node);
      qc.invalidateQueries({ queryKey: fleetKeys.nodes });
      toast.success('Node updated');
    },
    onError: (error) => toast.error(`Could not update the node: ${errorMessage(error)}`),
  });
}

/** Optimistically flips the node's status while the agent processes the command. */
function useNodeStatusMutation<V extends { id: string }>(
  fn: (vars: V) => Promise<unknown>,
  optimistic: Node['status'],
  successText: string,
  failText: string,
) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onMutate: async ({ id }) => {
      await qc.cancelQueries({ queryKey: fleetKeys.node(id) });
      const previous = qc.getQueryData<Node>(fleetKeys.node(id));
      if (previous) qc.setQueryData<Node>(fleetKeys.node(id), { ...previous, status: optimistic });
      return { previous };
    },
    onError: (error, { id }, ctx) => {
      if (ctx?.previous) qc.setQueryData(fleetKeys.node(id), ctx.previous);
      toast.error(`${failText}: ${errorMessage(error)}`);
    },
    onSuccess: () => toast.success(successText),
    onSettled: (_data, _err, { id }) => {
      qc.invalidateQueries({ queryKey: fleetKeys.node(id) });
      qc.invalidateQueries({ queryKey: fleetKeys.nodes });
    },
  });
}

export function useDrainNode() {
  return useNodeStatusMutation<{ id: string; body: DrainRequest }>(
    ({ id, body }) => api.drainNode(id, body),
    'draining',
    'Drain started — instances are being moved off the node',
    'Could not drain the node',
  );
}

export function useUndrainNode() {
  return useNodeStatusMutation<{ id: string }>(
    ({ id }) => api.undrainNode(id),
    'online',
    'Node is accepting placements again',
    'Could not undrain the node',
  );
}

export function useRemoveNode() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, force }: { id: string; force?: boolean }) => api.removeNode(id, force),
    onSuccess: (_, { id }) => {
      qc.removeQueries({ queryKey: fleetKeys.node(id) });
      qc.invalidateQueries({ queryKey: fleetKeys.nodes });
      qc.invalidateQueries({ queryKey: fleetKeys.devices });
      toast.success('Node removed from the fleet');
    },
    onError: (error) => toast.error(`Could not remove the node: ${errorMessage(error)}`),
  });
}

export function useRotateNodeCertificate() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.rotateNodeCertificate(id),
    onSuccess: (_, id) => {
      qc.invalidateQueries({ queryKey: fleetKeys.nodeCertificates(id) });
      toast.success('Certificate rotation requested — the agent will reconnect with the new certificate');
    },
    onError: (error) => toast.error(`Could not rotate the certificate: ${errorMessage(error)}`),
  });
}

export function useRevokeNodeCertificate() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ nodeId, certId, reason }: { nodeId: string; certId: string; reason?: string }) =>
      api.revokeNodeCertificate(nodeId, certId, { reason: reason ?? null }),
    onSuccess: (_, { nodeId }) => {
      qc.invalidateQueries({ queryKey: fleetKeys.nodeCertificates(nodeId) });
      qc.invalidateQueries({ queryKey: fleetKeys.node(nodeId) });
      qc.invalidateQueries({ queryKey: fleetKeys.nodes });
      toast.success('Certificate revoked — the node is cut off from the hub');
    },
    onError: (error) => toast.error(`Could not revoke the certificate: ${errorMessage(error)}`),
  });
}

// ─── Join tokens ──────────────────────────────────────────────────────────────

export function useJoinTokens(params: JoinTokenListParams = {}) {
  return useQuery({
    queryKey: fleetKeys.joinTokenList(params),
    queryFn: () => api.listJoinTokens(params),
    staleTime: 10_000,
    refetchInterval: LIVE_INTERVAL_MS,
  });
}

export function useCreateJoinToken() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: JoinTokenCreate) => api.createJoinToken(body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: fleetKeys.joinTokens });
      toast.success('Join token issued — it is shown once, copy it now');
    },
    onError: (error) => toast.error(`Could not issue a join token: ${errorMessage(error)}`),
  });
}

export function useRevokeJoinToken() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.revokeJoinToken(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: fleetKeys.joinTokens });
      toast.success('Join token revoked');
    },
    onError: (error) => toast.error(`Could not revoke the token: ${errorMessage(error)}`),
  });
}

// ─── Devices and slices ───────────────────────────────────────────────────────

export function useDevices(params: DeviceListParams = {}, options: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: fleetKeys.deviceList(params),
    queryFn: () => api.listDevices(params),
    staleTime: 10_000,
    placeholderData: (prev) => prev,
    refetchInterval: LIVE_INTERVAL_MS,
    ...options,
  });
}

export function useDevice(id: string) {
  return useQuery({
    queryKey: fleetKeys.device(id),
    queryFn: () => api.getDevice(id),
    enabled: !!id,
    refetchInterval: LIVE_INTERVAL_MS,
  });
}

export function useDeviceSlices(id: string, includeReleased = false) {
  return useQuery({
    queryKey: fleetKeys.deviceSlices(id, includeReleased),
    queryFn: () => api.listDeviceSlices(id, includeReleased),
    enabled: !!id,
    staleTime: 10_000,
  });
}

export function useAllDeviceSlices(params: { node_id?: string; deployment_id?: string } = {}) {
  return useQuery({
    queryKey: fleetKeys.allSlices(params),
    queryFn: () => api.listAllDeviceSlices({ page_size: 500, ...params }),
    staleTime: 10_000,
  });
}

function useInvalidateSlices() {
  const qc = useQueryClient();
  return () => {
    qc.invalidateQueries({ queryKey: fleetKeys.devices });
    qc.invalidateQueries({ queryKey: ['device-slices'] });
    qc.invalidateQueries({ queryKey: ['deployments'] });
    qc.invalidateQueries({ queryKey: ['gpu-instances'] });
  };
}

function sliceOutcome(result: DeviceSliceResult, fallback: string): string {
  if (result.message) return result.message;
  return result.rolling_update ? 'Rolling update started — replicas are replaced one at a time' : fallback;
}

export function useCreateDeviceSlice() {
  const invalidate = useInvalidateSlices();
  return useMutation({
    mutationFn: ({ deviceId, body }: { deviceId: string; body: DeviceSliceCreate }) => api.createDeviceSlice(deviceId, body),
    onSuccess: (result) => {
      invalidate();
      toast.success(result.deployment_id ? 'Replica placed on this GPU' : `Slice "${result.slice.name ?? 'slice'}" reserved`, { description: result.message ?? undefined });
    },
    onError: (error) => toast.error(`Could not create the slice: ${errorMessage(error)}`),
  });
}

export function useUpdateDeviceSlice() {
  const invalidate = useInvalidateSlices();
  return useMutation({
    mutationFn: ({ deviceId, sliceId, body }: { deviceId: string; sliceId: string; body: DeviceSliceUpdate }) => api.updateDeviceSlice(deviceId, sliceId, body),
    onSuccess: (result) => {
      invalidate();
      toast.success(sliceOutcome(result, 'Slice resized'));
    },
    onError: (error) => toast.error(`Could not resize the slice: ${errorMessage(error)}`),
  });
}

export function useDeleteDeviceSlice() {
  const invalidate = useInvalidateSlices();
  return useMutation({
    mutationFn: ({ deviceId, sliceId, force }: { deviceId: string; sliceId: string; force?: boolean }) => api.deleteDeviceSlice(deviceId, sliceId, force),
    onSuccess: (result) => {
      invalidate();
      toast.success(result.message ?? 'Slice released');
    },
    onError: (error) => toast.error(`Could not release the slice: ${errorMessage(error)}`),
  });
}

// ─── Agent downloads ──────────────────────────────────────────────────────────

export function useAgentBuilds() {
  return useQuery({
    queryKey: fleetKeys.agentBuilds,
    queryFn: () => api.listAgentBuilds(),
    staleTime: 5 * 60_000,
  });
}

// ─── Pickers ──────────────────────────────────────────────────────────────────

export function useCredentialOptions(types?: string[]) {
  const key = types?.join(',');
  return useQuery({
    queryKey: fleetKeys.credentials(key),
    queryFn: async () => {
      const result = await api.listCredentials({ status: 'active' });
      return types && types.length > 0 ? result.items.filter((c) => types.includes(c.type)) : result.items;
    },
    staleTime: 60_000,
  });
}
