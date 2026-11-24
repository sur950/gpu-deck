/**
 * SSH GPU instances — UI types (snake_case, exactly the admin.yaml
 * `GpuInstance*` shapes, GPU Instances tag).
 */

import type { components } from '@/lib/api/generated/admin';

export type GpuInstanceState = components['schemas']['gpu_instance_state'];

export const GPU_INSTANCE_STATES: GpuInstanceState[] = ['requested', 'placing', 'starting', 'running', 'stopping', 'stopped', 'error'];

/** Lifecycle shown as a stepper; stopping/stopped/error branch off the end. */
export const GPU_INSTANCE_LIFECYCLE: GpuInstanceState[] = ['requested', 'placing', 'starting', 'running'];

export const GPU_INSTANCE_STATE_LABELS: Record<GpuInstanceState, string> = {
  requested: 'Requested',
  placing: 'Placing',
  starting: 'Starting',
  running: 'Running',
  stopping: 'Stopping',
  stopped: 'Stopped',
  error: 'Error',
};

type S = components['schemas'];

export type GpuDeviceVendor = S['device_vendor'];

/** Contract request bodies. */
export type GpuInstanceCreate = S['GpuInstanceCreate'];
export type GpuInstanceUpdate = S['GpuInstanceUpdate'];
export type GpuInstanceStopRequest = S['GpuInstanceStopRequest'];
export type GpuInstanceVolume = S['GpuInstanceVolume'];

/**
 * UI shapes: the contract's `GpuInstance` with its server-defaulted fields
 * (lists, maps, counters) made non-optional by lib/api/gpu-instances.ts.
 */
export interface GpuInstanceLimits {
  cpu_cores?: number | null;
  memory_bytes?: number | null;
  shm_bytes?: number | null;
  pids?: number | null;
}

export interface GpuInstanceKey {
  type: string;
  fingerprint: string;
  comment: string | null;
}

export interface GpuInstanceHostKey {
  type: string | null;
  fingerprint: string | null;
  public_key: string | null;
}

export interface GpuInstanceConnection {
  host: string | null;
  port: number | null;
  user: string;
  host_key_fingerprint: string | null;
  host_keys: GpuInstanceHostKey[];
  ssh_command: string | null;
  known_hosts: string[];
  ready: boolean;
}

export interface GpuInstance {
  id: string;
  name: string;
  description: string | null;
  state: GpuInstanceState;
  state_message: string | null;
  health: string;
  image: string;
  cluster_id: string | null;
  node_id: string | null;
  node_name: string | null;
  gpu_count: number;
  slice_vram_bytes: number | null;
  slice_compute_pct: number | null;
  device_vendor: string | null;
  node_selector: Record<string, unknown>;
  device_ids: string[];
  limits: GpuInstanceLimits;
  authorized_keys: GpuInstanceKey[];
  ssh_user: string;
  container_port: number;
  allowed_source_cidrs: string[];
  env: Record<string, string>;
  volumes: GpuInstanceVolume[];
  labels: Record<string, string>;
  ttl_seconds: number | null;
  idle_timeout_seconds: number | null;
  expires_at: string | null;
  connection: GpuInstanceConnection;
  gpu_attached: boolean;
  warnings: string[];
  container_id: string | null;
  active_connections: number;
  connections_total: number;
  last_connection_at: string | null;
  stop_reason: string | null;
  start_command_id: string | null;
  stop_command_id: string | null;
  gpu_seconds_total: number;
  state_changed_at: string | null;
  started_at: string | null;
  running_at: string | null;
  stopped_at: string | null;
  created_at: string;
  updated_at: string | null;
}

export interface GpuInstanceListParams {
  page?: number;
  page_size?: number;
  state?: GpuInstanceState | '';
  node_id?: string;
  cluster_id?: string;
  search?: string;
  include_stopped?: boolean;
}

/** Stop reasons the lifecycle writes (`stop_reason`). */
export const STOP_REASON_LABELS: Record<string, string> = {
  user: 'Stopped by you',
  ttl_expired: 'TTL reached',
  idle_timeout: 'Idle timeout',
  deleted: 'Deleted',
};
