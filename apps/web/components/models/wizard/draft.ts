/**
 * Deployment wizard draft: the form state for every step, how it maps to the
 * DeploymentCreate / DeploymentUpdate payloads, and how an existing
 * deployment (edit mode) or URL presets (deploy from a GPU / node / model
 * page) seed it.
 */

import { recordToRows, rowsToFlags, rowsToRecord, type KeyValueRow } from '@/components/shared/key-value-editor';
import type { DeviceVendor } from '@/types/fleet';
import type { Deployment, DeploymentCreate, DeploymentPreset, DeploymentSpec, DeploymentUpdate, Engine, ModelPriority, PlacementStrategy } from '@/types/models';

const GiB = 1024 ** 3;

export type SpecMethod = 'draft_model' | 'ngram' | 'eagle' | 'mtp';
export type KvBackend = 'lmcache' | 'mooncake' | 'native';
export type PdTransfer = 'nixl' | 'mooncake';
export type TrafficStrategyName = 'least_sessions' | 'round_robin' | 'weighted';

export interface ControlVectorRow {
  path: string;
  scale: string;
  from: string;
  to: string;
}

export interface WizardDraft {
  name: string;
  description: string;
  modelId: string;
  /** '' = the model's default version. */
  versionId: string;
  /** '' = auto-pick at schedule time. */
  engineVersionId: string;
  clusterId: string;
  replicas: number;
  minReplicas: number;
  maxReplicas: number;
  gpuCount: number;
  tpSize: number;
  ppSize: number;
  strategy: PlacementStrategy;
  vendors: DeviceVendor[];
  pinNodeIds: string[];
  pinDeviceIds: string[];
  labelSelector: KeyValueRow[];
  sliceEnabled: boolean;
  sliceGiB: number;
  slicePct: number;
  preset: DeploymentPreset | null;
  pdSplit: boolean;
  prefillReplicas: number;
  decodeReplicas: number;
  pdTransfer: PdTransfer;
  specEnabled: boolean;
  specMethod: SpecMethod;
  draftVersionId: string;
  numDraftTokens: number;
  kvSharing: boolean;
  kvBackend: KvBackend;
  contextSize: string;
  flags: KeyValueRow[];
  toolParser: string;
  controlVectors: ControlVectorRow[];
  envVars: KeyValueRow[];
  priorityOverride: ModelPriority | '';
  trafficStrategy: TrafficStrategyName;
  start: boolean;
  /**
   * Edit mode: the deployment's JSON settings as stored. The wizard only edits some keys of
   * these objects; the PATCH sends the whole object, so every key it does not edit (traffic
   * weights, a KV remote URL, …) is carried over instead of being reset.
   */
  stored: StoredConfigs;
}

export interface StoredConfigs {
  pdConfig: Record<string, unknown>;
  specDecoding: Record<string, unknown>;
  kvSharingConfig: Record<string, unknown>;
  trafficConfig: Record<string, unknown>;
}

export const EMPTY_DRAFT: WizardDraft = {
  name: '',
  description: '',
  modelId: '',
  versionId: '',
  engineVersionId: '',
  clusterId: '',
  replicas: 1,
  minReplicas: 0,
  maxReplicas: 2,
  gpuCount: 1,
  tpSize: 1,
  ppSize: 1,
  strategy: 'spread',
  vendors: [],
  pinNodeIds: [],
  pinDeviceIds: [],
  labelSelector: [],
  sliceEnabled: false,
  sliceGiB: 8,
  slicePct: 100,
  preset: 'low_latency',
  pdSplit: false,
  prefillReplicas: 1,
  decodeReplicas: 1,
  pdTransfer: 'nixl',
  specEnabled: false,
  specMethod: 'ngram',
  draftVersionId: '',
  numDraftTokens: 4,
  kvSharing: false,
  kvBackend: 'lmcache',
  contextSize: '',
  flags: [],
  toolParser: '',
  controlVectors: [],
  envVars: [],
  priorityOverride: '',
  trafficStrategy: 'least_sessions',
  start: true,
  stored: { pdConfig: {}, specDecoding: {}, kvSharingConfig: {}, trafficConfig: {} },
};

