/**
 * Time-series generators for GPU, engine, session and usage metrics.
 *
 * Every value is a function of (metric, entity, timestamp) so a chart that
 * refetches keeps its shape, the live SSE frame agrees with the last chart
 * point, and the dashboard tiles agree with the observability pages.
 * Metric names follow libs/contracts/metrics.md.
 */

import { IDS, DEVICES, INSTANCES, GiB } from '@/lib/mocks/fixtures/fleet';
import { clamp, fbm, hash32, round } from '@/lib/mocks/fixtures/random';
import type { Schemas } from '@/lib/mocks/types';

// ─── Step handling ────────────────────────────────────────────────────────────

export const STEP_SECONDS: Record<string, number> = {
  '2s': 2,
  '15s': 15,
  '1m': 60,
  '5m': 300,
  '15m': 900,
  '1h': 3600,
  '1d': 86400,
};

export function stepToSeconds(step: string | null | undefined, fallback = 60): number {
  if (!step) return fallback;
  return STEP_SECONDS[step] ?? fallback;
}

export interface RangeQuery {
  from: Date;
  to: Date;
  stepSeconds: number;
  step: string;
}

/** Parse `from` / `to` / `step` with the contract defaults (now-1h, now, 1m). */
export function parseRange(query: URLSearchParams, now = Date.now()): RangeQuery {
  const to = query.get('to') ? new Date(query.get('to') as string) : new Date(now);
  const from = query.get('from') ? new Date(query.get('from') as string) : new Date(to.getTime() - 3_600_000);
  const step = query.get('step') ?? '1m';
  let stepSeconds = stepToSeconds(step);
  // Keep series at a sane size: widen the bucket if a range asks for > 720 points.
  const span = (to.getTime() - from.getTime()) / 1000;
  while (span / stepSeconds > 720) stepSeconds *= 2;
  return { from, to, stepSeconds, step };
}

// ─── Device profiles ──────────────────────────────────────────────────────────

interface DeviceProfile {
  utilBase: number;
  utilSwing: number;
  memBase: number; // bytes
  memSwing: number;
  memTotal: number;
  powerIdle: number;
  powerMax: number;
  tempIdle: number;
  tempMax: number;
  smClock: [number, number];
  memClock: number;
  fan: boolean;
  errorsPerHour: number;
}

const DEVICE_PROFILES: Record<string, DeviceProfile> = {
  [IDS.devices.m5]: {
    utilBase: 38,
    utilSwing: 22,
    memBase: 11.2 * GiB,
    memSwing: 1.4 * GiB,
    memTotal: 24 * GiB,
    powerIdle: 4,
    powerMax: 22,
    tempIdle: 42,
    tempMax: 71,
    smClock: [0, 0],
    memClock: 0,
    fan: false,
    errorsPerHour: 0,
  },
  [IDS.devices.l40s0]: {
    utilBase: 61,
    utilSwing: 24,
    memBase: 31.4 * GiB,
    memSwing: 2.6 * GiB,
    memTotal: 44.99 * GiB,
    powerIdle: 68,
    powerMax: 300,
    tempIdle: 36,
    tempMax: 79,
    smClock: [1200, 2520],
    memClock: 9001,
    fan: true,
    errorsPerHour: 0.3,
  },
  [IDS.devices.l40s1]: {
    utilBase: 17,
    utilSwing: 12,
    memBase: 8.9 * GiB,
    memSwing: 0.6 * GiB,
    memTotal: 44.99 * GiB,
    powerIdle: 68,
    powerMax: 300,
    tempIdle: 36,
    tempMax: 79,
    smClock: [1200, 2520],
    memClock: 9001,
    fan: true,
    errorsPerHour: 0,
  },
};

/** Diurnal traffic shape: busy from 09:00, peak 15:00, quiet overnight (local time). */
function diurnal(ts: number): number {
  const d = new Date(ts);
  const hour = d.getHours() + d.getMinutes() / 60;
  const x = ((hour - 15) / 24) * Math.PI * 2;
  return 0.5 + 0.5 * Math.cos(x);
}

function noise(seed: string, ts: number, periodSeconds: number, octaves = 3): number {
  return fbm(hash32(seed), ts / 1000 / periodSeconds, octaves);
}

export function gpuUtilization(deviceId: string, ts: number): number {
  const p = DEVICE_PROFILES[deviceId];
  if (!p) return 0;
  const base = p.utilBase * (0.55 + 0.45 * diurnal(ts));
  const wobble = noise(`util:${deviceId}`, ts, 240) * p.utilSwing;
  const spikes = Math.max(0, noise(`spike:${deviceId}`, ts, 37, 2)) * 18;
  return round(clamp(base + wobble + spikes, 0, 100), 1);
}

