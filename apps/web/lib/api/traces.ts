/**
 * Traces API: root-span search (`GET /traces`) and a full trace (`GET /traces/{id}`).
 */

import { apiGet, toQuery } from '@/lib/api/client';
import { apiGetList } from '@/lib/api/paginated';
import type { components } from '@/lib/api/generated/admin';
import type { Trace, TraceListResult, TraceQuery, TraceSpan } from '@/types/traces';

type Schemas = components['schemas'];

export function mapSpan(row: Schemas['TraceSpan']): TraceSpan {
  return {
    ts: row.ts,
    traceId: row.trace_id,
    spanId: row.span_id,
    parentSpanId: row.parent_span_id ?? null,
    name: row.name,
    service: row.service,
    requestId: row.request_id ?? null,
    apiKeyId: row.api_key_id ?? null,
    modelId: row.model_id ?? null,
    deploymentId: row.deployment_id ?? null,
    instanceId: row.instance_id ?? null,
    nodeId: row.node_id ?? null,
    format: row.format ?? null,
    endpoint: row.endpoint ?? null,
    statusCode: row.status_code ?? null,
    durationMs: row.duration_ms ?? null,
    error: row.error,
    attributes: row.attributes ?? null,
  };
}

export async function getTraces(query: TraceQuery): Promise<TraceListResult> {
  const qs = toQuery({
    page: query.page,
    page_size: query.pageSize,
    from: query.from,
    to: query.to,
    service: query.service,
    api_key_id: query.apiKeyId,
    model_id: query.modelId,
    deployment_id: query.deploymentId,
    instance_id: query.instanceId,
    node_id: query.nodeId,
    format: query.format,
    status_code: query.statusCode,
    error: query.error ? 'true' : undefined,
    min_duration_ms: query.minDurationMs,
    request_id: query.requestId,
  });
  const { items, meta } = await apiGetList<Schemas['TraceSpan']>(`/traces${qs}`);
  return {
    items: items.map(mapSpan),
    total: meta.total,
    page: meta.page,
    pageSize: meta.page_size,
    hasMore: meta.has_more,
  };
}

export async function getTrace(traceId: string): Promise<Trace> {
  const row = await apiGet<Schemas['Trace']>(`/traces/${encodeURIComponent(traceId)}`);
  return {
    traceId: row.trace_id,
    root: row.root ? mapSpan(row.root) : null,
    spans: row.spans.map(mapSpan),
    durationMs: row.duration_ms ?? null,
    otlpUrl: row.otlp_url ?? null,
  };
}