/** Seed from query params: ?model_id&node_id&device_id&cluster_id&slice_gib&slice_pct. */
export function draftFromParams(params: URLSearchParams): WizardDraft {
  const draft: WizardDraft = { ...EMPTY_DRAFT };
  const model = params.get('model_id');
  const node = params.get('node_id');
  const device = params.get('device_id');
  const cluster = params.get('cluster_id');
  const sliceGiB = Number(params.get('slice_gib'));
  const slicePct = Number(params.get('slice_pct'));
  if (model) draft.modelId = model;
  if (cluster) draft.clusterId = cluster;
  if (node) draft.pinNodeIds = [node];
  if (device) {
    draft.pinDeviceIds = [device];
    draft.replicas = 1;
    draft.maxReplicas = 1;
  }
  if (sliceGiB > 0) {
    draft.sliceEnabled = true;
    draft.sliceGiB = sliceGiB;
    draft.slicePct = slicePct > 0 ? slicePct : 100;
  }
  return draft;
}

function num(v: unknown, fallback: number): number {
  return typeof v === 'number' && Number.isFinite(v) ? v : fallback;
}

/** Edit mode: turn a deployment back into wizard state. */
export function draftFromDeployment(d: Deployment): WizardDraft {
  const selector = (d.node_selector ?? {}) as Record<string, unknown>;
  const pd = (d.pd_config ?? {}) as Record<string, unknown>;
  const spec = (d.spec_decoding ?? {}) as Record<string, unknown>;
  const kv = (d.kv_sharing_config ?? {}) as Record<string, unknown>;
  const traffic = (d.traffic_config ?? {}) as Record<string, unknown>;
  return {
    ...EMPTY_DRAFT,
    name: d.name,
    description: d.description ?? '',
    modelId: d.model_id ?? '',
    versionId: d.model_version_id ?? '',
    engineVersionId: d.engine_version_id ?? '',
    clusterId: d.cluster_id ?? '',
    replicas: d.replicas,
    minReplicas: d.min_replicas,
    maxReplicas: d.max_replicas,
    gpuCount: d.gpu_count,
    tpSize: d.tp_size,
    ppSize: d.pp_size,
    strategy: d.placement_strategy ?? 'spread',
    vendors: d.device_vendor_filter ?? [],
    pinNodeIds: Array.isArray(selector.node_ids) ? (selector.node_ids as string[]) : [],
    pinDeviceIds: Array.isArray(selector.device_ids) ? (selector.device_ids as string[]) : [],
    labelSelector: Object.entries(selector)
      .filter(([k]) => k !== 'node_ids' && k !== 'device_ids')
      .map(([key, value]) => ({ key, value: String(value) })),
    sliceEnabled: !!(d.slice_vram_bytes || d.slice_compute_pct),
    // Exact (not rounded): the PATCH sends the slice back, and a rounded value would resize it.
    sliceGiB: d.slice_vram_bytes ? d.slice_vram_bytes / GiB : 8,
    slicePct: d.slice_compute_pct ?? 100,
    preset: d.preset ?? null,
    pdSplit: d.pd_split,
    prefillReplicas: num(pd.prefill_replicas, 1),
    decodeReplicas: num(pd.decode_replicas, 1),
    pdTransfer: pd.transfer === 'mooncake' ? 'mooncake' : 'nixl',
    specEnabled: spec.enabled === true,
    specMethod: (['draft_model', 'ngram', 'eagle', 'mtp'] as SpecMethod[]).includes(spec.method as SpecMethod) ? (spec.method as SpecMethod) : 'ngram',
    draftVersionId: typeof spec.draft_model_version_id === 'string' ? spec.draft_model_version_id : '',
    numDraftTokens: num(spec.num_draft_tokens, 4),
    kvSharing: d.kv_sharing,
    kvBackend: (['lmcache', 'mooncake', 'native'] as KvBackend[]).includes(kv.backend as KvBackend) ? (kv.backend as KvBackend) : 'lmcache',
    contextSize: d.context_size ? String(d.context_size) : '',
    flags: recordToRows(d.launch_flags),
    toolParser: d.tool_parser ?? '',
    controlVectors: (d.control_vectors ?? []).map((cv) => {
      const range = Array.isArray(cv.layer_range) ? (cv.layer_range as number[]) : [];
      return { path: String(cv.path ?? ''), scale: String(cv.scale ?? '1'), from: range[0] !== undefined ? String(range[0]) : '', to: range[1] !== undefined ? String(range[1]) : '' };
    }),
    envVars: recordToRows(d.env_vars),
    priorityOverride: d.priority_override ?? '',
    trafficStrategy: (['least_sessions', 'round_robin', 'weighted'] as TrafficStrategyName[]).includes(traffic.strategy as TrafficStrategyName) ? (traffic.strategy as TrafficStrategyName) : 'least_sessions',
    start: d.desired_running ?? true,
    stored: { pdConfig: pd, specDecoding: spec, kvSharingConfig: kv, trafficConfig: traffic },
  };
}