export function gpuMemoryUsed(deviceId: string, ts: number): number {
  const p = DEVICE_PROFILES[deviceId];
  if (!p) return 0;
  const drift = noise(`mem:${deviceId}`, ts, 1800, 2) * p.memSwing;
  const kv = (gpuUtilization(deviceId, ts) / 100) * 0.08 * p.memTotal;
  return Math.round(clamp(p.memBase + drift + kv, 0.2 * GiB, p.memTotal * 0.97));
}

export function gpuMemoryTotal(deviceId: string): number {
  return DEVICE_PROFILES[deviceId]?.memTotal ?? 0;
}

export function gpuPower(deviceId: string, ts: number): number {
  const p = DEVICE_PROFILES[deviceId];
  if (!p) return 0;
  const util = gpuUtilization(deviceId, ts) / 100;
  const watts = p.powerIdle + (p.powerMax - p.powerIdle) * Math.pow(util, 1.15) + noise(`pw:${deviceId}`, ts, 20, 2) * (p.powerMax - p.powerIdle) * 0.025;
  return round(clamp(watts, p.powerIdle * 0.9, p.powerMax), 1);
}

export function gpuTemperature(deviceId: string, ts: number): number {
  const p = DEVICE_PROFILES[deviceId];
  if (!p) return 0;
  // Temperature lags utilisation: sample util a few minutes back.
  const util = gpuUtilization(deviceId, ts - 180_000) / 100;
  const c = p.tempIdle + (p.tempMax - p.tempIdle) * Math.pow(util, 0.8) + noise(`tp:${deviceId}`, ts, 300, 2) * 2.5;
  return round(clamp(c, p.tempIdle - 2, p.tempMax + 3), 1);
}

export function gpuSmClock(deviceId: string, ts: number): number | null {
  const p = DEVICE_PROFILES[deviceId];
  if (!p || p.smClock[1] === 0) return null;
  const util = gpuUtilization(deviceId, ts) / 100;
  return Math.round(p.smClock[0] + (p.smClock[1] - p.smClock[0]) * Math.min(1, util * 1.4 + 0.15));
}

export function gpuErrorCount(deviceId: string, ts: number, bucketSeconds: number): number {
  const p = DEVICE_PROFILES[deviceId];
  if (!p || p.errorsPerHour === 0) return 0;
  // Sparse deterministic error events: one every ~(3600 / rate) seconds.
  const interval = Math.round(3600 / p.errorsPerHour);
  const bucket = Math.floor(ts / 1000 / bucketSeconds);
  const seed = hash32(`err:${deviceId}:${Math.floor((bucket * bucketSeconds) / interval)}`);
  const hit = seed % Math.max(1, Math.round(interval / bucketSeconds)) === 0;
  return hit ? 1 : 0;
}

export function gpuFan(deviceId: string, ts: number): number | null {
  const p = DEVICE_PROFILES[deviceId];
  if (!p || !p.fan) return null;
  const t = gpuTemperature(deviceId, ts);
  return Math.round(clamp(((t - 30) / 50) * 100, 20, 100));
}

export function gpuSample(deviceId: string, ts: number): Record<string, unknown> {
  const p = DEVICE_PROFILES[deviceId];
  const memUsed = gpuMemoryUsed(deviceId, ts);
  const memTotal = gpuMemoryTotal(deviceId);
  return {
    ts: new Date(ts).toISOString(),
    device_id: deviceId,
    utilization_pct: gpuUtilization(deviceId, ts),
    memory_util_pct: p?.smClock[1] ? round(gpuUtilization(deviceId, ts) * 0.72, 1) : null,
    memory_used_bytes: memUsed,
    memory_total_bytes: memTotal,
    power_watts: gpuPower(deviceId, ts),
    power_limit_watts: p?.smClock[1] ? 300 : null,
    temperature_c: gpuTemperature(deviceId, ts),
    memory_temperature_c: p?.smClock[1] ? round(gpuTemperature(deviceId, ts) + 6, 1) : null,
    sm_clock_mhz: gpuSmClock(deviceId, ts),
    memory_clock_mhz: p?.smClock[1] ? p.memClock : null,
    fan_pct: gpuFan(deviceId, ts),
    encoder_pct: p?.smClock[1] ? 0 : null,
    decoder_pct: p?.smClock[1] ? 0 : null,
    pcie_tx_bytes_per_s: p?.smClock[1] ? Math.round(gpuUtilization(deviceId, ts) * 1.8e6) : null,
    pcie_rx_bytes_per_s: p?.smClock[1] ? Math.round(gpuUtilization(deviceId, ts) * 4.1e6) : null,
    ecc_errors_total: p?.smClock[1] ? 0 : null,
    error_count: gpuErrorCount(deviceId, ts, 2),
    throttle_reasons: gpuTemperature(deviceId, ts) > 76 ? ['sw_thermal'] : [],
    process_count: INSTANCES.filter((i) => i.device_ids.includes(deviceId) && i.state === 'ready').length,
    extra: null,
  };
}

