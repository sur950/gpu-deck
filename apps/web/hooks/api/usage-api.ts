/**
 * Usage API layer — metering (usage summary / events / CSV export), price
 * plans, billing estimate and billing reports. Also the two PATCH calls that
 * attach a price plan to a cluster or a node (the plan itself is standalone;
 * node overrides cluster).
 */

import { apiDelete, apiGet, apiPatch, apiPost, toQuery } from '@/lib/api/client';
import type { components } from '@/lib/api/generated/admin';
import { pagedGet, type Paged } from '@/hooks/api/automation-api';

type S = components['schemas'];

// ─── Metering ─────────────────────────────────────────────────────────────────

export type UsageFormat = S['usage_format'];
export type UsageStatus = S['usage_status'];
export type UsageGroupBy = 'api_key' | 'model' | 'node' | 'deployment' | 'cluster' | 'format' | 'endpoint' | 'day' | 'hour';

/** The API key id the billing job uses for GPU minutes nobody consumed (idle loaded models). */
export const UNALLOCATED_KEY = 'unallocated';

export interface UsageTotals {
  requestCount: number;
  successCount: number;
  errorCount: number;
  inputTokens: number;
  outputTokens: number;
  cachedTokens: number;
  reasoningTokens: number;
  mediaUnits: number;
  gpuSeconds: number;
  gpuMinutes: number;
  costUsd: number;
  latencyMsAvg: number | null;
  ttftMsAvg: number | null;
}

export interface UsageSummaryRow {
  /** group_by key → id */
  group: Record<string, string>;
  /** group_by key → display name */
  labels: Record<string, string>;
  totals: UsageTotals;
}

export interface UsageSummary {
  from: string;
  to: string;
  groupBy: string[];
  rows: UsageSummaryRow[];
  totals: UsageTotals;
}

export interface UsageFilters {
  from?: string;
  to?: string;
  apiKeyId?: string;
  modelId?: string;
  nodeId?: string;
  deploymentId?: string;
  clusterId?: string;
  format?: UsageFormat | '';
  status?: UsageStatus | '';
}

export interface UsageEvent {
  id: number;
  ts: string;
  requestId: string;
  apiKeyId: string | null;
  apiKeyName: string | null;
  modelId: string | null;
  modelName: string;
  deploymentId: string | null;
  nodeId: string | null;
  nodeName: string | null;
  format: UsageFormat;
  endpoint: string;
  status: UsageStatus;
  httpStatus: number | null;
  errorCode: string | null;
  stream: boolean;
  inputTokens: number;
  outputTokens: number;
  cachedTokens: number;
  reasoningTokens: number;
  gpuSeconds: number;
  costUsd: number;
  latencyMs: number | null;
  ttftMs: number | null;
  ipAddress: string | null;
  pricePlanId: string | null;
}

export function mapTotals(t: S['UsageTotals'] | undefined | null): UsageTotals {
  return {
    requestCount: t?.request_count ?? 0,
    successCount: t?.success_count ?? 0,
    errorCount: t?.error_count ?? 0,
    inputTokens: t?.input_tokens ?? 0,
    outputTokens: t?.output_tokens ?? 0,
    cachedTokens: t?.cached_tokens ?? 0,
    reasoningTokens: t?.reasoning_tokens ?? 0,
    mediaUnits: t?.media_units ?? 0,
    gpuSeconds: t?.gpu_seconds ?? 0,
    gpuMinutes: t?.gpu_minutes ?? (t?.gpu_seconds ?? 0) / 60,
    costUsd: t?.cost_usd ?? 0,
    latencyMsAvg: t?.latency_ms_avg ?? null,
    ttftMsAvg: t?.ttft_ms_avg ?? null,
  };
}

/** Display names for requests that never resolved a dimension (bad key, unknown model → no node). */
const EMPTY_GROUP_LABELS: Record<string, string> = {
  api_key: 'No API key',
  model: 'Unrouted (no model)',
  deployment: 'Unrouted (no deployment)',
  node: 'Unrouted (no node)',
  cluster: 'Unrouted (no cluster)',
};

