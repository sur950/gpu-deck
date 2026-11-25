/**
 * Fleet types (clusters, nodes, join tokens, GPUs, slices, certificates).
 *
 * Contract shapes come straight from the generated OpenAPI types and keep the
 * backend's snake_case: the admin API carries free-form dictionaries (labels,
 * launch_flags, env_vars, config …) whose keys must never be re-cased, so a
 * generic snake→camel mapper would corrupt them. UI-only helpers (labels,
 * option lists, derived unions) live below the aliases.
 */

import type { components } from '@/lib/api/generated/admin';
import { PICKER_KINDS } from '@/types/vault';

type S = components['schemas'];

// ─── Contract aliases ─────────────────────────────────────────────────────────

export type ListMeta = S['ListMeta'];
export type CommandRefs = S['CommandRefs'];

export type Cluster = S['Cluster'];
export type ClusterCreate = S['ClusterCreate'];
export type ClusterUpdate = S['ClusterUpdate'];
export type ClusterCounts = S['ClusterCounts'];
export type ClusterType = S['cluster_type'];
export type ClusterStatus = S['cluster_status'];

export type Node = S['Node'];
export type NodeUpdate = S['NodeUpdate'];
export type NodeStatus = S['node_status'];
export type NodeOs = S['node_os'];
export type NodeArch = S['node_arch'];
export type NodeDisk = S['NodeDisk'];
export type DrainRequest = S['DrainRequest'];
export type NodeCertificate = S['NodeCertificate'];
export type RevokeRequest = S['RevokeRequest'];
export type AgentCommand = S['AgentCommand'];
export type AgentCommandState = S['agent_command_state'];
export type NodeScanRequest = S['NodeScanRequest'];
export type NodeHashRequest = S['NodeHashRequest'];
export type NodeFileCommandResult = S['NodeFileCommandResult'];

export type JoinToken = S['JoinToken'];
export type JoinTokenCreate = S['JoinTokenCreate'];
export type JoinTokenCreated = S['JoinTokenCreated'];
export type AgentBuild = S['AgentBuild'];

export type Device = S['Device'];
export type DeviceProcess = S['DeviceProcess'];
export type DeviceLive = S['DeviceLive'];
export type DeviceSlice = S['DeviceSlice'];
export type DeviceSliceCreate = S['DeviceSliceCreate'];
export type DeviceSliceUpdate = S['DeviceSliceUpdate'];
export type DeviceSliceResult = S['DeviceSliceResult'];
export type DeviceVendor = S['device_vendor'];
export type DeviceHealth = S['device_health'];
export type DeviceSliceStatus = S['device_slice_status'];

export type Credential = S['Credential'];
export type CredentialType = S['credential_type'];

export type CacheSummary = S['CacheSummary'];
export type Instance = S['Instance'];

export type LogChunk = S['LogChunk'];
export type LogPage = S['LogPage'];
export type LogSource = S['log_source'];
export type LogLevel = S['log_level'];
export type CertificateKind = S['certificate_kind'];

// ─── Agent file commands (agent-protocol command.json, agent >= 1.1.0) ──────
// NodeFileCommandResult.result is a free-form object in admin.yaml; these
// mirror the agent-protocol ScanPathResult / HashFilesResult shapes.

export type ScanCandidateKind = 'plain' | 'ollama_blob' | 'lmstudio' | 'hf_cache' | 'mlx_dir';

export interface ScanCandidateFile {
  /** Relative to the candidate root (file name for single files / blobs). */
  path: string;
  /** Real path on the node (symlinks resolved). */
  absolute_path: string;
  size_bytes: number;
  format: S['model_file_format'];
  /** Known without hashing (Ollama manifest digest); otherwise null. */
  sha256?: string | null;
  is_required?: boolean;
}

