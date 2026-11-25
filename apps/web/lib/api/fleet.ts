/**
 * Fleet API — clusters, nodes, join tokens, devices, slices, certificates,
 * plus the vault credentials the cluster form picks from.
 *
 * Paths are relative to NEXT_PUBLIC_API_URL (…/api/v1) and follow
 * libs/contracts/openapi/admin.yaml. Single resources go through the shared
 * client; paginated lists go through `apiList`, which keeps the `meta`
 * envelope (total / page / has_more) that `apiGet` discards.
 */

import { apiDelete, apiGet, apiPatch, apiPost, apiRequest, metaFor, toQuery } from '@/lib/api/client';
import { getGatewayUrl } from '@/utils/gateway-url';
import type {
  AgentBuild,
  AgentCommand,
  CacheSummary,
  Cluster,
  ClusterCreate,
  ClusterListParams,
  ClusterUpdate,
  CommandRefs,
  Credential,
  Device,
  DeviceListParams,
  DeviceSlice,
  DeviceSliceCreate,
  DeviceSliceResult,
  DeviceSliceUpdate,
  DrainRequest,
  Instance,
  JoinToken,
  JoinTokenCreate,
  JoinTokenCreated,
  JoinTokenListParams,
  ListMeta,
  LogPage,
  LogSource,
  Node,
  NodeCertificate,
  NodeFileCommandResult,
  NodeHashRequest,
  NodeListParams,
  NodeScanRequest,
  NodeUpdate,
  RevokeRequest,
} from '@/types/fleet';

// ─── Paginated list helper ────────────────────────────────────────────────────

export interface ListResult<T> {
  items: T[];
  meta: ListMeta;
}

type QueryParams = Record<string, string | number | boolean | null | undefined>;

/**
 * GET a list endpoint (or POST a search body) and return items together with
 * the pagination meta. Same transport and error classes as `apiGet`, so React
 * Query's retry/redirect rules apply unchanged.
 */
export async function apiList<T>(path: string, params: QueryParams = {}, body?: unknown): Promise<ListResult<T>> {
  const { data, meta } = await apiRequest<T[] | null>(body === undefined ? 'GET' : 'POST', `${path}${toQuery(params)}`, body);
  const items = Array.isArray(data) ? data : [];
  return { items, meta: metaFor(items, meta) };
}

/** Strip the optional '' filter sentinels the URL state uses before sending. */
function clean(params: object): QueryParams {
  const out: QueryParams = {};
  for (const [k, v] of Object.entries(params)) {
    if (v === '' || v === undefined || v === null) continue;
    out[k] = v as string | number | boolean;
  }
  return out;
}

// ─── Clusters ─────────────────────────────────────────────────────────────────

export const listClusters = (params: ClusterListParams = {}) => apiList<Cluster>('/clusters', clean(params));
export const getCluster = (id: string) => apiGet<Cluster>(`/clusters/${id}`);
export const createCluster = (body: ClusterCreate) => apiPost<Cluster>('/clusters', body);
export const updateCluster = (id: string, body: ClusterUpdate) => apiPatch<Cluster>(`/clusters/${id}`, body);
export const deleteCluster = (id: string) => apiDelete<null>(`/clusters/${id}`);
export const listClusterNodes = (id: string, params: { page?: number; page_size?: number } = {}) =>
  apiList<Node>(`/clusters/${id}/nodes`, clean(params));

// ─── Nodes ────────────────────────────────────────────────────────────────────

export const listNodes = (params: NodeListParams = {}) => apiList<Node>('/nodes', clean(params));
export const getNode = (id: string) => apiGet<Node>(`/nodes/${id}`);
export const updateNode = (id: string, body: NodeUpdate) => apiPatch<Node>(`/nodes/${id}`, body);
export const removeNode = (id: string, force = false) => apiDelete<null>(`/nodes/${id}${toQuery({ force: force || undefined })}`);
export const drainNode = (id: string, body: DrainRequest) => apiPost<CommandRefs>(`/nodes/${id}/drain`, body);
export const undrainNode = (id: string) => apiPost<Node>(`/nodes/${id}/undrain`);
export const listNodeDevices = (id: string) => apiList<Device>(`/nodes/${id}/devices`);
export const listNodeInstances = (id: string) => apiList<Instance>(`/nodes/${id}/instances`);
export const getNodeCache = (id: string) => apiGet<CacheSummary>(`/nodes/${id}/cache`);
export const listNodeCommands = (id: string, params: { page?: number; page_size?: number; state?: string } = {}) =>
  apiList<AgentCommand>(`/nodes/${id}/commands`, clean(params));
/**
 * Read-only agent file commands (agent >= 1.1.0). The admin API waits up to
 * `wait_seconds` for the result: 200 with the outcome when the agent answered
 * in time, 202 with `state` running/sent otherwise — then poll
 * `getNodeCommand` until the state is terminal.
 */
