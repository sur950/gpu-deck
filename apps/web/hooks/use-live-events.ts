/**
 * Live events (`GET /live/events`, SSE) with automatic reconnect.
 *
 * Subscribes to the topics a screen needs and keeps the latest frame per
 * topic. EventSource reconnects on its own for transient drops; when the
 * server closes the stream (deploy, 401 after session expiry) the hook
 * re-opens it with exponential backoff (1 s → 30 s). In mock mode it
 * subscribes to `lib/mocks/live.ts` instead.
 *
 *   const live = useLiveEvents({ topics: ['metrics'], scope: { nodeId } });
 *   live.status            // 'connecting' | 'open' | 'reconnecting' | 'closed'
 *   live.latest.metrics    // last metrics frame ({ gpu, engine, overview })
 */

'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import type { components } from '@/lib/api/generated/admin';
import { isMockMode } from '@/lib/mocks';
import { getGatewayUrl } from '@/utils/gateway-url';

type LiveEvent = components['schemas']['LiveEvent'];
export type LiveTopic = LiveEvent['event'];
export type LiveStatus = 'idle' | 'connecting' | 'open' | 'reconnecting' | 'closed';

export interface LiveScope {
  clusterId?: string;
  nodeId?: string;
  deploymentId?: string;
  instanceId?: string;
}

export interface UseLiveEventsOptions {
  topics: LiveTopic[];
  scope?: LiveScope;
  /** Metrics push interval in seconds (1–60, default 2). */
  intervalSeconds?: number;
  enabled?: boolean;
  /** Called for every frame (e.g. to append log chunks). */
  onEvent?: (event: LiveEvent) => void;
}

export interface LiveEventsState {
  status: LiveStatus;
  /** Latest frame per topic. */
  latest: Partial<Record<LiveTopic, LiveEvent>>;
  /** Frames received since mount — a cheap "is it moving" indicator. */
  frames: number;
  lastEventAt: number | null;
}

const MAX_BACKOFF_MS = 30_000;

function buildUrl(options: UseLiveEventsOptions): string {
  const params = new URLSearchParams();
  for (const topic of options.topics) params.append('topics', topic);
  const scope = options.scope ?? {};
  if (scope.clusterId) params.set('cluster_id', scope.clusterId);
  if (scope.nodeId) params.set('node_id', scope.nodeId);
  if (scope.deploymentId) params.set('deployment_id', scope.deploymentId);
  if (scope.instanceId) params.set('instance_id', scope.instanceId);
  if (options.intervalSeconds) params.set('interval_seconds', String(options.intervalSeconds));
  return `${getGatewayUrl()}/live/events?${params.toString()}`;
}

export function useLiveEvents(options: UseLiveEventsOptions): LiveEventsState {
  const { topics, scope, intervalSeconds, enabled = true } = options;
  const [status, setStatus] = useState<LiveStatus>('connecting');
  const [latest, setLatest] = useState<Partial<Record<LiveTopic, LiveEvent>>>({});
  const [frames, setFrames] = useState(0);
  const [lastEventAt, setLastEventAt] = useState<number | null>(null);
  const onEventRef = useRef(options.onEvent);
  useEffect(() => {
    onEventRef.current = options.onEvent;
  });

  // Stable key so a re-render with the same options doesn't reconnect.
  const key = useMemo(
    () => JSON.stringify({ topics, scope: scope ?? {}, intervalSeconds: intervalSeconds ?? 2 }),
    [topics, scope, intervalSeconds],
  );

  useEffect(() => {
    if (!enabled) return;
    const parsed = JSON.parse(key) as { topics: LiveTopic[]; scope: LiveScope; intervalSeconds: number };
    let cancelled = false;
    let source: EventSource | null = null;
    let unsubscribeMock: (() => void) | null = null;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;
    let attempt = 0;

    const handle = (event: LiveEvent) => {
      if (cancelled) return;
      setLatest((prev) => ({ ...prev, [event.event]: event }));
      setFrames((n) => n + 1);
      setLastEventAt(Date.now());
      onEventRef.current?.(event);
    };

    const connect = () => {
      if (cancelled) return;
      if (attempt > 0) setStatus('reconnecting');

      if (isMockMode()) {
        void import('@/lib/mocks/live').then(({ subscribeMockLive }) => {
          if (cancelled) return;
          unsubscribeMock = subscribeMockLive(
            { topics: parsed.topics, intervalSeconds: parsed.intervalSeconds, scope: parsed.scope },
            handle,
            () => setStatus('open'),
          );
        });
        return;
      }

      source = new EventSource(buildUrl({ topics: parsed.topics, scope: parsed.scope, intervalSeconds: parsed.intervalSeconds }), {
        withCredentials: true,
      });
      source.onopen = () => {
        attempt = 0;
        setStatus('open');
      };
      for (const topic of parsed.topics) {
        source.addEventListener(topic, (e) => {
          try {
            const data = JSON.parse((e as MessageEvent<string>).data) as Record<string, unknown>;
            handle({ event: topic, ts: new Date().toISOString(), data });
          } catch {
            // Malformed frame: ignore, the next one will be fine.
          }
        });
      }
      source.onerror = () => {
        // EventSource retries CONNECTING on its own; only when it gives up
        // (readyState CLOSED) do we schedule our own reconnect with backoff.
        if (source?.readyState === EventSource.CLOSED) {
          source.close();
          source = null;
          attempt += 1;
          setStatus('reconnecting');
          retryTimer = setTimeout(connect, Math.min(1_000 * 2 ** (attempt - 1), MAX_BACKOFF_MS));
        } else {
          setStatus('reconnecting');
        }
      };
    };

    connect();

    return () => {
      cancelled = true;
      if (retryTimer) clearTimeout(retryTimer);
      source?.close();
      unsubscribeMock?.();
      // The next run (new scope / re-enable) starts from "connecting".
      setStatus('connecting');
    };
  }, [key, enabled]);

  return { status: enabled ? status : 'idle', latest, frames, lastEventAt };
}