// ─── Engine profiles ──────────────────────────────────────────────────────────

interface EngineProfile {
  slots: number;
  rpmBase: number; // requests per minute at peak
  ttftP50: number; // ms
  ttftSpread: number;
  e2eP50: number;
  tpsBase: number; // generation tokens/s per active session
  promptTpsBase: number;
  kvTotalTokens: number;
  kvBase: number; // 0–1 usage
  cachedRatio: number; // share of prompt tokens served from cache
  errorRate: number; // pct
  active: boolean;
}

const ENGINE_PROFILES: Record<string, EngineProfile> = {
  [IDS.instances.qwenL40s]: {
    slots: 8,
    rpmBase: 92,
    ttftP50: 118,
    ttftSpread: 160,
    e2eP50: 1450,
    tpsBase: 148,
    promptTpsBase: 3100,
    kvTotalTokens: 8 * 8192,
    kvBase: 0.46,
    cachedRatio: 0.58,
    errorRate: 0.3,
    active: true,
  },
  [IDS.instances.qwenMac]: {
    slots: 4,
    rpmBase: 34,
    ttftP50: 210,
    ttftSpread: 260,
    e2eP50: 2600,
    tpsBase: 62,
    promptTpsBase: 820,
    kvTotalTokens: 4 * 8192,
    kvBase: 0.31,
    cachedRatio: 0.44,
    errorRate: 0.1,
    active: true,
  },
  [IDS.instances.gemmaMac]: {
    slots: 1,
    rpmBase: 11,
    ttftP50: 340,
    ttftSpread: 300,
    e2eP50: 3900,
    tpsBase: 48,
    promptTpsBase: 560,
    kvTotalTokens: 8192,
    kvBase: 0.22,
    cachedRatio: 0,
    errorRate: 0,
    active: true,
  },
  [IDS.instances.extVllm]: {
    slots: 32,
    rpmBase: 22,
    ttftP50: 160,
    ttftSpread: 220,
    e2eP50: 4200,
    tpsBase: 41,
    promptTpsBase: 2400,
    kvTotalTokens: 32 * 8192,
    kvBase: 0.18,
    cachedRatio: 0.35,
    errorRate: 0.2,
    active: true,
  },
  [IDS.instances.nomicL40s]: {
    slots: 16,
    rpmBase: 410,
    ttftP50: 14,
    ttftSpread: 10,
    e2eP50: 19,
    tpsBase: 0,
    promptTpsBase: 9800,
    kvTotalTokens: 16 * 2048,
    kvBase: 0.08,
    cachedRatio: 0,
    errorRate: 0.05,
    active: true,
  },
};

function load(instanceId: string, ts: number): number {
  // 0–1 traffic intensity for an instance.
  const base = 0.35 + 0.65 * diurnal(ts);
  const wobble = noise(`load:${instanceId}`, ts, 420) * 0.3;
  const burst = Math.max(0, noise(`burst:${instanceId}`, ts, 53, 2)) * 0.45;
  return clamp(base + wobble + burst, 0.02, 1);
}

export function engineActiveSessions(instanceId: string, ts: number): number {
  const p = ENGINE_PROFILES[instanceId];
  if (!p || !p.active) return 0;
  return Math.round(clamp(load(instanceId, ts) * p.slots * 0.9 + noise(`as:${instanceId}`, ts, 45, 1) * 1.2, 0, p.slots));
}

export function engineWaitingSessions(instanceId: string, ts: number): number {
  const p = ENGINE_PROFILES[instanceId];
  if (!p || !p.active) return 0;
  const l = load(instanceId, ts);
  const over = Math.max(0, l - 0.8) * p.slots * 1.6;
  return Math.round(clamp(over + Math.max(0, noise(`ws:${instanceId}`, ts, 90, 1) - 0.35) * 2, 0, p.slots * 2));
}

export function engineRequestsPerMinute(instanceId: string, ts: number): number {
  const p = ENGINE_PROFILES[instanceId];
  if (!p || !p.active) return 0;
  return round(clamp(p.rpmBase * load(instanceId, ts) * (1 + noise(`rpm:${instanceId}`, ts, 90, 2) * 0.18), 0, p.rpmBase * 1.4), 1);
}