export function effectiveGpuCount(draft: WizardDraft): number {
  return Math.max(draft.gpuCount, draft.tpSize * draft.ppSize);
}

/** The spec both create and preview send. */
export function toSpec(draft: WizardDraft): DeploymentSpec {
  const selector: Record<string, unknown> = rowsToRecord(draft.labelSelector);
  if (draft.pinNodeIds.length) selector.node_ids = draft.pinNodeIds;
  if (draft.pinDeviceIds.length) selector.device_ids = draft.pinDeviceIds;
  const stored = draft.stored ?? EMPTY_DRAFT.stored;
  return {
    name: draft.name.trim(),
    description: draft.description.trim() || null,
    model_id: draft.modelId,
    model_version_id: draft.versionId || null,
    engine_version_id: draft.engineVersionId || null,
    cluster_id: draft.clusterId || null,
    replicas: draft.replicas,
    min_replicas: draft.minReplicas,
    max_replicas: Math.max(draft.maxReplicas, draft.replicas),
    gpu_count: effectiveGpuCount(draft),
    tp_size: draft.tpSize,
    pp_size: draft.ppSize,
    placement_strategy: draft.strategy,
    device_vendor_filter: draft.vendors,
    node_selector: selector,
    slice_vram_bytes: draft.sliceEnabled ? Math.round(draft.sliceGiB * GiB) : null,
    slice_compute_pct: draft.sliceEnabled ? draft.slicePct : null,
    preset: draft.preset,
    pd_split: draft.pdSplit,
    pd_config: draft.pdSplit ? { ...stored.pdConfig, prefill_replicas: draft.prefillReplicas, decode_replicas: draft.decodeReplicas, transfer: draft.pdTransfer } : {},
    spec_decoding: draft.specEnabled
      ? { ...stored.specDecoding, enabled: true, method: draft.specMethod, num_draft_tokens: draft.numDraftTokens, draft_model_version_id: draft.specMethod === 'draft_model' ? draft.draftVersionId || null : null }
      : {},
    kv_sharing: draft.kvSharing,
    kv_sharing_config: draft.kvSharing ? { remote_url: null, ...stored.kvSharingConfig, backend: draft.kvBackend } : {},
    context_size: draft.contextSize ? Number(draft.contextSize) : null,
    launch_flags: rowsToFlags(draft.flags),
    tool_parser: draft.toolParser.trim() || null,
    control_vectors: draft.controlVectors
      .filter((cv) => cv.path.trim())
      .map((cv) => ({ path: cv.path.trim(), scale: Number(cv.scale) || 1, ...(cv.from && cv.to ? { layer_range: [Number(cv.from), Number(cv.to)] } : {}) })),
    env_vars: rowsToRecord(draft.envVars),
    priority_override: draft.priorityOverride || null,
    traffic_config: { failover: true, max_retries: 1, health_eject: true, ...stored.trafficConfig, strategy: draft.trafficStrategy },
  };
}

export function toCreate(draft: WizardDraft, dryRun = false): DeploymentCreate {
  return { ...toSpec(draft), start: draft.start, dry_run: dryRun };
}

/** PATCH body: the full spec minus the model (fixed after creation); unchanged fields are no-ops server-side. */
export function toUpdate(draft: WizardDraft): DeploymentUpdate {
  const spec = toSpec(draft);
  const { model_id: _model, ...rest } = spec;
  void _model;
  return rest;
}

export interface EngineCaps {
  tensorParallel: boolean;
  pipelineParallel: boolean;
  pdSplit: boolean;
  specDecoding: boolean;
  kvSharing: boolean;
  slices: boolean;
  controlVectors: boolean;
  routerMode: boolean;
}

export function capsOf(engine: Engine | null | undefined): EngineCaps {
  const c = (engine?.capabilities ?? {}) as Record<string, unknown>;
  return {
    tensorParallel: c.tensor_parallel === true,
    pipelineParallel: c.pipeline_parallel === true,
    pdSplit: c.pd_split === true,
    specDecoding: c.speculative_decoding === true,
    kvSharing: c.kv_sharing === true,
    slices: c.slices === true,
    controlVectors: c.control_vectors === true,
    routerMode: c.router_mode === true,
  };
}

export const SERVING_NAME_RE = /^[a-z0-9][a-z0-9._-]{0,62}$/;