/**
 * The API groups requests that never resolved a dimension under an empty key, and idle loaded
 * GPU time under an empty api_key labelled "unallocated" (summary rows and report line items
 * alike); give both a stable id and a readable name.
 */
export function normalizeGroup(
  rawGroup: Record<string, string | null | undefined> | null | undefined,
  rawLabels: Record<string, string | null | undefined> | null | undefined,
): { group: Record<string, string>; labels: Record<string, string> } {
  const group: Record<string, string> = {};
  const labels: Record<string, string> = {};
  for (const [k, v] of Object.entries(rawLabels ?? {})) if (v) labels[k] = v;
  for (const [k, v] of Object.entries(rawGroup ?? {})) {
    if (v === '' || v === null || v === undefined) {
      if (labels[k] === UNALLOCATED_KEY) {
        group[k] = UNALLOCATED_KEY;
        continue;
      }
      group[k] = 'unrouted';
      labels[k] = EMPTY_GROUP_LABELS[k] ?? 'None';
    } else {
      group[k] = v;
      if (!labels[k]) labels[k] = v;
    }
  }
  return { group, labels };
}

export function mapSummaryRow(r: S['UsageSummaryRow']): UsageSummaryRow {
  return { ...normalizeGroup(r.group, r.labels), totals: mapTotals(r.totals) };
}

function usageQuery(filters: UsageFilters, extra: Record<string, string | number | boolean | null | undefined> = {}): string {
  return toQuery({
    from: filters.from,
    to: filters.to,
    api_key_id: filters.apiKeyId,
    model_id: filters.modelId,
    node_id: filters.nodeId,
    deployment_id: filters.deploymentId,
    cluster_id: filters.clusterId,
    format: filters.format || undefined,
    status: filters.status || undefined,
    ...extra,
  });
}

export async function getUsageSummary(filters: UsageFilters, groupBy: UsageGroupBy[]): Promise<UsageSummary> {
  // `group_by` is repeatable: append each value by hand (toQuery keeps one value per key).
  const base = usageQuery(filters);
  const search = new URLSearchParams(base.startsWith('?') ? base.slice(1) : base);
  for (const g of groupBy) search.append('group_by', g);
  const qs = search.toString();
  const r = await apiGet<S['UsageSummary']>(`/usage/summary${qs ? `?${qs}` : ''}`);
  return {
    from: r.from,
    to: r.to,
    groupBy: r.group_by ?? groupBy,
    rows: (r.rows ?? []).map(mapSummaryRow),
    totals: mapTotals(r.totals),
  };
}

export function mapUsageEvent(e: S['UsageEvent']): UsageEvent {
  return {
    id: e.id,
    ts: e.ts,
    requestId: e.request_id,
    apiKeyId: e.api_key_id ?? null,
    apiKeyName: e.api_key_name ?? null,
    modelId: e.model_id ?? null,
    modelName: e.model_name,
    deploymentId: e.deployment_id ?? null,
    nodeId: e.node_id ?? null,
    nodeName: e.node_name ?? null,
    format: e.format,
    endpoint: e.endpoint,
    status: e.status,
    httpStatus: e.http_status ?? null,
    errorCode: e.error_code ?? null,
    stream: e.stream ?? false,
    inputTokens: e.input_tokens,
    outputTokens: e.output_tokens,
    cachedTokens: e.cached_tokens,
    reasoningTokens: e.reasoning_tokens ?? 0,
    gpuSeconds: e.gpu_seconds,
    costUsd: e.cost_usd,
    latencyMs: e.latency_ms ?? null,
    ttftMs: e.ttft_ms ?? null,
    ipAddress: e.ip_address ?? null,
    pricePlanId: e.price_plan_id ?? null,
  };
}

export function listUsageEvents(filters: UsageFilters, params: { page?: number; pageSize?: number; sort?: string; endpoint?: string; requestId?: string } = {}): Promise<Paged<UsageEvent>> {
  return pagedGet<S['UsageEvent'], UsageEvent>(
    `/usage${usageQuery(filters, { page: params.page, page_size: params.pageSize, sort: params.sort, endpoint: params.endpoint, request_id: params.requestId })}`,
    mapUsageEvent,
  );
}