export function engineTtft(instanceId: string, ts: number, q: 'avg' | 'p50' | 'p95'): number {
  const p = ENGINE_PROFILES[instanceId];
  if (!p || !p.active) return 0;
  const l = load(instanceId, ts);
  const queue = Math.max(0, l - 0.7) * 900;
  const p50 = p.ttftP50 * (0.85 + l * 0.5) + noise(`ttft:${instanceId}`, ts, 60, 2) * p.ttftP50 * 0.25 + queue * 0.4;
  if (q === 'p50') return round(Math.max(4, p50), 0);
  if (q === 'avg') return round(Math.max(4, p50 * 1.12), 0);
  return round(Math.max(6, p50 * 1.9 + p.ttftSpread * (0.6 + l) + queue), 0);
}

export function engineE2e(instanceId: string, ts: number, q: 'avg' | 'p50' | 'p95'): number {
  const p = ENGINE_PROFILES[instanceId];
  if (!p || !p.active) return 0;
  const l = load(instanceId, ts);
  const p50 = p.e2eP50 * (0.8 + l * 0.6) + noise(`e2e:${instanceId}`, ts, 75, 2) * p.e2eP50 * 0.2;
  if (q === 'p50') return round(Math.max(5, p50), 0);
  if (q === 'avg') return round(Math.max(5, p50 * 1.15), 0);
  return round(Math.max(8, p50 * 2.1 + engineTtft(instanceId, ts, 'p95')), 0);
}

export function engineGenerationTps(instanceId: string, ts: number): number {
  const p = ENGINE_PROFILES[instanceId];
  if (!p || !p.active || p.tpsBase === 0) return 0;
  const active = engineActiveSessions(instanceId, ts);
  if (active === 0) return 0;
  // Per-session throughput drops as the batch grows; aggregate still rises.
  const perSession = p.tpsBase / Math.pow(active, 0.35);
  return round(perSession * active * (1 + noise(`tps:${instanceId}`, ts, 30, 2) * 0.12), 1);
}

export function enginePromptTps(instanceId: string, ts: number): number {
  const p = ENGINE_PROFILES[instanceId];
  if (!p || !p.active) return 0;
  return round(p.promptTpsBase * load(instanceId, ts) * (1 + noise(`ptps:${instanceId}`, ts, 45, 2) * 0.2), 1);
}

export function engineKvUsagePct(instanceId: string, ts: number): number {
  const p = ENGINE_PROFILES[instanceId];
  if (!p || !p.active) return 0;
  const active = engineActiveSessions(instanceId, ts);
  const pct = p.kvBase * 100 + (active / p.slots) * 42 + noise(`kv:${instanceId}`, ts, 120, 2) * 7;
  return round(clamp(pct, 0, 98), 1);
}

export function engineKvUsedTokens(instanceId: string, ts: number): number {
  const p = ENGINE_PROFILES[instanceId];
  if (!p) return 0;
  return Math.round((engineKvUsagePct(instanceId, ts) / 100) * p.kvTotalTokens);
}

export function engineKvTotalTokens(instanceId: string): number {
  return ENGINE_PROFILES[instanceId]?.kvTotalTokens ?? 0;
}

/** Prompt tokens processed in a bucket, split into cached vs computed. */
export function enginePromptTokensDelta(instanceId: string, ts: number, bucketSeconds: number): { cached: number; computed: number } {
  const p = ENGINE_PROFILES[instanceId];
  if (!p || !p.active) return { cached: 0, computed: 0 };
  const requests = (engineRequestsPerMinute(instanceId, ts) / 60) * bucketSeconds;
  const promptPerRequest = p.tpsBase === 0 ? 180 : 640;
  const total = requests * promptPerRequest * (1 + noise(`ptok:${instanceId}`, ts, 200, 2) * 0.3);
  const cachedShare = clamp(p.cachedRatio + noise(`cr:${instanceId}`, ts, 600, 2) * 0.12, 0, 0.95);
  const cached = Math.round(total * cachedShare);
  return { cached, computed: Math.round(total - cached) };
}

export function engineGeneratedTokensDelta(instanceId: string, ts: number, bucketSeconds: number): number {
  return Math.round(engineGenerationTps(instanceId, ts) * bucketSeconds);
}

