/**
 * Model types (catalog, versions, engines, deployments, instances, model
 * cache, benchmarks). Contract shapes are the generated OpenAPI types in
 * snake_case — see types/fleet.ts for why they are not re-cased.
 */

import type { components } from '@/lib/api/generated/admin';
import type { DeviceVendor } from '@/types/fleet';

type S = components['schemas'];

// ─── Contract aliases ─────────────────────────────────────────────────────────

export type Model = S['Model'];
export type ModelCreate = S['ModelCreate'];
export type ModelUpdate = S['ModelUpdate'];
export type ModelVersion = S['ModelVersion'];
export type ModelVersionCreate = S['ModelVersionCreate'];
export type ModelFile = S['ModelFile'];
export type ModelType = S['model_type'];
export type ModelSource = S['model_source'];
export type ModelPriority = S['model_priority'];
export type ModelFileFormat = S['model_file_format'];
export type ModelVersionStatus = S['model_version_status'];
export type VerifyResult = S['VerifyResult'];

export type CatalogSearchResult = S['CatalogSearchResult'];
export type CatalogRemoteFile = S['CatalogRemoteFile'];
export type CatalogRemoteFiles = S['CatalogRemoteFiles'];
export type LocalScanCandidate = S['LocalScanCandidate'];
export type LocalScanRequest = S['LocalScanRequest'];
export type RemoteSource = 'huggingface' | 'modelscope';

export type Engine = S['Engine'];
export type EngineName = S['engine_name'];
export type EngineRuntime = S['engine_runtime'];
export type EngineUpdate = S['EngineUpdate'];
export type EngineVersion = S['EngineVersion'];
export type EngineVersionCreate = S['EngineVersionCreate'];
export type EngineVersionUpdate = S['EngineVersionUpdate'];
export type EngineVersionStatus = S['engine_version_status'];
export type CompatCheck = S['CompatCheck'];
export type CompatCheckRequest = S['CompatCheckRequest'];
export type CompatResult = S['compat_result'];
export type AutoPickRequest = S['AutoPickRequest'];
export type AutoPickResult = S['AutoPickResult'];

export type Deployment = S['Deployment'];
/** managed = GPU Deck runs it; external = an observed server GPU Deck did not start (read-only). */
export type DeploymentOrigin = S['deployment_origin'];
export type ExternalInfo = S['ExternalInfo'];
export type DeploymentSpec = S['DeploymentSpec'];
export type DeploymentCreate = S['DeploymentCreate'];
export type DeploymentUpdate = S['DeploymentUpdate'];
export type DeploymentStatus = S['deployment_status'];
export type DeploymentPreset = S['deployment_preset'];
export type PlacementStrategy = S['placement_strategy'];
export type PlacementPreview = S['PlacementPreview'];
export type PlacementCandidate = S['PlacementCandidate'];
export type PlacementPreviewRequest = S['PlacementPreviewRequest'];
export type ScaleRequest = S['ScaleRequest'];
export type TrafficWeightsRequest = S['TrafficWeightsRequest'];
export type TrafficStrategy = NonNullable<TrafficWeightsRequest['strategy']>;
export type SwapOperation = S['SwapOperation'];

export type Instance = S['Instance'];
export type InstanceLive = S['InstanceLive'];
export type InstanceState = S['instance_state'];
export type InstanceHealth = S['instance_health'];
export type InstanceDrainRequest = S['InstanceDrainRequest'];
export type SessionsSnapshot = S['SessionsSnapshot'];
export type SessionSample = S['SessionSample'];

export type CacheItem = S['CacheItem'];
export type CacheState = S['cache_state'];
export type CacheSummary = S['CacheSummary'];
export type PrefetchRequest = S['PrefetchRequest'];
export type EvictRequest = S['EvictRequest'];

export type Benchmark = S['Benchmark'];
export type BenchmarkCreate = S['BenchmarkCreate'];
export type BenchmarkUpdate = S['BenchmarkUpdate'];
export type BenchmarkRun = S['BenchmarkRun'];
export type BenchmarkRunStatus = S['benchmark_run_status'];
export type BenchmarkResult = BenchmarkRun['results'][number];
export type BenchmarkStartRequest = S['BenchmarkStartRequest'];
export type ApplyRecommendationRequest = S['ApplyRecommendationRequest'];

export type SystemInfo = S['SystemInfo'];
export type CommandRefs = S['CommandRefs'];

