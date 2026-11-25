/**
 * UI types for network latency: round-trip times per node (agent hub and every API layer),
 * their history, and where serving request time went. Backend shapes are mapped in
 * lib/api/latency.ts.
 */

import type { MetricSeries, MetricStep } from '@/types/metrics';

/** Round-trip statistics of one path, in milliseconds. */
export interface RttStats {
  rttMs: number | null;
  jitterMs: number | null;
  lastMs: number | null;
  minMs: number | null;
  maxMs: number | null;
  p50Ms: number | null;
  p95Ms: number | null;
  samples: number;
  failures: number;
  updatedAt: string | null;
}

export type ServingPath = 'tunnel' | 'direct';

export interface ApiLayerRtt extends RttStats {
  apiLayerId: string;
  region: string | null;
  mode: ServingPath | null;
}

export interface NodeRtt {
  nodeId: string;
  nodeName: string | null;
  status: string | null;
  /** Agent hub -> agent (WebSocket ping / pong). */
  hub: RttStats | null;
  /** Each API layer -> node over the serving path, nearest first. */
  apiLayers: ApiLayerRtt[];
  bestRttMs: number | null;
}

export interface ApiLayerInfo {
  id: string;
  region: string | null;
  seenAt: string | null;
  probeSeconds: number | null;
  nodes: number | null;
}

export interface LatencyLive {
  generatedAt: string;
  apiLayers: ApiLayerInfo[];
  nodes: NodeRtt[];
}

export interface LatencySummary {
  nodeId: string;
  nodeName: string | null;
  source: 'hub' | 'api_layer';
  apiLayerId: string | null;
  region: string | null;
  mode: string | null;
  points: number;
  p50Ms: number | null;
  p95Ms: number | null;
  minMs: number | null;
  maxMs: number | null;
  avgMs: number | null;
  jitterMs: number | null;
  lastAt: string | null;
}

export interface LatencyHistory {
  from: string;
  to: string;
  step: string;
  series: MetricSeries[];
  summaries: LatencySummary[];
}

export interface LatencyHistoryParams {
  from: string;
  to: string;
  step: MetricStep;
  nodeId?: string;
}

export interface RequestTimingParams {
  from: string;
  to: string;
  step: MetricStep;
  deploymentId?: string;
  nodeId?: string;
  region?: string;
  stream?: boolean;
}

export interface RequestTiming {
  from: string;
  to: string;
  step: string;
  /** One series per timing field (`route_ms`, ..., `part_*_ms`) plus `requests`. */
  series: MetricSeries[];
  summary: Record<string, number | null>;
  regions: Record<string, number | string | null>[];
}

/** Parts of the time to first byte, in stacking order (they add up per request). */
export const TIMING_PARTS = [
  { key: 'part_api_ms', label: 'API layer', hint: 'routing, waiting for a replica, opening a connection' },
  { key: 'part_network_ms', label: 'Network', hint: 'API layer <-> node, measured per request' },
  { key: 'part_node_ms', label: 'Node proxy', hint: 'node time outside the engine (queue for a slot, body)' },
  { key: 'part_unsplit_ms', label: 'Node + network', hint: 'agents that report no timing: not split' },
  { key: 'part_engine_ms', label: 'Engine', hint: 'engine to its response headers' },
  { key: 'part_first_token_ms', label: 'First token', hint: 'response headers to the first byte (first token when streaming)' },
] as const;

export type TimingPartKey = (typeof TIMING_PARTS)[number]['key'];