export function engineErrorsDelta(instanceId: string, ts: number, bucketSeconds: number): number {
  const p = ENGINE_PROFILES[instanceId];
  if (!p || !p.active) return 0;
  const requests = (engineRequestsPerMinute(instanceId, ts) / 60) * bucketSeconds;
  const burst = Math.max(0, noise(`errb:${instanceId}`, ts, 400, 2) - 0.72) * 40;
  return Math.round(requests * (p.errorRate / 100) + burst);
}

export function engineErrorRatePct(instanceId: string, ts: number, bucketSeconds: number): number {
  const requests = (engineRequestsPerMinute(instanceId, ts) / 60) * bucketSeconds;
  if (requests <= 0) return 0;
  return round(clamp((engineErrorsDelta(instanceId, ts, bucketSeconds) / requests) * 100, 0, 100), 2);
}

export function engineTpot(instanceId: string, ts: number): number | null {
  const p = ENGINE_PROFILES[instanceId];
  if (!p || p.tpsBase === 0) return null;
  const tps = engineGenerationTps(instanceId, ts) / Math.max(1, engineActiveSessions(instanceId, ts));
  return tps > 0 ? round(1000 / tps, 1) : null;
}

export function enginePrefixCacheHitRate(instanceId: string, ts: number): number | null {
  const p = ENGINE_PROFILES[instanceId];
  if (!p || p.cachedRatio === 0) return null;
  return round(clamp(p.cachedRatio + noise(`cr:${instanceId}`, ts, 600, 2) * 0.12, 0, 0.95), 3);
}

export function engineSample(instanceId: string, ts: number): Record<string, unknown> {
  const inst = INSTANCES.find((i) => i.id === instanceId);
  const p = ENGINE_PROFILES[instanceId];
  const active = engineActiveSessions(instanceId, ts);
  const sinceStart = Math.max(0, (ts - new Date(inst?.started_at ?? ts).getTime()) / 1000);
  const rpm = engineRequestsPerMinute(instanceId, ts);
  const requestsTotal = Math.round((rpm / 60) * sinceStart * 0.8);
  const errorsTotal = Math.round(requestsTotal * ((p?.errorRate ?? 0) / 100));
  return {
    ts: new Date(ts).toISOString(),
    instance_id: instanceId,
    deployment_id: inst?.deployment_id ?? null,
    active_sessions: active,
    waiting_sessions: engineWaitingSessions(instanceId, ts),
    slots_total: p?.slots ?? null,
    slots_idle: p ? Math.max(0, p.slots - active) : null,
    requests_total: requestsTotal,
    requests_success_total: requestsTotal - errorsTotal,
    errors_total: errorsTotal,
    prompt_tokens_total: Math.round(requestsTotal * 620),
    generated_tokens_total: Math.round(requestsTotal * 310),
    cached_tokens_total: Math.round(requestsTotal * 620 * (p?.cachedRatio ?? 0)),
    prompt_tps: enginePromptTps(instanceId, ts),
    generation_tps: engineGenerationTps(instanceId, ts),
    ttft_ms_avg: engineTtft(instanceId, ts, 'avg'),
    ttft_ms_p50: engineTtft(instanceId, ts, 'p50'),
    ttft_ms_p95: engineTtft(instanceId, ts, 'p95'),
    e2e_ms_avg: engineE2e(instanceId, ts, 'avg'),
    e2e_ms_p50: engineE2e(instanceId, ts, 'p50'),
    e2e_ms_p95: engineE2e(instanceId, ts, 'p95'),
    tpot_ms_avg: engineTpot(instanceId, ts),
    queue_ms_avg: round(Math.max(0, engineWaitingSessions(instanceId, ts)) * 180, 0),
    kv_cache_used_tokens: engineKvUsedTokens(instanceId, ts),
    kv_cache_total_tokens: engineKvTotalTokens(instanceId),
    kv_cache_usage_pct: engineKvUsagePct(instanceId, ts),
    kv_cache_bytes: Math.round(engineKvUsedTokens(instanceId, ts) * 28672),
    prefix_cache_hit_rate: enginePrefixCacheHitRate(instanceId, ts),
    spec_accept_rate: null,
    process_vram_bytes: inst ? Math.round((inst.resources?.vram_bytes as number | undefined) ?? 2 * GiB) : null,
    process_rss_bytes: Math.round(1.2 * GiB),
    health: inst?.health ?? 'unknown',
    extra: null,
  };
}

// ─── Series builders ──────────────────────────────────────────────────────────

export type SeriesPoint = [string, number | null];

export function bucketsFor(range: RangeQuery): number[] {
  const out: number[] = [];
  const stepMs = range.stepSeconds * 1000;
  const start = Math.floor(range.from.getTime() / stepMs) * stepMs;
  for (let t = start; t < range.to.getTime(); t += stepMs) out.push(t);
  return out;
}