export const scanNodePath = (id: string, body: NodeScanRequest) => apiPost<NodeFileCommandResult>(`/nodes/${id}/scan`, body);
export const hashNodeFiles = (id: string, body: NodeHashRequest) => apiPost<NodeFileCommandResult>(`/nodes/${id}/hash`, body);
export const getNodeCommand = (nodeId: string, commandId: string) => apiGet<NodeFileCommandResult>(`/nodes/${nodeId}/commands/${commandId}`);
export const listNodeCertificates = (id: string) => apiList<NodeCertificate>(`/nodes/${id}/certificates`);
export const rotateNodeCertificate = (id: string) => apiPost<CommandRefs>(`/nodes/${id}/certificates/rotate`);
export const revokeNodeCertificate = (nodeId: string, certId: string, body: RevokeRequest = {}) =>
  apiPost<NodeCertificate>(`/nodes/${nodeId}/certificates/${certId}/revoke`, body);

/** Agent / control-plane events for one node (GET /logs scoped to the node, newest first). */
export function listNodeEvents(
  nodeId: string,
  params: { sources?: LogSource[]; level?: string; sinceHours?: number; limit?: number } = {},
) {
  const query = new URLSearchParams({ node_id: nodeId, limit: String(params.limit ?? 100) });
  for (const source of params.sources ?? ['agent', 'control_plane', 'swap', 'rule']) query.append('source', source);
  if (params.level) query.set('level', params.level);
  query.set('from', new Date(Date.now() - (params.sinceHours ?? 24) * 3_600_000).toISOString());
  return apiGet<LogPage>(`/logs?${query.toString()}`);
}

// ─── Join tokens ──────────────────────────────────────────────────────────────

export const listJoinTokens = (params: JoinTokenListParams = {}) => apiList<JoinToken>('/nodes/join-tokens', clean(params));
export const createJoinToken = (body: JoinTokenCreate) => apiPost<JoinTokenCreated>('/nodes/join-tokens', body);
export const revokeJoinToken = (id: string) => apiDelete<null>(`/nodes/join-tokens/${id}`);

// ─── Devices and slices ───────────────────────────────────────────────────────

export const listDevices = (params: DeviceListParams = {}) => apiList<Device>('/devices', clean(params));
export const getDevice = (id: string) => apiGet<Device>(`/devices/${id}`);
export const listDeviceSlices = (id: string, includeReleased = false) =>
  apiList<DeviceSlice>(`/devices/${id}/slices`, { include_released: includeReleased || undefined });
export const listAllDeviceSlices = (params: { page?: number; page_size?: number; node_id?: string; deployment_id?: string } = {}) =>
  apiList<DeviceSlice>('/device-slices', clean(params));

/** Standalone reservation, or one more replica of a sliced deployment (`deployment_id`). */
export const createDeviceSlice = (deviceId: string, body: DeviceSliceCreate) => apiPost<DeviceSliceResult>(`/devices/${deviceId}/slices`, body);
/** Standalone: capacity-checked resize. Deployment slice: rolling update of every replica (`rolling_update`). */
export const updateDeviceSlice = (deviceId: string, sliceId: string, body: DeviceSliceUpdate) =>
  apiPatch<DeviceSliceResult>(`/devices/${deviceId}/slices/${sliceId}`, body);
/** Release; `force` also removes the replica / stops the SSH instance using it. */
export const deleteDeviceSlice = (deviceId: string, sliceId: string, force = false) =>
  apiDelete<DeviceSliceResult>(`/devices/${deviceId}/slices/${sliceId}${toQuery({ force: force || undefined })}`);

// ─── Agent downloads ──────────────────────────────────────────────────────────

export interface AgentBuilds {
  builds: AgentBuild[];
  latestVersion: string | null;
  /** URL prefix GPU machines download from (the agent hub's `<hub>/agent/v1` by default). */
  baseUrl: string;
  /** Release the control plane fetches missing builds from (null = local builds only). */
  releaseUrl: string | null;
}

/** Node-agent builds the control plane serves (AGENT_DIST_DIR, the release cache, or the release itself), newest first. */
export async function listAgentBuilds(): Promise<AgentBuilds> {
  const { data, meta } = await apiRequest<AgentBuild[] | null>('GET', '/agent/downloads');
  const extra = (meta ?? {}) as { latest_version?: string | null; base_url?: string; release_url?: string | null };
  const builds = (data ?? []).slice().sort((a, b) => b.version.localeCompare(a.version, undefined, { numeric: true }) || a.target.localeCompare(b.target));
  return {
    builds,
    latestVersion: extra.latest_version ?? builds[0]?.version ?? null,
    baseUrl: extra.base_url ?? `${getGatewayUrl()}/agent`,
    releaseUrl: extra.release_url ?? null,
  };
}

/** Absolute URL of a build (admin session cookie or `?token=` authorises it). */
export function agentDownloadUrl(build: Pick<AgentBuild, 'version' | 'target' | 'download_path'>): string {
  return `${getGatewayUrl()}${build.download_path ?? `/agent/downloads/${build.version}/${build.target}`}`;
}

// ─── Pickers (vault credentials) ──────────────────────────────────────────────

export const listCredentials = (params: { type?: string; status?: string; page_size?: number } = {}) =>
  apiList<Credential>('/vault', clean({ page_size: 200, ...params }));
