/**
 * UI types for request traces (OpenTelemetry index).
 */

import type { components } from '@/lib/api/generated/admin';

export type UsageFormat = components['schemas']['usage_format'];

export interface TraceSpan {
  ts: string;
  traceId: string;
  spanId: string;
  parentSpanId: string | null;
  name: string;
  service: string;
  requestId: string | null;
  apiKeyId: string | null;
  modelId: string | null;
  deploymentId: string | null;
  instanceId: string | null;
  nodeId: string | null;
  format: UsageFormat | null;
  endpoint: string | null;
  statusCode: number | null;
  durationMs: number | null;
  error: boolean;
  attributes: Record<string, unknown> | null;
}

export interface Trace {
  traceId: string;
  root: TraceSpan | null;
  spans: TraceSpan[];
  durationMs: number | null;
  otlpUrl: string | null;
}

export interface TraceQuery {
  page?: number;
  pageSize?: number;
  from?: string;
  to?: string;
  service?: string;
  apiKeyId?: string;
  modelId?: string;
  deploymentId?: string;
  instanceId?: string;
  nodeId?: string;
  format?: UsageFormat;
  statusCode?: number;
  error?: boolean;
  minDurationMs?: number;
  requestId?: string;
}

export interface TraceListResult {
  items: TraceSpan[];
  total: number;
  page: number;
  pageSize: number;
  hasMore: boolean;
}
