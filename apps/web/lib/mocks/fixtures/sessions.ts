/**
 * Per-slot session fixtures (llama.cpp `/slots` style) for an instance.
 *
 * Sessions persist for a few minutes (an "epoch" per slot), then a new
 * request lands in the slot, so the KV heat strip and slot table evolve
 * continuously instead of flickering every refetch.
 */

import { IDS, INSTANCES } from '@/lib/mocks/fixtures/fleet';
import { clamp, hash32, mulberry32, round } from '@/lib/mocks/fixtures/random';
import {
  engineActiveSessions,
  engineWaitingSessions,
  engineKvTotalTokens,
  engineTtft,
  buildSeries,
  type RangeQuery,
} from '@/lib/mocks/fixtures/timeseries';
import type { Schemas } from '@/lib/mocks/types';

const BYTES_PER_TOKEN: Record<string, number> = {
  [IDS.instances.qwenL40s]: 28672,
  [IDS.instances.qwenMac]: 28672,
  [IDS.instances.gemmaMac]: 26624,
  [IDS.instances.nomicL40s]: 3072,
};

function slotsOf(instanceId: string): number {
  const inst = INSTANCES.find((i) => i.id === instanceId);
  return Number(inst?.resources?.slots ?? 1);
}

function contextOf(instanceId: string): number {
  const inst = INSTANCES.find((i) => i.id === instanceId);
  return Number(inst?.resources?.context_size ?? 8192);
}

const EMBEDDING_INSTANCES = new Set<string>([IDS.instances.nomicL40s]);

/** Session length in ms for a slot/epoch: 6 – 70 s for chat, 2 – 8 s for embedding batches. */
function epochLength(instanceId: string, slot: number, epoch: number): number {
  const r = mulberry32(hash32(`epoch:${instanceId}:${slot}:${epoch}`))();
  return EMBEDDING_INSTANCES.has(instanceId) ? 2_000 + r * 6_000 : 6_000 + r * r * 64_000;
}

function epochOf(instanceId: string, slot: number, ts: number): { epoch: number; start: number; length: number } {
  // Variable-length epochs: walk from a fixed recent origin (6 h for chat,
  // 10 min for the short embedding batches), so this stays cheap.
  const span = EMBEDDING_INSTANCES.has(instanceId) ? 600_000 : 21_600_000;
  const origin = Math.floor(ts / span) * span;
  let start = origin + slot * 7_000;
  let epoch = 0;
  let length = epochLength(instanceId, slot, epoch);
  while (start + length < ts) {
    start += length;
    epoch += 1;
    length = epochLength(instanceId, slot, epoch);
  }
  return { epoch, start, length };
}

export function sessionSample(instanceId: string, slot: number, ts: number): Schemas['SessionSample'] {
  const { epoch, start, length } = epochOf(instanceId, slot, ts);
  const rnd = mulberry32(hash32(`session:${instanceId}:${slot}:${epoch}`));
  const active = engineActiveSessions(instanceId, ts);
  const isActive = slot < active;
  const context = contextOf(instanceId);
  const isEmbedding = EMBEDDING_INSTANCES.has(instanceId);
  const taskId = 8000 + ((epoch * 37 + slot * 11) % 9000);
  const elapsed = ts - start;
  const promptTokens = isEmbedding ? Math.round(80 + rnd() * 420) : Math.round(120 + rnd() * 2400);
  const cachedTokens = isEmbedding ? 0 : Math.round(promptTokens * clamp(rnd() * 0.9, 0, 0.9) * (rnd() > 0.35 ? 1 : 0));
  const tps = isEmbedding ? 0 : 22 + rnd() * 70;
  const ttft = engineTtft(instanceId, start, 'p50') * (0.6 + rnd() * 0.9);
  const generated = isEmbedding ? 0 : Math.min(context - promptTokens, Math.round(Math.max(0, elapsed - ttft) / 1000 * tps));
  const progress = clamp(elapsed / length, 0, 1);
  const state: Schemas['SessionSample']['state'] = !isActive
    ? 'idle'
    : elapsed < ttft
      ? 'processing'
      : 'generating';
  const kvTokens = isActive ? Math.min(context, promptTokens + generated) : Math.round((promptTokens + generated) * 0.3 * (1 - progress));
  return {
    ts: new Date(ts).toISOString(),
    instance_id: instanceId,
    slot_id: slot,
    session_key: `slot-${slot}#${taskId}`,
    request_id: isActive ? `${hash32(`req:${instanceId}:${slot}:${epoch}`).toString(16).padStart(8, '0')}-4a1e-4c5b-9d2f-${hash32(`r2:${slot}:${epoch}`).toString(16).padStart(12, '0')}` : null,
    state,
    prompt_tokens: isActive ? promptTokens : null,
    generated_tokens: isActive ? generated : null,
    cached_tokens: isActive ? cachedTokens : null,
    kv_cache_tokens: kvTokens,
    kv_cache_bytes: kvTokens * (BYTES_PER_TOKEN[instanceId] ?? 28672),
    context_size: context,
    ttft_ms: isActive ? round(ttft, 0) : null,
    elapsed_ms: isActive ? round(elapsed, 0) : null,
    tokens_per_second: isActive && state === 'generating' ? round(tps, 1) : null,
    extra: isActive ? { id_task: taskId, n_ctx: context, speculative: false } : null,
  };
}

export function sessionsSnapshot(instanceId: string, ts: number): Schemas['SessionsSnapshot'] {
  const slots = slotsOf(instanceId);
  const sessions = Array.from({ length: slots }, (_, i) => sessionSample(instanceId, i, ts));
  return {
    ts: new Date(ts).toISOString(),
    instance_id: instanceId,
    active: engineActiveSessions(instanceId, ts),
    waiting: engineWaitingSessions(instanceId, ts),
    slots_total: slots,
    sessions,
  };
}

/** `/metrics/sessions`: one series per slot of KV tokens (or filtered to one session). */
export function sessionSeries(instanceId: string, range: RangeQuery, sessionKey: string | null): Schemas['TimeSeries'][] {
  const slots = slotsOf(instanceId);
  const total = engineKvTotalTokens(instanceId);
  const out: Schemas['TimeSeries'][] = [];
  for (let slot = 0; slot < slots; slot += 1) {
    const key = `slot-${slot}`;
    if (sessionKey && !sessionKey.startsWith(key)) continue;
    out.push(
      buildSeries('kv_cache_tokens', 'tokens', { instance_id: instanceId, slot_id: String(slot), session_key: key }, range, (t) => sessionSample(instanceId, slot, t).kv_cache_tokens ?? null),
    );
  }
  out.push(buildSeries('kv_cache_total_tokens', 'tokens', { instance_id: instanceId }, range, () => total));
  return out;
}