/** Path (relative to the API base) for the CSV export of the current view. */
export function usageExportPath(filters: UsageFilters, mode: 'events' | 'summary', groupBy: UsageGroupBy[]): string {
  const base = usageQuery(filters, { mode });
  const search = new URLSearchParams(base.startsWith('?') ? base.slice(1) : base);
  if (mode === 'summary') for (const g of groupBy) search.append('group_by', g);
  return `/usage/export?${search.toString()}`;
}

// ─── Price plans ──────────────────────────────────────────────────────────────

export interface PricePlan {
  id: string;
  name: string;
  description: string | null;
  currency: string;
  isDefault: boolean;
  usdPerGpuMinute: number;
  usdPer1kInputTokens: number;
  usdPer1kOutputTokens: number;
  usdPer1kCachedTokens: number;
  createdAt: string | null;
  updatedAt: string | null;
}

export type PricePlanInput = S['PricePlanCreate'];

export function mapPricePlan(p: S['PricePlan']): PricePlan {
  return {
    id: p.id,
    name: p.name,
    description: p.description ?? null,
    currency: p.currency ?? 'USD',
    isDefault: p.is_default,
    usdPerGpuMinute: p.usd_per_gpu_minute ?? 0,
    usdPer1kInputTokens: p.usd_per_1k_input_tokens ?? 0,
    usdPer1kOutputTokens: p.usd_per_1k_output_tokens ?? 0,
    usdPer1kCachedTokens: p.usd_per_1k_cached_tokens ?? 0,
    createdAt: p.created_at ?? null,
    updatedAt: p.updated_at ?? null,
  };
}

export function listPricePlans(params: { page?: number; pageSize?: number; search?: string; sort?: string } = {}): Promise<Paged<PricePlan>> {
  return pagedGet<S['PricePlan'], PricePlan>(
    `/price-plans${toQuery({ page: params.page, page_size: params.pageSize ?? 100, search: params.search, sort: params.sort })}`,
    mapPricePlan,
  );
}

export async function getPricePlan(id: string): Promise<PricePlan> {
  return mapPricePlan(await apiGet<S['PricePlan']>(`/price-plans/${id}`));
}

export async function createPricePlan(body: PricePlanInput): Promise<PricePlan> {
  return mapPricePlan(await apiPost<S['PricePlan']>('/price-plans', body));
}

export async function updatePricePlan(id: string, body: Partial<PricePlanInput>): Promise<PricePlan> {
  return mapPricePlan(await apiPatch<S['PricePlan']>(`/price-plans/${id}`, body));
}

export function deletePricePlan(id: string): Promise<void> {
  return apiDelete<void>(`/price-plans/${id}`);
}

/** Attach / detach a plan on a cluster or node (`price_plan_id: null` detaches). */
export function setClusterPricePlan(clusterId: string, pricePlanId: string | null): Promise<unknown> {
  return apiPatch<S['Cluster']>(`/clusters/${clusterId}`, { price_plan_id: pricePlanId });
}

export function setNodePricePlan(nodeId: string, pricePlanId: string | null): Promise<unknown> {
  return apiPatch<S['Node']>(`/nodes/${nodeId}`, { price_plan_id: pricePlanId });
}

// ─── Billing ──────────────────────────────────────────────────────────────────

export type BillingReportStatus = S['billing_report_status'];
export type BillingGroupKey = 'api_key' | 'model' | 'node' | 'cluster' | 'deployment';

export interface BillingEstimate {
  asOf: string;
  periodStart: string;
  periodEnd: string;
  projectedCostUsd: number;
  totals: UsageTotals;
  byApiKey: UsageSummaryRow[];
  byModel: UsageSummaryRow[];
  byNode: UsageSummaryRow[];
}