// ─── Query params ─────────────────────────────────────────────────────────────

export interface ModelListParams {
  page?: number;
  page_size?: number;
  search?: string;
  sort?: string;
  source?: ModelSource | '';
  type?: ModelType | '';
  priority?: ModelPriority | '';
  family?: string;
  tag?: string;
}

export interface CatalogSearchParams {
  q: string;
  source: RemoteSource;
  type?: ModelType | '';
  format?: ModelFileFormat | '';
  author?: string;
  sort?: 'downloads' | 'likes' | 'last_modified' | 'relevance';
  limit?: number;
  /** Vault token credential (huggingface / modelscope kind); the default token otherwise. */
  credential_id?: string;
}

export interface DeploymentListParams {
  page?: number;
  page_size?: number;
  search?: string;
  sort?: string;
  status?: DeploymentStatus | '';
  engine?: EngineName | '';
  priority?: ModelPriority | '';
  cluster_id?: string;
  model_id?: string;
  /** managed | external (observed servers GPU Deck did not start). */
  origin?: DeploymentOrigin | '';
}

export interface InstanceListParams {
  page?: number;
  page_size?: number;
  sort?: string;
  deployment_id?: string;
  node_id?: string;
  state?: InstanceState | '';
  health?: InstanceHealth | '';
  include_stopped?: boolean;
  origin?: DeploymentOrigin | '';
}

export interface CacheListParams {
  page?: number;
  page_size?: number;
  sort?: string;
  node_id?: string;
  model_id?: string;
  model_version_id?: string;
  state?: CacheState | '';
  pinned?: boolean | '';
  unused_for_hours?: number;
}

export interface BenchmarkListParams {
  page?: number;
  page_size?: number;
  search?: string;
  sort?: string;
  deployment_id?: string;
}

export interface CompatCheckListParams {
  page?: number;
  page_size?: number;
  model_version_id?: string;
  engine_version_id?: string;
  result?: CompatResult | '';
}

export interface EngineListParams {
  enabled?: boolean;
  model_type?: ModelType | '';
  vendor?: DeviceVendor | '';
}

// ─── Labels and option lists ──────────────────────────────────────────────────

export const MODEL_TYPE_LABELS: Record<ModelType, string> = {
  chat: 'Chat',
  thinking: 'Thinking',
  vision: 'Vision',
  embedding: 'Embedding',
  rerank: 'Rerank',
  stt: 'Speech to text',
  tts: 'Text to speech',
  image: 'Image',
  video: 'Video',
};

export const MODEL_TYPE_OPTIONS = (Object.keys(MODEL_TYPE_LABELS) as ModelType[]).map((value) => ({
  value,
  label: MODEL_TYPE_LABELS[value],
}));

export const MODEL_SOURCE_LABELS: Record<ModelSource, string> = {
  huggingface: 'Hugging Face',
  modelscope: 'ModelScope',
  local_path: 'Local path',
};

export const MODEL_PRIORITY_LABELS: Record<ModelPriority, string> = {
  always_on: 'Always on',
  normal: 'Normal',
  evictable: 'Evictable',
};

export const MODEL_PRIORITY_DESCRIPTIONS: Record<ModelPriority, string> = {
  always_on: 'Rules never unload it; make-room never picks it.',
  normal: 'Default. Idle-unload and shrink rules may act on it.',
  evictable: 'First to go when a higher-priority model needs VRAM.',
};

export const MODEL_PRIORITY_OPTIONS = (Object.keys(MODEL_PRIORITY_LABELS) as ModelPriority[]).map((value) => ({
  value,
  label: MODEL_PRIORITY_LABELS[value],
}));

export const MODEL_FORMAT_LABELS: Record<ModelFileFormat, string> = {
  gguf: 'GGUF',
  safetensors: 'Safetensors',
  mlx: 'MLX',
  pickle: 'Pickle',
  other: 'Other',
};

export const MODEL_VERSION_STATUS_LABELS: Record<ModelVersionStatus, string> = {
  registered: 'Registered',
  verifying: 'Verifying',
  ready: 'Ready',
  missing: 'Missing',
  blocked: 'Blocked',
};

