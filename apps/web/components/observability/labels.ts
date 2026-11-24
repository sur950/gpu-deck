/**
 * Display names for contract enums and entities on the dashboards,
 * observability and topology screens.
 */

import type { components } from '@/lib/api/generated/admin';

type Schemas = components['schemas'];

export const ENGINE_LABELS: Record<Schemas['engine_name'], string> = {
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

export function engineLabel(engine: string | null | undefined): string {
  if (!engine) return '—';
  return ENGINE_LABELS[engine as Schemas['engine_name']] ?? engine;
}

export const VENDOR_LABELS: Record<Schemas['device_vendor'], string> = {
  nvidia: 'NVIDIA',
  amd: 'AMD',
  hygon: 'Hygon',
  metax: 'MetaX',
  moorethreads: 'Moore Threads',
  thead: 'T-Head',
  apple: 'Apple',
};

export const FORMAT_LABELS: Record<Schemas['usage_format'], string> = {
  openai: 'OpenAI',
  anthropic: 'Anthropic',
  ollama: 'Ollama',
};

export const SWAP_KIND_LABELS: Record<Schemas['swap_kind'], string> = {
  load: 'Load',
  unload: 'Unload',
  swap: 'Swap',
  scale_up: 'Scale up',
  scale_down: 'Scale down',
  restart: 'Restart',
  move: 'Move',
  apply_layout: 'Apply layout',
};

export const SWAP_STATE_LABELS: Record<Schemas['swap_state'], string> = {
  pending: 'Pending',
  draining: 'Draining',
  unloading: 'Unloading',
  loading: 'Loading',
  health_check: 'Health check',
  completed: 'Completed',
  failed: 'Failed',
  rolling_back: 'Rolling back',
  rolled_back: 'Rolled back',
  cancelled: 'Cancelled',
};

export const SWAP_TRIGGER_LABELS: Record<Schemas['swap_trigger'], string> = {
  manual: 'Manual',
  rule: 'Rule',
  layout: 'Layout',
  auto_heal: 'Auto heal',
  api: 'API',
  benchmark: 'Benchmark',
};

export const OUTCOME_LABELS: Record<Schemas['rule_run_outcome'], string> = {
  no_match: 'No match',
  cooldown: 'Cooldown',
  fired: 'Fired',
  dry_run: 'Dry run',
  skipped: 'Skipped',
  error: 'Error',
};

export const RULE_TYPE_LABELS: Record<Schemas['rule_type'], string> = {
  idle_unload: 'Idle unload',
  time_schedule: 'Time schedule',
  low_traffic_shrink: 'Low-traffic shrink',
  high_load_grow: 'High-load grow',
  make_room_by_priority: 'Make room by priority',
  auto_heal: 'Auto heal',
};

export const INSTANCE_STATE_LABELS: Record<Schemas['instance_state'], string> = {
  pending: 'Pending',
  loading: 'Loading',
  ready: 'Ready',
  draining: 'Draining',
  unloading: 'Unloading',
  stopped: 'Stopped',
  error: 'Error',
};

export const NODE_STATUS_LABELS: Record<Schemas['node_status'], string> = {
  pending: 'Pending',
  online: 'Online',
  offline: 'Offline',
  unhealthy: 'Unhealthy',
  draining: 'Draining',
  drained: 'Drained',
  removed: 'Removed',
};

export const HEALTH_LABELS: Record<Schemas['device_health'], string> = {
  healthy: 'Healthy',
  degraded: 'Degraded',
  unhealthy: 'Unhealthy',
  unknown: 'Unknown',
};

/** "NVIDIA L40S" → "L40S", "Apple M5 (10-core GPU)" → "Apple M5". */
export function gpuModelName(name: string): string {
  return name.replace(/^(NVIDIA|AMD|Hygon|MetaX|Moore Threads)\s+/i, '').replace(/\s*\(.*\)\s*$/, '').trim();
}

/** "L40S #0" — the index only matters when a node has several cards. */
export function gpuLabel(device: { name: string; index: number }, showIndex = true): string {
  const model = gpuModelName(device.name);
  return showIndex ? `${model} #${device.index}` : model;
}

/** "gpu-box-01 · L40S #0" */
export function gpuFullLabel(device: { name: string; index: number; nodeName: string }): string {
  return `${device.nodeName} · ${gpuLabel(device)}`;
}