export interface ScanCandidate {
  path: string;
  kind: ScanCandidateKind;
  format: S['model_file_format'];
  files: ScanCandidateFile[];
  total_size_bytes: number;
  suggested?: {
    name?: string;
    family?: string | null;
    type?: string | null;
    quantization?: string | null;
    context_size?: number | null;
    parameter_count?: number | null;
    architecture?: string | null;
    ollama_name?: string | null;
    lmstudio_name?: string | null;
    repo_id?: string | null;
    [key: string]: unknown;
  };
  metadata?: Record<string, unknown>;
}

export interface ScanPathResult {
  /** The expanded absolute path that was scanned. */
  path: string;
  candidates: ScanCandidate[];
  truncated: boolean;
  visited?: number;
  seconds?: number;
}

export interface HashedFile {
  path: string;
  sha256: string | null;
  size_bytes: number | null;
  error?: { code: string; message: string } | null;
}

export interface HashFilesResult {
  root?: string | null;
  files: HashedFile[];
  hashes: Record<string, string>;
  bytes_hashed: number;
  seconds?: number;
}

export const TERMINAL_COMMAND_STATES: readonly AgentCommandState[] = ['succeeded', 'failed', 'rejected', 'timed_out', 'cancelled'];

export function isTerminalCommand(state: AgentCommandState | undefined): boolean {
  return !!state && TERMINAL_COMMAND_STATES.includes(state);
}

/** `1.10.0 >= 1.1.0` — numeric compare of dotted versions (pre-release suffixes ignored). */
export function versionAtLeast(version: string | null | undefined, min: string): boolean {
  if (!version) return false;
  const a = version.replace(/^v/, '').split(/[.+-]/).slice(0, 3).map((n) => Number.parseInt(n, 10) || 0);
  const b = min.split('.').map((n) => Number.parseInt(n, 10) || 0);
  for (let i = 0; i < 3; i++) {
    if ((a[i] ?? 0) !== (b[i] ?? 0)) return (a[i] ?? 0) > (b[i] ?? 0);
  }
  return true;
}

/** One engine the agent found on the node (inventory `capabilities.engines`). */
export interface NodeEngine {
  name: string;
  /** `native` = binary on the host (path set); `container` = image pulled on demand. */
  runtime: string | null;
  path: string | null;
  versions: string[];
}

/** Normalise `capabilities.engines` — agent 1.1 reports objects, older fixtures plain names. */
export function nodeEngines(capabilities: Record<string, unknown> | null | undefined): NodeEngine[] {
  const raw = capabilities?.engines;
  if (!Array.isArray(raw)) return [];
  const out: NodeEngine[] = [];
  for (const item of raw) {
    if (typeof item === 'string') out.push({ name: item, runtime: null, path: null, versions: [] });
    else if (item && typeof item === 'object' && typeof (item as { name?: unknown }).name === 'string') {
      const e = item as { name: string; runtime?: unknown; path?: unknown; versions?: unknown };
      out.push({
        name: e.name,
        runtime: typeof e.runtime === 'string' ? e.runtime : null,
        path: typeof e.path === 'string' ? e.path : null,
        versions: Array.isArray(e.versions) ? e.versions.filter((v): v is string => typeof v === 'string') : [],
      });
    }
  }
  return out;
}

export const SCAN_KIND_LABELS: Record<ScanCandidateKind, string> = {
  plain: 'Plain files',
  ollama_blob: 'Ollama',
  lmstudio: 'LM Studio',
  hf_cache: 'HF cache',
  mlx_dir: 'MLX',
};

// ─── Query params ─────────────────────────────────────────────────────────────

export interface ClusterListParams {
  page?: number;
  page_size?: number;
  search?: string;
  sort?: string;
  status?: ClusterStatus | '';
  type?: ClusterType | '';
}

export interface NodeListParams {
  page?: number;
  page_size?: number;
  search?: string;
  sort?: string;
  status?: NodeStatus | '';
  cluster_id?: string;
  os?: NodeOs | '';
}

/** Heartbeat freshness buckets used by the fleet screens (agent heartbeats every ~5 s). */
export type HeartbeatFreshness = 'live' | 'late' | 'stale' | 'never';

