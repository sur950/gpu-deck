/**
 * Network latency API: current round trips per node, their history and the per-request
 * timing breakdown (admin.yaml Metrics tag). snake_case rows are mapped to camelCase here.
 */

import { apiGet, toQuery } from '@/lib/api/client';
import type { components } from '@/lib/api/generated/admin';
import { mapSeries } from '@/lib/api/metrics';
import type {
  ApiLayerRtt,
  LatencyHistory,
  LatencyHistoryParams,
  LatencyLive,
  NodeRtt,
  RequestTiming,
  RequestTimingParams,
  RttStats,
  ServingPath,
} from '@/types/latency';

type Schemas = components['schemas'];

function num(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

export function mapRttStats(row: Schemas['LatencyStats'] | null | undefined): RttStats | null {
  if (!row) return null;
  return {
    rttMs: num(row.rtt_ms),
    jitterMs: num(row.jitter_ms),
    lastMs: num(row.last_ms),
    minMs: num(row.min_ms),
    maxMs: num(row.max_ms),
    p50Ms: num(row.p50_ms),
    p95Ms: num(row.p95_ms),
    samples: row.samples ?? 0,
    failures: row.failures ?? 0,
    updatedAt: row.updated_at ?? null,
  };
}

function mapApiLayer(row: Schemas['ApiLayerLatency']): ApiLayerRtt {
  return {
    ...(mapRttStats(row) as RttStats),
    apiLayerId: row.api_layer_id,
    region: row.region ?? null,
    mode: (row.mode as ServingPath | null | undefined) ?? null,
  };
}

export function mapNodeRtt(row: Schemas['NodeLatency']): NodeRtt {
  return {
    nodeId: row.node_id,
    nodeName: row.node_name ?? null,
    status: row.status ?? null,
    hub: mapRttStats(row.hub ?? null),
    apiLayers: (row.api_layers ?? []).map(mapApiLayer),
    bestRttMs: num(row.best_rtt_ms),
  };
}

export function mapLatencyLive(row: Schemas['LatencyLive']): LatencyLive {
  return {
    generatedAt: row.generated_at,
    apiLayers: row.api_layers.map((l) => ({
      id: l.id,
      region: l.region ?? null,
      seenAt: l.seen_at ?? null,
      probeSeconds: num(l.probe_seconds),
      nodes: num(l.nodes),
    })),
    nodes: row.nodes.map(mapNodeRtt),
  };
}

export async function getLiveLatency(nodeId?: string): Promise<LatencyLive> {
  const qs = toQuery({ node_id: nodeId });
  return mapLatencyLive(await apiGet<Schemas['LatencyLive']>(`/metrics/latency/live${qs}`));
}

export async function getLatencyHistory(params: LatencyHistoryParams): Promise<LatencyHistory> {
  // History is kept per minute: finer steps are widened by the server.
  const qs = toQuery({ from: params.from, to: params.to, step: params.step, node_id: params.nodeId });
  const row = await apiGet<Schemas['LatencyHistory']>(`/metrics/latency${qs}`);
  return {
    from: row.from,
    to: row.to,
    step: row.step,
    series: row.series.map(mapSeries),
    summaries: row.summaries.map((s) => ({
      nodeId: s.node_id,
      nodeName: s.node_name ?? null,
      source: s.source,
      apiLayerId: s.api_layer_id ?? null,
      region: s.region ?? null,
      mode: s.mode ?? null,
      points: s.points ?? 0,
      p50Ms: num(s.p50_ms),
      p95Ms: num(s.p95_ms),
      minMs: num(s.min_ms),
      maxMs: num(s.max_ms),
      avgMs: num(s.avg_ms),
      jitterMs: num(s.jitter_ms),
      lastAt: s.last_at ?? null,
    })),
  };
}

export async function getRequestTiming(params: RequestTimingParams): Promise<RequestTiming> {
  const qs = toQuery({
    from: params.from,
    to: params.to,
    step: params.step,
    deployment_id: params.deploymentId,
    node_id: params.nodeId,
    region: params.region,
    stream: params.stream,
  });
  const row = await apiGet<Schemas['RequestTiming']>(`/metrics/request-timing${qs}`);
  return {
    from: row.from,
    to: row.to,
    step: row.step,
    series: row.series.map(mapSeries),
    summary: row.summary ?? {},
    regions: (row.regions ?? []) as Record<string, number | string | null>[],
  };
}