export const GPU_METRIC_UNITS: Record<string, string> = {
  gpu_utilization_pct: '%',
  gpu_memory_util_pct: '%',
  gpu_memory_used_bytes: 'bytes',
  gpu_memory_total_bytes: 'bytes',
  gpu_power_watts: 'W',
  gpu_temperature_c: '°C',
  gpu_sm_clock_mhz: 'MHz',
  gpu_memory_clock_mhz: 'MHz',
  gpu_fan_pct: '%',
  gpu_error_count: 'count',
  gpu_energy_wh: 'Wh',
};

export const DEFAULT_GPU_METRICS = ['gpu_utilization_pct', 'gpu_memory_used_bytes', 'gpu_power_watts', 'gpu_temperature_c'];

export function gpuMetricValue(metric: string, deviceId: string, ts: number, bucketSeconds: number): number | null {
  switch (metric) {
    case 'gpu_utilization_pct':
      return gpuUtilization(deviceId, ts);
    case 'gpu_memory_util_pct':
      return DEVICE_PROFILES[deviceId]?.smClock[1] ? round(gpuUtilization(deviceId, ts) * 0.72, 1) : null;
    case 'gpu_memory_used_bytes':
      return gpuMemoryUsed(deviceId, ts);
    case 'gpu_memory_total_bytes':
      return gpuMemoryTotal(deviceId);
    case 'gpu_power_watts':
      return gpuPower(deviceId, ts);
    case 'gpu_temperature_c':
      return gpuTemperature(deviceId, ts);
    case 'gpu_sm_clock_mhz':
      return gpuSmClock(deviceId, ts);
    case 'gpu_memory_clock_mhz':
      return DEVICE_PROFILES[deviceId]?.smClock[1] ? DEVICE_PROFILES[deviceId].memClock : null;
    case 'gpu_fan_pct':
      return gpuFan(deviceId, ts);
    case 'gpu_error_count':
      return gpuErrorCount(deviceId, ts, bucketSeconds);
    case 'gpu_energy_wh':
      return round((gpuPower(deviceId, ts) * bucketSeconds) / 3600, 3);
    default:
      return null;
  }
}

export const ENGINE_METRIC_UNITS: Record<string, string> = {
  active_sessions: 'count',
  waiting_sessions: 'count',
  slots_total: 'count',
  requests_delta: 'count',
  requests_per_minute: 'req/min',
  errors_delta: 'count',
  error_rate_pct: '%',
  prompt_tps: 'tok/s',
  generation_tps: 'tok/s',
  ttft_ms_avg: 'ms',
  ttft_ms_p50: 'ms',
  ttft_ms_p95: 'ms',
  e2e_ms_avg: 'ms',
  e2e_ms_p50: 'ms',
  e2e_ms_p95: 'ms',
  tpot_ms_avg: 'ms',
  queue_ms_avg: 'ms',
  kv_cache_usage_pct: '%',
  kv_cache_used_tokens: 'tokens',
  kv_cache_total_tokens: 'tokens',
  cached_tokens_delta: 'tokens',
  computed_tokens: 'tokens',
  prompt_tokens_delta: 'tokens',
  generated_tokens_delta: 'tokens',
  prefix_cache_hit_rate: 'ratio',
};

export const DEFAULT_ENGINE_METRICS = [
  'active_sessions',
  'waiting_sessions',
  'requests_delta',
  'generation_tps',
  'ttft_ms_p50',
  'ttft_ms_p95',
  'e2e_ms_p50',
  'e2e_ms_p95',
  'kv_cache_usage_pct',
  'cached_tokens_delta',
  'computed_tokens',
  'errors_delta',
  'error_rate_pct',
];