export interface BillingLineItem {
  group: Record<string, string>;
  labels: Record<string, string>;
  pricePlanId: string | null;
  pricePlanName: string | null;
  totals: UsageTotals;
  costBreakdown: { gpuTimeUsd: number; inputTokensUsd: number; outputTokensUsd: number; cachedTokensUsd: number };
}

export interface BillingReport {
  id: string;
  name: string;
  status: BillingReportStatus;
  periodStart: string;
  periodEnd: string;
  groupBy: BillingGroupKey[];
  filters: Record<string, unknown>;
  currency: string;
  totals: UsageTotals | null;
  lineItems: BillingLineItem[];
  errorMessage: string | null;
  generatedAt: string | null;
  createdAt: string;
  updatedAt: string | null;
}

export type BillingReportInput = S['BillingReportCreate'];

export async function getBillingEstimate(params: { periodStart?: string; periodEnd?: string; clusterId?: string } = {}): Promise<BillingEstimate> {
  const r = await apiGet<S['BillingEstimate']>(
    `/billing/estimate${toQuery({ period_start: params.periodStart, period_end: params.periodEnd, cluster_id: params.clusterId })}`,
  );
  return {
    asOf: r.as_of,
    periodStart: r.period_start,
    periodEnd: r.period_end,
    projectedCostUsd: r.projected_cost_usd ?? 0,
    totals: mapTotals(r.totals),
    byApiKey: (r.by_api_key ?? []).map(mapSummaryRow),
    byModel: (r.by_model ?? []).map(mapSummaryRow),
    byNode: (r.by_node ?? []).map(mapSummaryRow),
  };
}

export function mapBillingReport(r: S['BillingReport']): BillingReport {
  return {
    id: r.id,
    name: r.name,
    status: r.status,
    periodStart: r.period_start,
    periodEnd: r.period_end,
    groupBy: (r.group_by ?? ['api_key', 'model']) as BillingGroupKey[],
    filters: r.filters ?? {},
    currency: r.currency ?? 'USD',
    totals: r.totals ? mapTotals(r.totals) : null,
    lineItems: (r.line_items ?? []).map((li) => ({
      ...normalizeGroup(li.group, li.labels),
      pricePlanId: li.price_plan_id ?? null,
      pricePlanName: li.price_plan_name ?? null,
      totals: mapTotals(li.totals),
      costBreakdown: {
        gpuTimeUsd: li.cost_breakdown?.gpu_time_usd ?? 0,
        inputTokensUsd: li.cost_breakdown?.input_tokens_usd ?? 0,
        outputTokensUsd: li.cost_breakdown?.output_tokens_usd ?? 0,
        cachedTokensUsd: li.cost_breakdown?.cached_tokens_usd ?? 0,
      },
    })),
    errorMessage: r.error_message ?? null,
    generatedAt: r.generated_at ?? null,
    createdAt: r.created_at,
    updatedAt: r.updated_at ?? null,
  };
}

export function listBillingReports(params: { page?: number; pageSize?: number; search?: string; status?: BillingReportStatus | ''; sort?: string } = {}): Promise<Paged<BillingReport>> {
  return pagedGet<S['BillingReport'], BillingReport>(
    `/billing/reports${toQuery({ page: params.page, page_size: params.pageSize, search: params.search, status: params.status || undefined, sort: params.sort })}`,
    mapBillingReport,
  );
}

export async function getBillingReport(id: string): Promise<BillingReport> {
  return mapBillingReport(await apiGet<S['BillingReport']>(`/billing/reports/${id}`));
}

export async function createBillingReport(body: BillingReportInput): Promise<BillingReport> {
  return mapBillingReport(await apiPost<S['BillingReport']>('/billing/reports', body));
}

export async function regenerateBillingReport(id: string): Promise<BillingReport> {
  return mapBillingReport(await apiPost<S['BillingReport']>(`/billing/reports/${id}/regenerate`));
}

export function deleteBillingReport(id: string): Promise<void> {
  return apiDelete<void>(`/billing/reports/${id}`);
}

export function billingReportExportPath(id: string): string {
  return `/billing/reports/${id}/export`;
}
