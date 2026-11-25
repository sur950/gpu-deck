/**
 * Metrics API: time-series queries (GPU, inference, sessions, usage), live
 * sessions, and read-only instance / deployment lists used by the
 * observability screens. snake_case backend rows are mapped to camelCase
 * UI types here and nowhere else.
 */

import { apiGet, toQuery } from '@/lib/api/client';
import type { components } from '@/lib/api/generated/admin';
import type {
  DeploymentSummary,
  GpuMetricsParams,
  InferenceMetricsParams,
  InstanceLive,
  InstanceSummary,
  MetricCatalogEntry,
  MetricSeries,
  MetricsResult,
  SessionMetricsParams,
  SessionSlot,
  SessionsSnapshot,
  UsageMetricsParams,
} from '@/types/metrics';

type Schemas = components['schemas'];

// ─── Mapping ──────────────────────────────────────────────────────────────────

export function mapSeries(row: Schemas['TimeSeries']): MetricSeries {
  return {
    metric: row.metric,
    unit: row.unit,
    labels: row.labels ?? {},
    points: row.points.map(([ts, v]) => ({ t: new Date(ts).getTime(), v })),
  };
}

function mapResult(row: Schemas['TimeSeriesResponse']): MetricsResult {
  return { from: row.from, to: row.to, step: row.step, series: row.series.map(mapSeries) };
}

export function mapInstanceLive(row: Schemas['InstanceLive'] | undefined): InstanceLive {
  return {
    ts: row?.ts ?? null,
    activeSessions: row?.active_sessions ?? null,
    waitingSessions: row?.waiting_sessions ?? null,
    kvCacheUsagePct: row?.kv_cache_usage_pct ?? null,
    generationTps: row?.generation_tps ?? null,
    ttftMsP95: row?.ttft_ms_p95 ?? null,
    requestsPerMinute: row?.requests_per_minute ?? null,
    errorRatePct: row?.error_rate_pct ?? null,
  };
}

export function mapInstance(row: Schemas['Instance']): InstanceSummary {
  const resources = (row.resources ?? {}) as Record<string, unknown>;
  return {
    id: row.id,
    deploymentId: row.deployment_id,
    deploymentName: row.deployment_name ?? '',
    modelName: row.model_name ?? row.deployment_name ?? '',
    nodeId: row.node_id,
    nodeName: row.node_name ?? '',
    deviceIds: row.device_ids,
    engine: row.engine_name ?? null,
    servedModelName: row.served_model_name ?? null,
    port: row.port ?? null,
    state: row.state,
    stateMessage: row.state_message ?? null,
    health: row.health,
    trafficWeight: row.traffic_weight,
    slots: typeof resources.slots === 'number' ? resources.slots : null,
    contextSize: typeof resources.context_size === 'number' ? resources.context_size : null,
    restartCount: row.restart_count ?? 0,
    startedAt: row.started_at ?? null,
    lastRequestAt: row.last_request_at ?? null,
    live: mapInstanceLive(row.live),
    external: row.origin === 'external',
  };
}

export function mapDeployment(row: Schemas['Deployment']): DeploymentSummary {
  return {
    id: row.id,
    name: row.name,
    modelId: row.model_id ?? '',
    modelName: row.model_name ?? row.name,
    modelType: row.model_type ?? null,
    engine: row.engine_name ?? null,
    engineVersion: row.engine_version ?? null,
    status: row.status,
    priority: row.effective_priority ?? row.priority_override ?? null,
    replicas: row.replicas,
    readyReplicas: row.ready_replicas ?? 0,
    lastRequestAt: row.last_request_at ?? null,
    instances: (row.instances ?? []).map(mapInstance),
    live: mapInstanceLive(row.live),
    external: row.origin === 'external',
  };
}

function mapSlot(row: Schemas['SessionSample']): SessionSlot {
  return {
    ts: row.ts,
    instanceId: row.instance_id,
    slotId: row.slot_id ?? null,
    sessionKey: row.session_key,
    requestId: row.request_id ?? null,
    state: row.state,
    promptTokens: row.prompt_tokens ?? null,
    generatedTokens: row.generated_tokens ?? null,
    cachedTokens: row.cached_tokens ?? null,
    kvCacheTokens: row.kv_cache_tokens ?? null,
    kvCacheBytes: row.kv_cache_bytes ?? null,
    contextSize: row.context_size ?? null,
    ttftMs: row.ttft_ms ?? null,
    elapsedMs: row.elapsed_ms ?? null,
    tokensPerSecond: row.tokens_per_second ?? null,
  };
}