export function engineMetricValue(metric: string, instanceId: string, ts: number, bucketSeconds: number): number | null {
  switch (metric) {
    case 'active_sessions':
      return engineActiveSessions(instanceId, ts);
    case 'waiting_sessions':
      return engineWaitingSessions(instanceId, ts);
    case 'slots_total':
      return ENGINE_PROFILES[instanceId]?.slots ?? null;
    case 'requests_delta':
      return Math.round((engineRequestsPerMinute(instanceId, ts) / 60) * bucketSeconds);
    case 'requests_per_minute':
      return engineRequestsPerMinute(instanceId, ts);
    case 'errors_delta':
      return engineErrorsDelta(instanceId, ts, bucketSeconds);
    case 'error_rate_pct':
      return engineErrorRatePct(instanceId, ts, bucketSeconds);
    case 'prompt_tps':
      return enginePromptTps(instanceId, ts);
    case 'generation_tps':
      return engineGenerationTps(instanceId, ts);
    case 'ttft_ms_avg':
      return engineTtft(instanceId, ts, 'avg');
    case 'ttft_ms_p50':
      return engineTtft(instanceId, ts, 'p50');
    case 'ttft_ms_p95':
      return engineTtft(instanceId, ts, 'p95');
    case 'e2e_ms_avg':
      return engineE2e(instanceId, ts, 'avg');
    case 'e2e_ms_p50':
      return engineE2e(instanceId, ts, 'p50');
    case 'e2e_ms_p95':
      return engineE2e(instanceId, ts, 'p95');
    case 'tpot_ms_avg':
      return engineTpot(instanceId, ts);
    case 'queue_ms_avg':
      return round(engineWaitingSessions(instanceId, ts) * 180, 0);
    case 'kv_cache_usage_pct':
      return engineKvUsagePct(instanceId, ts);
    case 'kv_cache_used_tokens':
      return engineKvUsedTokens(instanceId, ts);
    case 'kv_cache_total_tokens':
      return engineKvTotalTokens(instanceId);
    case 'cached_tokens_delta':
      return enginePromptTokensDelta(instanceId, ts, bucketSeconds).cached;
    case 'computed_tokens':
      return enginePromptTokensDelta(instanceId, ts, bucketSeconds).computed;
    case 'prompt_tokens_delta': {
      const d = enginePromptTokensDelta(instanceId, ts, bucketSeconds);
      return d.cached + d.computed;
    }
    case 'generated_tokens_delta':
      return engineGeneratedTokensDelta(instanceId, ts, bucketSeconds);
    case 'prefix_cache_hit_rate':
      return enginePrefixCacheHitRate(instanceId, ts);
    default:
      return null;
  }
}

/** Metrics that aggregate by averaging rather than summing across instances. */
export const AVERAGED_ENGINE_METRICS = new Set([
  'error_rate_pct',
  'ttft_ms_avg',
  'ttft_ms_p50',
  'ttft_ms_p95',
  'e2e_ms_avg',
  'e2e_ms_p50',
  'e2e_ms_p95',
  'tpot_ms_avg',
  'queue_ms_avg',
  'kv_cache_usage_pct',
  'prefix_cache_hit_rate',
]);

export const AVERAGED_GPU_METRICS = new Set([
  'gpu_utilization_pct',
  'gpu_memory_util_pct',
  'gpu_temperature_c',
  'gpu_sm_clock_mhz',
  'gpu_memory_clock_mhz',
  'gpu_fan_pct',
]);

/** Metrics that are not averaged inside a bucket (constants, per-bucket counts, costly per-slot walks). */
const UNSMOOTHED = new Set(['gpu_error_count', 'gpu_memory_total_bytes', 'gpu_memory_clock_mhz', 'kv_cache_total_tokens', 'kv_cache_tokens', 'slots_total']);

/**
 * Value for one bucket. Buckets of a minute and more average several samples
 * spread across the bucket, the way the backend's rollups average raw
 * samples, so long ranges read as trends rather than point noise.
 */
function bucketValue(metric: string, t: number, stepSeconds: number, valueAt: (ts: number) => number | null): number | null {
  const samples = UNSMOOTHED.has(metric) ? 1 : Math.max(1, Math.min(8, Math.round(stepSeconds / 15)));
  if (samples === 1) return valueAt(t);
  let sum = 0;
  let n = 0;
  for (let i = 0; i < samples; i += 1) {
    const v = valueAt(t + ((i + 0.5) / samples) * stepSeconds * 1000);
    if (v === null) continue;
    sum += v;
    n += 1;
  }
  return n === 0 ? null : round(sum / n, 2);
}

export function buildSeries(
  metric: string,
  unit: string,
  labels: Record<string, string>,
  range: RangeQuery,
  valueAt: (ts: number) => number | null,
): Schemas['TimeSeries'] {
  return {
    metric,
    unit,
    labels,
    points: bucketsFor(range).map((t) => [new Date(t).toISOString(), bucketValue(metric, t, range.stepSeconds, valueAt)] as SeriesPoint),
  };
}

/** Aggregate several entity series into one (sum / avg / max). */
export function aggregateSeries(
  metric: string,
  unit: string,
  labels: Record<string, string>,
  range: RangeQuery,
  entityIds: string[],
  valueFor: (entityId: string, ts: number) => number | null,
  mode: 'sum' | 'avg' | 'max',
): Schemas['TimeSeries'] {
  return buildSeries(metric, unit, labels, range, (ts) => {
    const values = entityIds.map((id) => valueFor(id, ts)).filter((v): v is number => v !== null);
    if (values.length === 0) return null;
    if (mode === 'sum') return round(values.reduce((a, b) => a + b, 0), 2);
    if (mode === 'max') return Math.max(...values);
    return round(values.reduce((a, b) => a + b, 0) / values.length, 2);
  });
}

