/**
 * SSH GPU instances API (admin.yaml, GPU Instances tag): request a container
 * with sshd and GPU(s) attached on a node, read its connection details, stop,
 * start again, extend the TTL, delete.
 */

import { apiDelete, apiGet, apiPatch, apiPost, toQuery } from '@/lib/api/client';
import { apiList, type ListResult } from '@/lib/api/fleet';
import type { components } from '@/lib/api/generated/admin';
import type {
  GpuInstance,
  GpuInstanceConnection,
  GpuInstanceCreate,
  GpuInstanceListParams,
  GpuInstanceStopRequest,
  GpuInstanceUpdate,
} from '@/types/gpu-instances';

type RawInstance = components['schemas']['GpuInstance'];
type RawConnection = components['schemas']['GpuInstanceConnection'];

function toConnection(c: RawConnection | undefined, user: string): GpuInstanceConnection {
  return {
    host: c?.host ?? null,
    port: c?.port ?? null,
    user: c?.user ?? user,
    host_key_fingerprint: c?.host_key_fingerprint ?? null,
    host_keys: (c?.host_keys ?? []).map((k) => ({ type: k.type ?? null, fingerprint: k.fingerprint ?? null, public_key: k.public_key ?? null })),
    ssh_command: c?.ssh_command ?? null,
    known_hosts: c?.known_hosts ?? [],
    ready: c?.ready ?? false,
  };
}

/** Contract row → UI shape: server-defaulted lists, maps and counters filled in. */
export function toGpuInstance(r: RawInstance): GpuInstance {
  return {
    id: r.id,
    name: r.name,
    description: r.description ?? null,
    state: r.state,
    state_message: r.state_message ?? null,
    health: r.health ?? 'unknown',
    image: r.image,
    cluster_id: r.cluster_id ?? null,
    node_id: r.node_id ?? null,
    node_name: r.node_name ?? null,
    gpu_count: r.gpu_count,
    slice_vram_bytes: r.slice_vram_bytes ?? null,
    slice_compute_pct: r.slice_compute_pct ?? null,
    device_vendor: r.device_vendor ?? null,
    node_selector: r.node_selector ?? {},
    device_ids: r.device_ids ?? [],
    limits: r.limits ?? {},
    authorized_keys: (r.authorized_keys ?? []).map((k) => ({ type: k.type ?? 'unknown', fingerprint: k.fingerprint ?? '', comment: k.comment ?? null })),
    ssh_user: r.ssh_user,
    container_port: r.container_port,
    allowed_source_cidrs: r.allowed_source_cidrs ?? [],
    env: r.env ?? {},
    volumes: (r.volumes ?? []).map((v) => ({
      type: v.type === 'bind' || v.type === 'tmpfs' ? v.type : 'volume',
      source: typeof v.source === 'string' ? v.source : null,
      target: String(v.target ?? ''),
      read_only: Boolean(v.read_only),
      keep: Boolean(v.keep),
      size_bytes: typeof v.size_bytes === 'number' ? v.size_bytes : null,
    })),
    labels: r.labels ?? {},
    ttl_seconds: r.ttl_seconds ?? null,
    idle_timeout_seconds: r.idle_timeout_seconds ?? null,
    expires_at: r.expires_at ?? null,
    connection: toConnection(r.connection, r.ssh_user),
    gpu_attached: r.gpu_attached ?? false,
    warnings: r.warnings ?? [],
    container_id: r.container_id ?? null,
    active_connections: r.active_connections ?? 0,
    connections_total: r.connections_total ?? 0,
    last_connection_at: r.last_connection_at ?? null,
    stop_reason: r.stop_reason ?? null,
    start_command_id: r.start_command_id ?? null,
    stop_command_id: r.stop_command_id ?? null,
    gpu_seconds_total: r.gpu_seconds_total ?? 0,
    state_changed_at: r.state_changed_at ?? null,
    started_at: r.started_at ?? null,
    running_at: r.running_at ?? null,
    stopped_at: r.stopped_at ?? null,
    created_at: r.created_at,
    updated_at: r.updated_at ?? null,
  };
}

export const listGpuInstances = async (params: GpuInstanceListParams = {}): Promise<ListResult<GpuInstance>> => {
  const page = await apiList<RawInstance>('/gpu-instances', {
    page: params.page,
    page_size: params.page_size,
    state: params.state || undefined,
    node_id: params.node_id,
    cluster_id: params.cluster_id,
    search: params.search || undefined,
    include_stopped: params.include_stopped,
  });
  return { items: page.items.map(toGpuInstance), meta: page.meta };
};

export const getGpuInstance = async (id: string) => toGpuInstance(await apiGet<RawInstance>(`/gpu-instances/${id}`));
export const getGpuInstanceConnection = async (id: string, user = 'gpudeck') =>
  toConnection(await apiGet<RawConnection>(`/gpu-instances/${id}/connection`), user);
export const createGpuInstance = async (body: GpuInstanceCreate) => toGpuInstance(await apiPost<RawInstance>('/gpu-instances', body));
export const updateGpuInstance = async (id: string, body: GpuInstanceUpdate) => toGpuInstance(await apiPatch<RawInstance>(`/gpu-instances/${id}`, body));
export const stopGpuInstance = async (id: string, body: GpuInstanceStopRequest = { force: false, remove_volumes: true }) =>
  toGpuInstance(await apiPost<RawInstance>(`/gpu-instances/${id}/stop`, body));
export const startGpuInstance = async (id: string) => toGpuInstance(await apiPost<RawInstance>(`/gpu-instances/${id}/start`));
export const deleteGpuInstance = (id: string, force = false) => apiDelete<null>(`/gpu-instances/${id}${toQuery({ force: force || undefined })}`);