export const ENGINE_LABELS: Record<EngineName, string> = {
  llama_cpp: 'llama.cpp',
  vllm: 'vLLM',
  vllm_omni: 'vLLM-Omni',
  sglang: 'SGLang',
  sglang_diffusion: 'SGLang Diffusion',
  tensorrt_llm: 'TensorRT-LLM',
  mlx: 'MLX',
  mlx_audio: 'mlx-audio',
  whisper_cpp: 'whisper.cpp',
  stable_diffusion_cpp: 'stable-diffusion.cpp',
  custom: 'Custom',
};

export const EXTERNAL_ENGINE_LABELS: Record<ExternalInfo['engine'], string> = {
  vllm: 'vLLM',
  sglang: 'SGLang',
  llamacpp: 'llama.cpp',
  tgi: 'TGI',
  openai: 'OpenAI-compatible',
  generic: 'Generic HTTP',
};

/** True for a deployment / instance GPU Deck only observes (never changes). */
export function isExternal(row: { origin?: DeploymentOrigin | null } | null | undefined): boolean {
  return row?.origin === 'external';
}

export const DEPLOYMENT_STATUS_LABELS: Record<DeploymentStatus, string> = {
  pending: 'Pending',
  scheduling: 'Scheduling',
  deploying: 'Deploying',
  running: 'Running',
  degraded: 'Degraded',
  scaling: 'Scaling',
  stopped: 'Stopped',
  failed: 'Failed',
  deleting: 'Deleting',
};

export const INSTANCE_STATE_LABELS: Record<InstanceState, string> = {
  pending: 'Pending',
  loading: 'Loading',
  ready: 'Ready',
  draining: 'Draining',
  unloading: 'Unloading',
  stopped: 'Stopped',
  error: 'Error',
};

export const CACHE_STATE_LABELS: Record<CacheState, string> = {
  queued: 'Queued',
  downloading: 'Downloading',
  ready: 'Ready',
  evicting: 'Evicting',
  evicted: 'Evicted',
  error: 'Error',
};

export const BENCHMARK_RUN_STATUS_LABELS: Record<BenchmarkRunStatus, string> = {
  pending: 'Pending',
  running: 'Running',
  completed: 'Completed',
  failed: 'Failed',
  cancelled: 'Cancelled',
};

export const PRESET_LABELS: Record<DeploymentPreset, string> = {
  low_latency: 'Low latency',
  high_throughput: 'High throughput',
};

export const PRESET_DESCRIPTIONS: Record<DeploymentPreset, string> = {
  low_latency: 'Small batches, short queues: best time to first token for interactive chat.',
  high_throughput: 'Large batches and continuous batching: most tokens per second for bulk work.',
};

/** Tool-call parsers per engine (the value is passed through as the launch setting). */
export const TOOL_PARSER_OPTIONS: Record<string, { value: string; label: string }[]> = {
  llama_cpp: [
    { value: 'jinja', label: 'Jinja chat template (--jinja)' },
    { value: 'none', label: 'None' },
  ],
  vllm: [
    { value: 'hermes', label: 'Hermes' },
    { value: 'llama3_json', label: 'Llama 3 JSON' },
    { value: 'mistral', label: 'Mistral' },
    { value: 'qwen', label: 'Qwen' },
    { value: 'deepseek_v3', label: 'DeepSeek V3' },
    { value: 'granite', label: 'Granite' },
    { value: 'internlm', label: 'InternLM' },
    { value: 'none', label: 'None' },
  ],
  sglang: [
    { value: 'qwen25', label: 'Qwen 2.5' },
    { value: 'llama3', label: 'Llama 3' },
    { value: 'mistral', label: 'Mistral' },
    { value: 'deepseekv3', label: 'DeepSeek V3' },
    { value: 'none', label: 'None' },
  ],
  mlx: [
    { value: 'auto', label: 'Auto (chat template)' },
    { value: 'none', label: 'None' },
  ],
  default: [
    { value: 'auto', label: 'Auto' },
    { value: 'none', label: 'None' },
  ],
};

export function toolParserOptions(engine: EngineName | null | undefined) {
  return TOOL_PARSER_OPTIONS[engine ?? 'default'] ?? TOOL_PARSER_OPTIONS.default;
}

/** Serving formats offered as endpoint snippets on the deployment page. */
export type ServingFormat = 'openai' | 'anthropic' | 'ollama';

export const SERVING_FORMAT_LABELS: Record<ServingFormat, string> = {
  openai: 'OpenAI',
  anthropic: 'Anthropic',
  ollama: 'Ollama',
};