export function heartbeatFreshness(at: string | null | undefined, now = Date.now()): HeartbeatFreshness {
  if (!at) return 'never';
  const age = now - new Date(at).getTime();
  if (age < 20_000) return 'live';
  if (age < 90_000) return 'late';
  return 'stale';
}

export interface DeviceListParams {
  page?: number;
  page_size?: number;
  sort?: string;
  vendor?: DeviceVendor | '';
  health?: DeviceHealth | '';
  cluster_id?: string;
  node_id?: string;
  free_vram_min_bytes?: number;
}

export interface JoinTokenListParams {
  page?: number;
  page_size?: number;
  cluster_id?: string;
  active_only?: boolean;
}

// ─── Labels and option lists ──────────────────────────────────────────────────

export const CLUSTER_TYPE_LABELS: Record<ClusterType, string> = {
  bare_metal: 'Bare metal',
  vm: 'Virtual machines',
  kubernetes: 'Kubernetes',
  cloud_aws: 'AWS',
  cloud_azure: 'Azure',
  cloud_gcp: 'Google Cloud',
  cloud_do: 'DigitalOcean',
};

export const CLUSTER_TYPE_OPTIONS = (Object.keys(CLUSTER_TYPE_LABELS) as ClusterType[]).map((value) => ({
  value,
  label: CLUSTER_TYPE_LABELS[value],
}));

export const CLOUD_CLUSTER_TYPES: ClusterType[] = ['cloud_aws', 'cloud_azure', 'cloud_gcp', 'cloud_do'];

export function isCloudCluster(type: ClusterType): boolean {
  return CLOUD_CLUSTER_TYPES.includes(type);
}

/** Vault credential kinds a cluster type accepts (AWS key for cloud_aws, kubeconfig for Kubernetes, SSH key otherwise); mirrors the backend's CLUSTER_TYPE_KINDS. */
export function credentialKindsForCluster(type: ClusterType): string[] {
  return PICKER_KINDS[`cluster.${type}`] ?? ['ssh_key'];
}

export const NODE_STATUS_LABELS: Record<NodeStatus, string> = {
  pending: 'Pending',
  online: 'Online',
  offline: 'Offline',
  unhealthy: 'Unhealthy',
  draining: 'Draining',
  drained: 'Drained',
  removed: 'Removed',
};

export const NODE_OS_LABELS: Record<NodeOs, string> = { linux: 'Linux', macos: 'macOS' };

export const DEVICE_VENDOR_LABELS: Record<DeviceVendor, string> = {
  nvidia: 'NVIDIA',
  amd: 'AMD',
  hygon: 'Hygon',
  metax: 'MetaX',
  moorethreads: 'Moore Threads',
  thead: 'T-Head',
  apple: 'Apple',
};

export const DEVICE_VENDOR_OPTIONS = (Object.keys(DEVICE_VENDOR_LABELS) as DeviceVendor[]).map((value) => ({
  value,
  label: DEVICE_VENDOR_LABELS[value],
}));

export const DEVICE_HEALTH_LABELS: Record<DeviceHealth, string> = {
  healthy: 'Healthy',
  degraded: 'Degraded',
  unhealthy: 'Unhealthy',
  unknown: 'Unknown',
};

export const AGENT_COMMAND_STATE_LABELS: Record<AgentCommandState, string> = {
  pending: 'Pending',
  sent: 'Sent',
  accepted: 'Accepted',
  running: 'Running',
  succeeded: 'Succeeded',
  failed: 'Failed',
  rejected: 'Rejected',
  timed_out: 'Timed out',
  cancelled: 'Cancelled',
};

/** Install targets offered by the join flow. */
export type InstallPlatform = 'linux' | 'macos' | 'kubernetes';

export const INSTALL_PLATFORM_LABELS: Record<InstallPlatform, string> = {
  linux: 'Linux (systemd)',
  macos: 'macOS (launchd)',
  kubernetes: 'Kubernetes (DaemonSet)',
};
