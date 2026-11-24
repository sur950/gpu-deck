/**
 * UI types for time-series metrics (GPU, inference, sessions, usage).
 * Backend shapes are mapped in lib/api/metrics.ts.
 */

import type { components } from '@/lib/api/generated/admin';

export type MetricStep = '2s' | '15s' | '1m' | '5m' | '15m' | '1h' | '1d';

export interface TimeRange {
  /** ISO datetime */
  from: string;
  /** ISO datetime */
  to: string;
  step: MetricStep;
}

/** A point in a series: epoch milliseconds + value (null = no sample). */
export interface SeriesPoint {
  t: number;
  v: number | null;
}

export interface MetricSeries {
  metric: string;
  unit: string;
  labels: Record<string, string>;
  points: SeriesPoint[];
}

export interface MetricsResult {
  from: string;
  to: string;
  step: string;
  series: MetricSeries[];
}

export type GpuAggregate = 'none' | 'sum' | 'avg' | 'max';

export interface GpuMetricsParams extends TimeRange {
  metrics?: string[];
  clusterId?: string;
  nodeId?: string;
  deviceIds?: string[];
  aggregate?: GpuAggregate;
}

export interface InferenceMetricsParams extends TimeRange {
  metrics?: string[];
  modelId?: string;
  deploymentId?: string;
  instanceIds?: string[];
  nodeId?: string;
  aggregate?: GpuAggregate;
}

export interface SessionMetricsParams extends TimeRange {
  instanceId: string;
  sessionKey?: string;
  requestId?: string;
}

export interface UsageMetricsParams {
  from: string;
  to: string;
  step?: '1h' | '1d';
  metrics?: string[];
  apiKeyId?: string;
  modelId?: string;
  nodeId?: string;
  deploymentId?: string;
  groupBy?: 'none' | 'api_key' | 'model' | 'node' | 'deployment';
}

export type SessionState = components['schemas']['SessionSample']['state'];

export interface SessionSlot {
  ts: string;
  instanceId: string;
  slotId: number | null;
  sessionKey: string;
  requestId: string | null;
  state: SessionState;
  promptTokens: number | null;
  generatedTokens: number | null;
  cachedTokens: number | null;
  kvCacheTokens: number | null;
  kvCacheBytes: number | null;
  contextSize: number | null;
  ttftMs: number | null;
  elapsedMs: number | null;
  tokensPerSecond: number | null;
}

export interface SessionsSnapshot {
  ts: string;
  instanceId: string;
  active: number;
  waiting: number;
  slotsTotal: number | null;
  sessions: SessionSlot[];
}

/** Live numbers for an instance or a deployment (aggregated by the backend). */
export interface InstanceLive {
  ts: string | null;
  activeSessions: number | null;
  waitingSessions: number | null;
  kvCacheUsagePct: number | null;
  generationTps: number | null;
  ttftMsP95: number | null;
  requestsPerMinute: number | null;
  errorRatePct: number | null;
}

export type InstanceState = components['schemas']['instance_state'];
export type InstanceHealth = components['schemas']['instance_health'];
export type EngineName = components['schemas']['engine_name'];

export interface InstanceSummary {
  id: string;
  deploymentId: string;
  deploymentName: string;
  modelName: string;
  nodeId: string;
  nodeName: string;
  deviceIds: string[];
  engine: EngineName | null;
  servedModelName: string | null;
  /** Engine port on the node (loopback); the agent may pick a new one when it restarts the engine. */
  port: number | null;
  state: InstanceState;
  stateMessage: string | null;
  health: InstanceHealth;
  trafficWeight: number;
  slots: number | null;
  contextSize: number | null;
  restartCount: number;
  startedAt: string | null;
  lastRequestAt: string | null;
  live: InstanceLive;
  /** Observed server GPU Deck did not start (read-only). */
  external: boolean;
}

export interface DeploymentSummary {
  id: string;
  name: string;
  modelId: string;
  modelName: string;
  modelType: string | null;
  engine: EngineName | null;
  engineVersion: string | null;
  status: string;
  priority: string | null;
  replicas: number;
  readyReplicas: number;
  lastRequestAt: string | null;
  instances: InstanceSummary[];
  live: InstanceLive;
  /** Observed server GPU Deck did not start (read-only). */
  external: boolean;
}

export interface MetricCatalogEntry {
  name: string;
  unit: string;
  type: 'gauge' | 'counter' | 'histogram';
  scope: 'gpu' | 'engine' | 'session' | 'usage' | 'node';
  description: string;
}