export function mapSessionsSnapshot(row: Schemas['SessionsSnapshot']): SessionsSnapshot {
  return {
    ts: row.ts,
    instanceId: row.instance_id,
    active: row.active,
    waiting: row.waiting,
    slotsTotal: row.slots_total ?? null,
    sessions: row.sessions.map(mapSlot),
  };
}

// ─── Queries ──────────────────────────────────────────────────────────────────

/** Repeatable params are sent comma-joined; the contract accepts both forms. */
function list(values: string[] | undefined): string | undefined {
  return values && values.length > 0 ? values.join(',') : undefined;
}

export async function getGpuMetrics(params: GpuMetricsParams): Promise<MetricsResult> {
  const qs = toQuery({
    from: params.from,
    to: params.to,
    step: params.step,
    metrics: list(params.metrics),
    cluster_id: params.clusterId,
    node_id: params.nodeId,
    device_id: list(params.deviceIds),
    aggregate: params.aggregate,
  });
  return mapResult(await apiGet<Schemas['TimeSeriesResponse']>(`/metrics/gpu${qs}`));
}

export async function getInferenceMetrics(params: InferenceMetricsParams): Promise<MetricsResult> {
  const qs = toQuery({
    from: params.from,
    to: params.to,
    step: params.step,
    metrics: list(params.metrics),
    model_id: params.modelId,
    deployment_id: params.deploymentId,
    instance_id: list(params.instanceIds),
    node_id: params.nodeId,
    aggregate: params.aggregate,
  });
  return mapResult(await apiGet<Schemas['TimeSeriesResponse']>(`/metrics/inference${qs}`));
}

export async function getSessionMetrics(params: SessionMetricsParams): Promise<MetricsResult> {
  const qs = toQuery({
    from: params.from,
    to: params.to,
    step: params.step,
    instance_id: params.instanceId,
    session_key: params.sessionKey,
    request_id: params.requestId,
  });
  return mapResult(await apiGet<Schemas['TimeSeriesResponse']>(`/metrics/sessions${qs}`));
}

export async function getUsageMetrics(params: UsageMetricsParams): Promise<MetricsResult> {
  const qs = toQuery({
    from: params.from,
    to: params.to,
    step: params.step,
    metrics: list(params.metrics),
    api_key_id: params.apiKeyId,
    model_id: params.modelId,
    node_id: params.nodeId,
    deployment_id: params.deploymentId,
    group_by: params.groupBy,
  });
  return mapResult(await apiGet<Schemas['TimeSeriesResponse']>(`/metrics/usage${qs}`));
}

export async function getLiveSessions(scope: { deploymentId?: string; nodeId?: string; instanceId?: string } = {}): Promise<SessionsSnapshot[]> {
  const qs = toQuery({ deployment_id: scope.deploymentId, node_id: scope.nodeId, instance_id: scope.instanceId });
  const rows = await apiGet<Schemas['SessionsSnapshot'][]>(`/metrics/sessions/live${qs}`);
  return rows.map(mapSessionsSnapshot);
}

export async function getInstanceSessions(instanceId: string): Promise<SessionsSnapshot> {
  return mapSessionsSnapshot(await apiGet<Schemas['SessionsSnapshot']>(`/instances/${instanceId}/sessions`));
}

export async function getInstances(scope: { deploymentId?: string; nodeId?: string; state?: string } = {}): Promise<InstanceSummary[]> {
  const qs = toQuery({ deployment_id: scope.deploymentId, node_id: scope.nodeId, state: scope.state, page_size: 200 });
  const rows = await apiGet<Schemas['Instance'][]>(`/instances${qs}`);
  return rows.map(mapInstance);
}

export async function getInstance(instanceId: string): Promise<InstanceSummary> {
  return mapInstance(await apiGet<Schemas['Instance']>(`/instances/${instanceId}`));
}

export async function getDeployments(): Promise<DeploymentSummary[]> {
  const rows = await apiGet<Schemas['Deployment'][]>(`/deployments${toQuery({ page_size: 200 })}`);
  return rows.map(mapDeployment);
}

export async function getMetricCatalog(): Promise<MetricCatalogEntry[]> {
  return apiGet<MetricCatalogEntry[]>('/metrics/catalog');
}