// ─── Typed views over the metrics frame ───────────────────────────────────────

export interface LiveGpuSample {
  ts: string;
  deviceId: string;
  utilizationPct: number | null;
  memoryUsedBytes: number | null;
  memoryTotalBytes: number | null;
  powerWatts: number | null;
  temperatureC: number | null;
  smClockMhz: number | null;
  fanPct: number | null;
  errorCount: number | null;
  throttleReasons: string[];
}

export interface LiveEngineSample {
  ts: string;
  instanceId: string;
  deploymentId: string | null;
  activeSessions: number | null;
  waitingSessions: number | null;
  slotsTotal: number | null;
  generationTps: number | null;
  promptTps: number | null;
  ttftMsP50: number | null;
  ttftMsP95: number | null;
  e2eMsP50: number | null;
  e2eMsP95: number | null;
  kvCacheUsagePct: number | null;
  kvCacheUsedTokens: number | null;
  kvCacheTotalTokens: number | null;
  requestsTotal: number | null;
  errorsTotal: number | null;
  health: string | null;
}

function num(v: unknown): number | null {
  return typeof v === 'number' && Number.isFinite(v) ? v : null;
}

function str(v: unknown): string | null {
  return typeof v === 'string' ? v : null;
}

export function readGpuSamples(frame: LiveEvent | undefined): LiveGpuSample[] {
  const list = (frame?.data?.gpu as Record<string, unknown>[] | undefined) ?? [];
  return list.map((s) => ({
    ts: str(s.ts) ?? '',
    deviceId: str(s.device_id) ?? '',
    utilizationPct: num(s.utilization_pct),
    memoryUsedBytes: num(s.memory_used_bytes),
    memoryTotalBytes: num(s.memory_total_bytes),
    powerWatts: num(s.power_watts),
    temperatureC: num(s.temperature_c),
    smClockMhz: num(s.sm_clock_mhz),
    fanPct: num(s.fan_pct),
    errorCount: num(s.error_count),
    throttleReasons: Array.isArray(s.throttle_reasons) ? (s.throttle_reasons as string[]) : [],
  }));
}

export function readEngineSamples(frame: LiveEvent | undefined): LiveEngineSample[] {
  const list = (frame?.data?.engine as Record<string, unknown>[] | undefined) ?? [];
  return list.map((s) => ({
    ts: str(s.ts) ?? '',
    instanceId: str(s.instance_id) ?? '',
    deploymentId: str(s.deployment_id),
    activeSessions: num(s.active_sessions),
    waitingSessions: num(s.waiting_sessions),
    slotsTotal: num(s.slots_total),
    generationTps: num(s.generation_tps),
    promptTps: num(s.prompt_tps),
    ttftMsP50: num(s.ttft_ms_p50),
    ttftMsP95: num(s.ttft_ms_p95),
    e2eMsP50: num(s.e2e_ms_p50),
    e2eMsP95: num(s.e2e_ms_p95),
    kvCacheUsagePct: num(s.kv_cache_usage_pct),
    kvCacheUsedTokens: num(s.kv_cache_used_tokens),
    kvCacheTotalTokens: num(s.kv_cache_total_tokens),
    requestsTotal: num(s.requests_total),
    errorsTotal: num(s.errors_total),
    health: str(s.health),
  }));
}

export function readOverviewLive(frame: LiveEvent | undefined): components['schemas']['OverviewLive'] | null {
  const o = frame?.data?.overview as components['schemas']['OverviewLive'] | undefined;
  return o && typeof o.ts === 'string' ? o : null;
}