// ─── Fleet-wide live totals (dashboard + SSE overview frame) ─────────────────

export function overviewLive(ts: number): Schemas['OverviewLive'] {
  const devices = DEVICES.map((d) => d.id);
  const instances = INSTANCES.filter((i) => i.state === 'ready').map((i) => i.id);
  const utils = devices.map((d) => gpuUtilization(d, ts));
  const vramUsed = devices.reduce((sum, d) => sum + gpuMemoryUsed(d, ts), 0);
  const vramTotal = devices.reduce((sum, d) => sum + gpuMemoryTotal(d), 0);
  const requests = instances.reduce((sum, i) => sum + engineRequestsPerMinute(i, ts), 0);
  const errors = instances.reduce((sum, i) => sum + engineErrorsDelta(i, ts, 60), 0);
  const ttftWeighted = instances.reduce((sum, i) => sum + engineTtft(i, ts, 'p95') * engineRequestsPerMinute(i, ts), 0);
  return {
    ts: new Date(ts).toISOString(),
    gpu_utilization_pct_avg: round(utils.reduce((a, b) => a + b, 0) / Math.max(1, utils.length), 1),
    vram_used_bytes: vramUsed,
    vram_total_bytes: vramTotal,
    power_watts: round(devices.reduce((sum, d) => sum + gpuPower(d, ts), 0), 1),
    active_sessions: instances.reduce((sum, i) => sum + engineActiveSessions(i, ts), 0),
    waiting_sessions: instances.reduce((sum, i) => sum + engineWaitingSessions(i, ts), 0),
    requests_per_minute: round(requests, 1),
    generation_tps: round(instances.reduce((sum, i) => sum + engineGenerationTps(i, ts), 0), 1),
    ttft_ms_p95: requests > 0 ? round(ttftWeighted / requests, 0) : null,
    error_rate_pct: requests > 0 ? round((errors / requests) * 100, 2) : 0,
  };
}

// ─── Usage (cost) series ──────────────────────────────────────────────────────

export const USAGE_METRIC_UNITS: Record<string, string> = {
  requests: 'count',
  input_tokens: 'tokens',
  output_tokens: 'tokens',
  cached_tokens: 'tokens',
  cost_usd: 'USD',
  gpu_minutes: 'min',
};

export function usageMetricValue(metric: string, ts: number, bucketSeconds: number, modelKey = 'all'): number {
  const instances = modelKey === 'all' ? INSTANCES : INSTANCES.filter((i) => i.deployment_id === modelKey);
  const ids = instances.map((i) => i.id);
  const requests = ids.reduce((s, i) => s + (engineRequestsPerMinute(i, ts) / 60) * bucketSeconds, 0);
  const prompt = ids.reduce((s, i) => {
    const d = enginePromptTokensDelta(i, ts, bucketSeconds);
    return s + d.cached + d.computed;
  }, 0);
  const cached = ids.reduce((s, i) => s + enginePromptTokensDelta(i, ts, bucketSeconds).cached, 0);
  const generated = ids.reduce((s, i) => s + engineGeneratedTokensDelta(i, ts, bucketSeconds), 0);
  const gpuMinutes = (instances.length * bucketSeconds) / 60;
  switch (metric) {
    case 'requests':
      return Math.round(requests);
    case 'input_tokens':
      return Math.round(prompt);
    case 'output_tokens':
      return Math.round(generated);
    case 'cached_tokens':
      return Math.round(cached);
    case 'gpu_minutes':
      return round(gpuMinutes, 2);
    case 'cost_usd':
      // $0.012 / GPU-minute on the L40S box, $0.004 on the Mac, plus $0.08 / 1M tokens.
      return round(gpuMinutes * 0.0085 + ((prompt + generated) / 1_000_000) * 0.08, 4);
    default:
      return 0;
  }
}

/** Sum a usage metric over a window at hourly resolution (cheap, for cost tiles). */
export function usageTotal(metric: string, from: number, to: number): number {
  let total = 0;
  const step = 3600;
  for (let t = Math.floor(from / 3.6e6) * 3.6e6; t < to; t += step * 1000) {
    const bucket = Math.min(step, (to - t) / 1000);
    total += usageMetricValue(metric, t, bucket);
  }
  return round(total, metric === 'cost_usd' ? 2 : 0);
}
