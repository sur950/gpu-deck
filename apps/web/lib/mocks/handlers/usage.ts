/**
 * Usage mocks: metering (`/usage`, `/usage/summary`, `/usage/export`),
 * price plans, billing estimate and monthly billing reports. Attaching a plan
 * to a cluster / node goes through the fleet mocks' `PATCH /clusters/:id` and
 * `PATCH /nodes/:id` (same shared fixture objects).
 *
 * Usage is derived from the same instance load curves as the observability
 * fixtures, at hourly resolution: requests and tokens split across API keys
 * and formats by fixed shares; billable GPU minutes come from instance
 * uptime (not per-request wall time) — the share of each instance-hour with no
 * traffic lands in the `unallocated` bucket.
 */

import { CLUSTER, IDS, INSTANCES } from '@/lib/mocks/fixtures/fleet';
import { fleetStore } from '@/lib/mocks/handlers/fleet';
import { hash32, round, uuid } from '@/lib/mocks/fixtures/random';
import { engineRequestsPerMinute, engineTtft } from '@/lib/mocks/fixtures/timeseries';
import { bodyOf, created, fail, noContent, notFound, ok, paginate, registerMockHandlers, sortBy } from '@/lib/mocks/registry';
import type { Schemas } from '@/lib/mocks/types';
import { apiKeyName, csvResponse, recordAudit } from '@/lib/mocks/handlers/security';

type PricePlan = Schemas['PricePlan'];
type UsageTotals = Schemas['UsageTotals'];
type BillingReport = Schemas['BillingReport'];

const HOUR = 3_600_000;
const DAY = 24 * HOUR;
const BOOT = Date.now();
const iso = (ms: number) => new Date(ms).toISOString();
const UNALLOCATED = 'unallocated';

// ─── Price plans ──────────────────────────────────────────────────────────────

/** Price plans live in the fleet store (the cluster / node forms pick from the same list). */
const plans: PricePlan[] = fleetStore.pricePlans;

const deploymentById = (id: string) => fleetStore.deployments.find((d) => d.id === id);
const nodeById = (id: string) => fleetStore.nodes.find((n) => n.id === id);
const clusterById = (id: string) => fleetStore.clusters.find((c) => c.id === id);

/** Changes whenever a plan's rates or a cluster / node attachment change (invalidates cached cost cells). */
function pricingSignature(): string {
  return JSON.stringify([
    plans.map((p) => [p.id, p.is_default, p.usd_per_gpu_minute, p.usd_per_1k_input_tokens, p.usd_per_1k_output_tokens, p.usd_per_1k_cached_tokens]),
    fleetStore.clusters.map((c) => [c.id, c.price_plan_id]),
    fleetStore.nodes.map((n) => [n.id, n.price_plan_id]),
  ]);
}

const FREE_PLAN: PricePlan = { id: 'none', name: 'No plan', description: null, currency: 'USD', is_default: false, usd_per_gpu_minute: 0, usd_per_1k_input_tokens: 0, usd_per_1k_output_tokens: 0, usd_per_1k_cached_tokens: 0 };

/** Node plan overrides the cluster plan; else the default plan. */
function planForNode(nodeId: string): PricePlan {
  const node = nodeById(nodeId);
  const cluster = node ? clusterById(node.cluster_id) : undefined;
  const id = node?.price_plan_id ?? cluster?.price_plan_id ?? null;
  return plans.find((p) => p.id === id) ?? plans.find((p) => p.is_default) ?? plans[0] ?? FREE_PLAN;
}

// ─── Usage model ──────────────────────────────────────────────────────────────

interface Profile {
  inTokens: number;
  outTokens: number;
  cachedRatio: number;
  reasoningRatio: number;
  errorRate: number;
  keys: [string, number][];
  formats: [Schemas['usage_format'], number, string][];
}

const PROFILES: Record<string, Profile> = {
  [IDS.deployments.qwen]: {
    inTokens: 940,
    outTokens: 410,
    cachedRatio: 0.46,
    reasoningRatio: 0.32,
    errorRate: 0.008,
    keys: [[IDS.apiKeys.gateway, 0.74], [IDS.apiKeys.evals, 0.17], [IDS.apiKeys.indexer, 0.09]],
    formats: [['anthropic', 0.52, '/v1/messages'], ['openai', 0.4, '/v1/chat/completions'], ['ollama', 0.08, '/api/chat']],
  },
  [IDS.deployments.gemma]: {
    inTokens: 620,
    outTokens: 290,
    cachedRatio: 0.18,
    reasoningRatio: 0,
    errorRate: 0.012,
    keys: [[IDS.apiKeys.evals, 0.62], [IDS.apiKeys.gateway, 0.38]],
    formats: [['openai', 0.7, '/v1/chat/completions'], ['anthropic', 0.3, '/v1/messages']],
  },
  [IDS.deployments.nomic]: {
    inTokens: 380,
    outTokens: 0,
    cachedRatio: 0.03,
    reasoningRatio: 0,
    errorRate: 0.002,
    keys: [[IDS.apiKeys.indexer, 0.86], [IDS.apiKeys.gateway, 0.14]],
    formats: [['ollama', 0.58, '/api/embed'], ['openai', 0.42, '/v1/embeddings']],
  },
};

interface Cell {
  hour: number;
  apiKeyId: string;
  deploymentId: string;
  modelId: string;
  nodeId: string;
  clusterId: string;
  format: Schemas['usage_format'] | null;
  endpoint: string | null;
  totals: UsageTotals;
}

function emptyTotals(): UsageTotals {
  return { request_count: 0, success_count: 0, error_count: 0, input_tokens: 0, output_tokens: 0, cached_tokens: 0, reasoning_tokens: 0, media_units: 0, gpu_seconds: 0, gpu_minutes: 0, cost_usd: 0, latency_ms_avg: null, ttft_ms_avg: null };
}

function add(into: UsageTotals, t: UsageTotals): void {
  const prevReq = into.request_count;
  into.request_count += t.request_count;
  into.success_count = (into.success_count ?? 0) + (t.success_count ?? 0);
  into.error_count = (into.error_count ?? 0) + (t.error_count ?? 0);
  into.input_tokens += t.input_tokens;
  into.output_tokens += t.output_tokens;
  into.cached_tokens += t.cached_tokens;
  into.reasoning_tokens = (into.reasoning_tokens ?? 0) + (t.reasoning_tokens ?? 0);
  into.media_units = (into.media_units ?? 0) + (t.media_units ?? 0);
  into.gpu_seconds += t.gpu_seconds;
  into.gpu_minutes = round((into.gpu_minutes ?? 0) + (t.gpu_minutes ?? 0), 3);
  into.cost_usd = round(into.cost_usd + t.cost_usd, 6);
  // Request-weighted means.
  for (const key of ['latency_ms_avg', 'ttft_ms_avg'] as const) {
    const a = into[key];
    const b = t[key];
    if (b === null || b === undefined || t.request_count === 0) continue;
    into[key] = a === null || a === undefined || prevReq === 0 ? b : round((a * prevReq + b * t.request_count) / (prevReq + t.request_count), 1);
  }
}

const cellCache = new Map<number, Cell[]>();
let cacheSignature = '';

/** All cells for one hour bucket (cached — the curves are deterministic). */
function cellsForHour(hour: number): Cell[] {
  const cached = cellCache.get(hour);
  if (cached) return cached;
  const out: Cell[] = [];
  const mid = hour + HOUR / 2;
  for (const inst of INSTANCES) {
    const profile = PROFILES[inst.deployment_id];
    const deployment = deploymentById(inst.deployment_id);
    if (!profile || !deployment) continue;
    // Instance-level traffic for the hour (three samples smooth the curve).
    const rpm = (engineRequestsPerMinute(inst.id, hour + 10 * 60_000) + engineRequestsPerMinute(inst.id, mid) + engineRequestsPerMinute(inst.id, hour + 50 * 60_000)) / 3;
    const requests = Math.round(rpm * 60);
    const plan = planForNode(inst.node_id);
    const ttft = engineTtft(inst.id, mid, 'avg');
    // Uptime-based GPU minutes; the idle share of the hour is unallocated.
    const busy = Math.min(1, rpm / (inst.deployment_id === IDS.deployments.nomic ? 30 : 14));
    const gpuMinutes = 60 * (deployment.gpu_count ?? 1);
    const allocated = gpuMinutes * Math.max(0.04, busy);
    const unallocated = gpuMinutes - allocated;
    const base = { hour, deploymentId: inst.deployment_id, modelId: deployment.model_id ?? '', nodeId: inst.node_id, clusterId: deployment.cluster_id ?? CLUSTER.id };

    for (const [apiKeyId, keyShare] of profile.keys) {
      for (const [format, formatShare, endpoint] of profile.formats) {
        const share = keyShare * formatShare;
        const req = Math.round(requests * share);
        if (req === 0 && allocated * share < 0.01) continue;
        const jitter = 0.9 + (hash32(`${hour}:${inst.id}:${apiKeyId}:${format}`) % 200) / 1000;
        const input = Math.round(req * profile.inTokens * jitter);
        const output = Math.round(req * profile.outTokens * (2 - jitter));
        const cachedTokens = Math.round(input * profile.cachedRatio);
        const errors = Math.round(req * profile.errorRate * (format === 'ollama' ? 1.6 : 1));
        const gpuMin = allocated * share;
        const cost = gpuMin * plan.usd_per_gpu_minute + ((input - cachedTokens) / 1000) * plan.usd_per_1k_input_tokens + (output / 1000) * plan.usd_per_1k_output_tokens + (cachedTokens / 1000) * plan.usd_per_1k_cached_tokens;
        out.push({
          ...base,
          apiKeyId,
          format,
          endpoint,
          totals: {
            request_count: req,
            success_count: req - errors,
            error_count: errors,
            input_tokens: input,
            output_tokens: output,
            cached_tokens: cachedTokens,
            reasoning_tokens: Math.round(output * profile.reasoningRatio),
            media_units: 0,
            gpu_seconds: round(gpuMin * 60, 1),
            gpu_minutes: round(gpuMin, 3),
            cost_usd: round(cost, 6),
            latency_ms_avg: req ? round(ttft + profile.outTokens * 9 * jitter, 1) : null,
            ttft_ms_avg: req ? round(ttft, 1) : null,
          },
        });
      }
    }
    if (unallocated > 0) {
      out.push({
        ...base,
        apiKeyId: UNALLOCATED,
        format: null,
        endpoint: null,
        totals: { ...emptyTotals(), gpu_seconds: round(unallocated * 60, 1), gpu_minutes: round(unallocated, 3), cost_usd: round(unallocated * plan.usd_per_gpu_minute, 6) },
      });
    }
  }
  if (hour + HOUR < Date.now()) cellCache.set(hour, out);
  return out;
}

interface UsageQuery {
  from: number;
  to: number;
  apiKeyId: string | null;
  modelId: string | null;
  nodeId: string | null;
  deploymentId: string | null;
  clusterId: string | null;
  format: string | null;
  status: string | null;
}

function parseUsageQuery(q: URLSearchParams, defaultDays = 7): UsageQuery {
  const now = Date.now();
  const to = q.get('to') ? Date.parse(q.get('to') as string) : now;
  const from = q.get('from') ? Date.parse(q.get('from') as string) : to - defaultDays * DAY;
  return {
    from: Number.isFinite(from) ? from : now - defaultDays * DAY,
    to: Number.isFinite(to) ? Math.min(to, now) : now,
    apiKeyId: q.get('api_key_id'),
    modelId: q.get('model_id'),
    nodeId: q.get('node_id'),
    deploymentId: q.get('deployment_id'),
    clusterId: q.get('cluster_id'),
    format: q.get('format'),
    status: q.get('status'),
  };
}

function selectCells(q: UsageQuery): Cell[] {
  const signature = pricingSignature();
  if (signature !== cacheSignature) {
    cellCache.clear();
    cacheSignature = signature;
  }
  const out: Cell[] = [];
  const start = Math.floor(q.from / HOUR) * HOUR;
  for (let hour = start; hour < q.to; hour += HOUR) {
    // Partial first / last hour: scale by the covered fraction.
    const covered = (Math.min(q.to, hour + HOUR) - Math.max(q.from, hour)) / HOUR;
    if (covered <= 0) continue;
    for (const cell of cellsForHour(hour)) {
      if (q.apiKeyId && cell.apiKeyId !== q.apiKeyId) continue;
      if (q.modelId && cell.modelId !== q.modelId) continue;
      if (q.nodeId && cell.nodeId !== q.nodeId) continue;
      if (q.deploymentId && cell.deploymentId !== q.deploymentId) continue;
      if (q.clusterId && cell.clusterId !== q.clusterId) continue;
      if (q.format && cell.format !== q.format) continue;
      let totals = cell.totals;
      if (q.status === 'error' || q.status === 'timeout' || q.status === 'cancelled') {
        if (cell.apiKeyId === UNALLOCATED) continue;
        const errors = Math.round((totals.error_count ?? 0) * (q.status === 'error' ? 0.8 : 0.1));
        totals = { ...emptyTotals(), request_count: errors, error_count: errors };
      } else if (q.status === 'success') {
        if (cell.apiKeyId === UNALLOCATED) continue;
        totals = { ...totals, request_count: totals.success_count ?? totals.request_count, error_count: 0 };
      }
      if (covered < 1) {
        const s = (n: number) => Math.round(n * covered);
        totals = { ...totals, request_count: s(totals.request_count), success_count: s(totals.success_count ?? 0), error_count: s(totals.error_count ?? 0), input_tokens: s(totals.input_tokens), output_tokens: s(totals.output_tokens), cached_tokens: s(totals.cached_tokens), reasoning_tokens: s(totals.reasoning_tokens ?? 0), gpu_seconds: round(totals.gpu_seconds * covered, 1), gpu_minutes: round((totals.gpu_minutes ?? 0) * covered, 3), cost_usd: round(totals.cost_usd * covered, 6) };
      }
      out.push({ ...cell, totals });
    }
  }
  return out;
}

type GroupKey = 'api_key' | 'model' | 'node' | 'deployment' | 'cluster' | 'format' | 'endpoint' | 'day' | 'hour';

function groupValue(cell: Cell, key: GroupKey): { id: string; label: string } {
  switch (key) {
    case 'api_key':
      // Same wire shape as the backend: idle loaded time has an empty id labelled "unallocated".
      return cell.apiKeyId === UNALLOCATED ? { id: '', label: UNALLOCATED } : { id: cell.apiKeyId, label: apiKeyName(cell.apiKeyId) };
    case 'model': {
      const d = fleetStore.deployments.find((x) => x.model_id === cell.modelId);
      return { id: cell.modelId, label: d?.model_name ?? cell.modelId };
    }
    case 'node':
      return { id: cell.nodeId, label: nodeById(cell.nodeId)?.name ?? cell.nodeId };
    case 'deployment':
      return { id: cell.deploymentId, label: deploymentById(cell.deploymentId)?.name ?? cell.deploymentId };
    case 'cluster':
      return { id: cell.clusterId, label: clusterById(cell.clusterId)?.name ?? cell.clusterId };
    case 'format':
      return { id: cell.format ?? UNALLOCATED, label: cell.format ?? 'Unallocated' };
    case 'endpoint':
      return { id: cell.endpoint ?? UNALLOCATED, label: cell.endpoint ?? 'Unallocated' };
    case 'day':
      return { id: iso(cell.hour).slice(0, 10), label: iso(cell.hour).slice(0, 10) };
    case 'hour':
      return { id: iso(cell.hour).slice(0, 13) + ':00:00Z', label: iso(cell.hour).slice(0, 13) + ':00' };
  }
}

function summarize(cells: Cell[], groupBy: GroupKey[]): Schemas['UsageSummaryRow'][] {
  const rows = new Map<string, Schemas['UsageSummaryRow']>();
  for (const cell of cells) {
    const parts = groupBy.map((k) => ({ k, ...groupValue(cell, k) }));
    const key = parts.map((p) => p.id).join('|');
    let row = rows.get(key);
    if (!row) {
      row = { group: Object.fromEntries(parts.map((p) => [p.k, p.id])), labels: Object.fromEntries(parts.map((p) => [p.k, p.label])), totals: emptyTotals() };
      rows.set(key, row);
    }
    add(row.totals, cell.totals);
  }
  return [...rows.values()].sort((a, b) => {
    const time = groupBy.find((k) => k === 'day' || k === 'hour');
    if (time && a.group[time] !== b.group[time]) return a.group[time] < b.group[time] ? -1 : 1;
    return b.totals.cost_usd - a.totals.cost_usd;
  });
}

function totalOf(cells: Cell[]): UsageTotals {
  const t = emptyTotals();
  for (const c of cells) add(t, c.totals);
  return t;
}

function groupByParam(q: URLSearchParams, fallback: GroupKey[] = ['api_key']): GroupKey[] {
  const valid: GroupKey[] = ['api_key', 'model', 'node', 'deployment', 'cluster', 'format', 'endpoint', 'day', 'hour'];
  const list = q.getAll('group_by').flatMap((v) => v.split(',')).filter((v): v is GroupKey => (valid as string[]).includes(v));
  return list.length ? list : fallback;
}

// ─── Usage events (request log) ───────────────────────────────────────────────

function eventsBetween(q: UsageQuery, limit = 2_000): Schemas['UsageEvent'][] {
  const out: Schemas['UsageEvent'][] = [];
  const spacing = 7_000;
  let t = Math.floor(q.to / spacing) * spacing;
  let n = 0;
  while (t > q.from && out.length < limit && n < 20_000) {
    n += 1;
    const seed = hash32(`event:${t}`);
    const inst = INSTANCES[seed % INSTANCES.length];
    const profile = PROFILES[inst.deployment_id];
    const deployment = deploymentById(inst.deployment_id);
    t -= spacing + (seed % 5_000);
    if (!profile || !deployment) continue;
    const keyPick = (seed % 1000) / 1000;
    let acc = 0;
    const apiKeyId = profile.keys.find(([, share]) => (acc += share) >= keyPick)?.[0] ?? profile.keys[0][0];
    const fPick = ((seed >>> 10) % 1000) / 1000;
    acc = 0;
    const fmt = profile.formats.find(([, share]) => (acc += share) >= fPick) ?? profile.formats[0];
    const isError = (seed >>> 3) % 1000 < profile.errorRate * 1000 * 2;
    const status: Schemas['usage_status'] = isError ? ((seed >>> 5) % 4 === 0 ? 'timeout' : 'error') : 'success';
    const input = isError ? Math.round(profile.inTokens * 0.6) : Math.round(profile.inTokens * (0.3 + ((seed >>> 7) % 1400) / 1000));
    const output = isError ? 0 : Math.round(profile.outTokens * (0.2 + ((seed >>> 11) % 1600) / 1000));
    const cachedTokens = Math.round(input * profile.cachedRatio * (((seed >>> 13) % 100) / 60));
    const plan = planForNode(inst.node_id);
    const latency = Math.round(engineTtft(inst.id, t, 'avg') + output * 8.5);
    const event: Schemas['UsageEvent'] = {
      id: Math.floor(t / 1000),
      ts: iso(t),
      request_id: `req_${(seed >>> 0).toString(36)}${(t % 9973).toString(36)}`,
      api_key_id: apiKeyId,
      api_key_name: apiKeyName(apiKeyId),
      model_id: deployment.model_id,
      model_name: deployment.name,
      deployment_id: deployment.id,
      instance_id: inst.id,
      node_id: inst.node_id,
      node_name: inst.node_name ?? null,
      cluster_id: deployment.cluster_id ?? CLUSTER.id,
      format: fmt[0],
      endpoint: fmt[2],
      status,
      http_status: status === 'success' ? 200 : status === 'timeout' ? 504 : 500,
      error_code: status === 'success' ? null : status === 'timeout' ? 'upstream_timeout' : 'engine_error',
      stream: fmt[2].includes('embed') ? false : (seed >>> 2) % 3 !== 0,
      input_tokens: input,
      output_tokens: output,
      cached_tokens: Math.min(cachedTokens, input),
      reasoning_tokens: Math.round(output * profile.reasoningRatio),
      gpu_seconds: round((latency / 1000) * 0.35, 3),
      gpu_count: deployment.gpu_count ?? 1,
      cost_usd: round(((input - cachedTokens) / 1000) * plan.usd_per_1k_input_tokens + (output / 1000) * plan.usd_per_1k_output_tokens + (latency / 60_000) * 0.35 * plan.usd_per_gpu_minute, 6),
      latency_ms: latency,
      ttft_ms: Math.round(engineTtft(inst.id, t, 'avg')),
      ip_address: apiKeyId === IDS.apiKeys.gateway ? '192.168.1.10' : `10.20.${(seed >>> 4) % 8}.${(seed >>> 9) % 250}`,
      price_plan_id: plan.id,
      request_count: 1,
      media_units: 0,
      metadata: null,
    };
    if (q.apiKeyId && event.api_key_id !== q.apiKeyId) continue;
    if (q.modelId && event.model_id !== q.modelId) continue;
    if (q.nodeId && event.node_id !== q.nodeId) continue;
    if (q.deploymentId && event.deployment_id !== q.deploymentId) continue;
    if (q.format && event.format !== q.format) continue;
    if (q.status && event.status !== q.status) continue;
    out.push(event);
  }
  return out;
}

// ─── Billing ──────────────────────────────────────────────────────────────────

function monthStart(ms: number): number {
  const d = new Date(ms);
  return Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), 1);
}

function nextMonth(ms: number): number {
  const d = new Date(ms);
  return Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, 1);
}

interface ReportState {
  report: BillingReport;
  readyAt: number;
}

function buildLineItems(r: BillingReport): Schemas['BillingLineItem'][] {
  const q: UsageQuery = { from: Date.parse(r.period_start), to: Date.parse(r.period_end), apiKeyId: null, modelId: null, nodeId: null, deploymentId: null, clusterId: (r.filters?.cluster_id as string | undefined) ?? null, format: null, status: null };
  const groupBy = (r.group_by?.length ? r.group_by : ['api_key', 'model']) as GroupKey[];
  const cells = selectCells(q);
  // Group with the node so each line knows its price plan, then fold.
  const rows = summarize(cells, [...groupBy, ...(groupBy.includes('node') ? [] : (['node'] as GroupKey[]))]);
  const lines = new Map<string, Schemas['BillingLineItem']>();
  for (const row of rows) {
    const key = groupBy.map((k) => row.group[k]).join('|');
    const plan = planForNode(row.group.node);
    const existing = lines.get(key);
    const t = row.totals;
    const breakdown = {
      gpu_time_usd: round((t.gpu_minutes ?? 0) * plan.usd_per_gpu_minute, 4),
      input_tokens_usd: round(((t.input_tokens - t.cached_tokens) / 1000) * plan.usd_per_1k_input_tokens, 4),
      output_tokens_usd: round((t.output_tokens / 1000) * plan.usd_per_1k_output_tokens, 4),
      cached_tokens_usd: round((t.cached_tokens / 1000) * plan.usd_per_1k_cached_tokens, 4),
    };
    if (!existing) {
      lines.set(key, { group: Object.fromEntries(groupBy.map((k) => [k, row.group[k]])), labels: Object.fromEntries(groupBy.map((k) => [k, row.labels[k]])), price_plan_id: plan.id, price_plan_name: plan.name, totals: { ...t }, cost_breakdown: breakdown });
    } else {
      add(existing.totals, t);
      const b = existing.cost_breakdown ?? {};
      existing.cost_breakdown = {
        gpu_time_usd: round((b.gpu_time_usd ?? 0) + breakdown.gpu_time_usd, 4),
        input_tokens_usd: round((b.input_tokens_usd ?? 0) + breakdown.input_tokens_usd, 4),
        output_tokens_usd: round((b.output_tokens_usd ?? 0) + breakdown.output_tokens_usd, 4),
        cached_tokens_usd: round((b.cached_tokens_usd ?? 0) + breakdown.cached_tokens_usd, 4),
      };
      if (existing.price_plan_id !== plan.id) existing.price_plan_name = 'Mixed';
    }
  }
  return [...lines.values()].sort((a, b) => b.totals.cost_usd - a.totals.cost_usd);
}

function materializeReport(s: ReportState, now = Date.now()): BillingReport {
  if (s.report.status === 'failed') return s.report;
  if (now < s.readyAt) return { ...s.report, status: now < s.readyAt - 2_500 ? 'pending' : 'generating', line_items: [], totals: undefined };
  if (s.report.status !== 'ready') {
    const items = buildLineItems(s.report);
    const totals = emptyTotals();
    for (const li of items) add(totals, li.totals);
    s.report = { ...s.report, status: 'ready', line_items: items, totals, generated_at: iso(s.readyAt), updated_at: iso(s.readyAt) };
  }
  return s.report;
}

const reports: ReportState[] = (() => {
  const thisMonth = monthStart(BOOT);
  const prev = monthStart(thisMonth - 1);
  const prev2 = monthStart(prev - 1);
  const label = (ms: number) => new Date(ms).toLocaleString('en-US', { month: 'long', year: 'numeric', timeZone: 'UTC' });
  const mk = (id: string, name: string, start: number, end: number, groupBy: BillingReport['group_by'], createdAt: number, status: BillingReport['status'] = 'ready'): ReportState => ({
    report: { id, name, period_start: iso(start), period_end: iso(end), group_by: groupBy, filters: {}, currency: 'USD', status, created_at: iso(createdAt), updated_at: iso(createdAt), generated_at: null, error_message: status === 'failed' ? 'usage_rollups for 2026-09-14 were still being backfilled — regenerate' : null },
    readyAt: createdAt + 4_000,
  });
  return [
    mk(uuid('report:mtd'), `${label(thisMonth)} — month to date`, thisMonth, Math.floor(BOOT / HOUR) * HOUR, ['api_key', 'model'], BOOT - 2 * HOUR),
    mk(uuid('report:prev'), label(prev), prev, thisMonth, ['api_key', 'model'], thisMonth + 2 * HOUR),
    mk(uuid('report:prev-node'), `${label(prev)} — by node`, prev, thisMonth, ['node'], thisMonth + 2 * HOUR + 60_000),
    mk(uuid('report:prev2'), label(prev2), prev2, prev, ['api_key', 'model'], prev + 2 * HOUR, 'failed'),
  ];
})();

function reportById(id: string): ReportState | undefined {
  return reports.find((r) => r.report.id === id);
}

function usageCsv(rows: Schemas['UsageSummaryRow'][], groupBy: GroupKey[]): (string | number | null)[][] {
  const header = [...groupBy, ...groupBy.map((k) => `${k}_label`), 'requests', 'errors', 'input_tokens', 'output_tokens', 'cached_tokens', 'reasoning_tokens', 'gpu_minutes', 'cost_usd'];
  return [header, ...rows.map((r) => [...groupBy.map((k) => r.group[k]), ...groupBy.map((k) => r.labels[k]), r.totals.request_count, r.totals.error_count ?? 0, r.totals.input_tokens, r.totals.output_tokens, r.totals.cached_tokens, r.totals.reasoning_tokens ?? 0, r.totals.gpu_minutes ?? 0, r.totals.cost_usd])];
}

// ─── Handlers ─────────────────────────────────────────────────────────────────

registerMockHandlers('usage', [
  // ── Metering ──
  {
    method: 'GET',
    path: '/usage',
    handle: (req) => {
      const q = parseUsageQuery(req.query, 1);
      const sort = req.query.get('sort');
      const requestId = req.query.get('request_id');
      const endpoint = req.query.get('endpoint');
      let list = eventsBetween(q).filter((e) => (!requestId || e.request_id === requestId) && (!endpoint || e.endpoint === endpoint));
      if (sort && sort !== '-ts') list = sortBy(list as unknown as Record<string, unknown>[], sort) as unknown as Schemas['UsageEvent'][];
      return paginate(list, req.query, 25);
    },
  },
  {
    method: 'GET',
    path: '/usage/summary',
    handle: (req) => {
      const q = parseUsageQuery(req.query);
      const groupBy = groupByParam(req.query);
      const cells = selectCells(q);
      const out: Schemas['UsageSummary'] = { from: iso(q.from), to: iso(q.to), group_by: groupBy, rows: summarize(cells, groupBy), totals: totalOf(cells) };
      return ok(out);
    },
  },
  {
    method: 'GET',
    path: '/usage/export',
    handle: (req) => {
      const q = parseUsageQuery(req.query);
      const stamp = new Date().toISOString().slice(0, 10);
      if (req.query.get('mode') === 'summary') {
        const groupBy = groupByParam(req.query);
        return csvResponse(usageCsv(summarize(selectCells(q), groupBy), groupBy), `gpu-deck-usage-summary-${stamp}.csv`);
      }
      const rows = eventsBetween(q, 5_000).map((e) => [e.ts, e.request_id, e.api_key_name, e.model_name, e.node_name, e.format, e.endpoint, e.status, e.http_status, e.input_tokens, e.output_tokens, e.cached_tokens, e.reasoning_tokens, e.latency_ms, e.ttft_ms, e.gpu_seconds, e.cost_usd]);
      return csvResponse([['ts', 'request_id', 'api_key', 'model', 'node', 'format', 'endpoint', 'status', 'http_status', 'input_tokens', 'output_tokens', 'cached_tokens', 'reasoning_tokens', 'latency_ms', 'ttft_ms', 'gpu_seconds', 'cost_usd'], ...rows], `gpu-deck-usage-events-${stamp}.csv`);
    },
  },

  // ── Price plans ──
  {
    method: 'GET',
    path: '/price-plans',
    handle: (req) => {
      const search = (req.query.get('search') ?? '').toLowerCase();
      const list = plans.filter((p) => !search || p.name.toLowerCase().includes(search));
      return paginate(sortBy(list as unknown as Record<string, unknown>[], req.query.get('sort'), 'created_at') as unknown as PricePlan[], req.query, 100);
    },
  },
  {
    method: 'POST',
    path: '/price-plans',
    handle: (req) => {
      const body = bodyOf<Schemas['PricePlanCreate']>(req);
      if (!body.name?.trim()) return fail(422, 'VALIDATION_ERROR', 'name is required');
      if (plans.some((p) => p.name.toLowerCase() === body.name?.trim().toLowerCase())) return fail(409, 'CONFLICT', `A plan named "${body.name}" already exists`);
      const now = Date.now();
      const plan: PricePlan = { id: uuid(`price-plan:new:${body.name}:${now}`), name: body.name.trim(), description: body.description ?? null, currency: 'USD', is_default: body.is_default ?? false, usd_per_gpu_minute: body.usd_per_gpu_minute ?? 0, usd_per_1k_input_tokens: body.usd_per_1k_input_tokens ?? 0, usd_per_1k_output_tokens: body.usd_per_1k_output_tokens ?? 0, usd_per_1k_cached_tokens: body.usd_per_1k_cached_tokens ?? 0, created_at: iso(now), updated_at: iso(now) };
      if (plan.is_default) for (const p of plans) p.is_default = false;
      plans.push(plan);
      recordAudit({ eventType: 'price_plan.created', resourceType: 'price_plan', resourceId: plan.id, resourceName: plan.name, after: { usd_per_gpu_minute: plan.usd_per_gpu_minute, usd_per_1k_input_tokens: plan.usd_per_1k_input_tokens, usd_per_1k_output_tokens: plan.usd_per_1k_output_tokens, is_default: plan.is_default } });
      return created(plan);
    },
  },
  {
    method: 'GET',
    path: '/price-plans/:plan_id',
    handle: (req) => {
      const p = plans.find((x) => x.id === req.params.plan_id);
      return p ? ok(p) : notFound('Price plan');
    },
  },
  {
    method: 'PATCH',
    path: '/price-plans/:plan_id',
    handle: (req) => {
      const p = plans.find((x) => x.id === req.params.plan_id);
      if (!p) return notFound('Price plan');
      const body = bodyOf<Schemas['PricePlanCreate']>(req);
      const before = Object.fromEntries(Object.keys(body).map((k) => [k, (p as Record<string, unknown>)[k]]));
      if (body.is_default) for (const other of plans) other.is_default = false;
      Object.assign(p, body, { updated_at: iso(Date.now()) });
      recordAudit({ eventType: 'price_plan.updated', resourceType: 'price_plan', resourceId: p.id, resourceName: p.name, before, after: body as Record<string, unknown> });
      return ok(p);
    },
  },
  {
    method: 'DELETE',
    path: '/price-plans/:plan_id',
    handle: (req) => {
      const idx = plans.findIndex((x) => x.id === req.params.plan_id);
      if (idx < 0) return notFound('Price plan');
      const p = plans[idx];
      const users = [...fleetStore.clusters.filter((c) => c.price_plan_id === p.id).map((c) => `cluster ${c.name}`), ...fleetStore.nodes.filter((n) => n.price_plan_id === p.id && n.status !== 'removed').map((n) => `node ${n.name}`)];
      if (users.length) return fail(409, 'CONFLICT', `Attached to ${users.join(', ')} — detach it first`);
      if (p.is_default) return fail(409, 'CONFLICT', 'The default plan cannot be deleted — make another plan the default first');
      plans.splice(idx, 1);
      recordAudit({ eventType: 'price_plan.deleted', resourceType: 'price_plan', resourceId: p.id, resourceName: p.name });
      return noContent();
    },
  },
  // ── Billing ──
  {
    method: 'GET',
    path: '/billing/estimate',
    handle: (req) => {
      const now = Date.now();
      const start = req.query.get('period_start') ? Date.parse(req.query.get('period_start') as string) : monthStart(now);
      const end = req.query.get('period_end') ? Date.parse(req.query.get('period_end') as string) : nextMonth(start);
      const q: UsageQuery = { from: start, to: Math.min(now, end), apiKeyId: null, modelId: null, nodeId: null, deploymentId: null, clusterId: req.query.get('cluster_id'), format: null, status: null };
      const cells = selectCells(q);
      const totals = totalOf(cells);
      const elapsed = Math.max(HOUR, Math.min(now, end) - start);
      const out: Schemas['BillingEstimate'] = {
        as_of: iso(now),
        period_start: iso(start),
        period_end: iso(end),
        projected_cost_usd: round((totals.cost_usd * (end - start)) / elapsed, 2),
        totals,
        by_api_key: summarize(cells, ['api_key']),
        by_model: summarize(cells, ['model']),
        by_node: summarize(cells, ['node']),
      };
      return ok(out);
    },
  },
  {
    method: 'GET',
    path: '/billing/reports',
    handle: (req) => {
      const search = (req.query.get('search') ?? '').toLowerCase();
      const status = req.query.get('status');
      const now = Date.now();
      const list = reports
        .map((s) => materializeReport(s, now))
        .filter((r) => (!search || r.name.toLowerCase().includes(search)) && (!status || r.status === status))
        .map((r) => ({ ...r, line_items: undefined }));
      return paginate(sortBy(list as unknown as Record<string, unknown>[], req.query.get('sort'), '-period_start') as unknown as BillingReport[], req.query, 25);
    },
  },
  {
    method: 'POST',
    path: '/billing/reports',
    handle: (req) => {
      const body = bodyOf<Schemas['BillingReportCreate']>(req);
      if (!body.name?.trim() || !body.period_start || !body.period_end) return fail(422, 'VALIDATION_ERROR', 'name, period_start and period_end are required');
      if (Date.parse(body.period_end) <= Date.parse(body.period_start)) return fail(422, 'VALIDATION_ERROR', 'period_end must be after period_start');
      const now = Date.now();
      const state: ReportState = {
        report: { id: uuid(`report:new:${body.name}:${now}`), name: body.name.trim(), period_start: body.period_start, period_end: body.period_end, group_by: body.group_by?.length ? body.group_by : ['api_key', 'model'], filters: body.filters ?? {}, currency: 'USD', status: 'pending', created_at: iso(now), updated_at: iso(now), generated_at: null, error_message: null },
        readyAt: now + 5_000,
      };
      reports.push(state);
      recordAudit({ eventType: 'billing_report.created', resourceType: 'billing_report', resourceId: state.report.id, resourceName: state.report.name, after: { period_start: body.period_start, period_end: body.period_end, group_by: state.report.group_by } });
      return created(materializeReport(state, now));
    },
  },
  {
    method: 'GET',
    path: '/billing/reports/:report_id',
    handle: (req) => {
      const s = reportById(req.params.report_id);
      return s ? ok(materializeReport(s)) : notFound('Billing report');
    },
  },
  {
    method: 'POST',
    path: '/billing/reports/:report_id/regenerate',
    handle: (req) => {
      const s = reportById(req.params.report_id);
      if (!s) return notFound('Billing report');
      const now = Date.now();
      s.report = { ...s.report, status: 'pending', error_message: null, line_items: [], totals: undefined, updated_at: iso(now) };
      s.readyAt = now + 5_000;
      return ok(materializeReport(s, now));
    },
  },
  {
    method: 'DELETE',
    path: '/billing/reports/:report_id',
    handle: (req) => {
      const idx = reports.findIndex((r) => r.report.id === req.params.report_id);
      if (idx < 0) return notFound('Billing report');
      reports.splice(idx, 1);
      return noContent();
    },
  },
  {
    method: 'GET',
    path: '/billing/reports/:report_id/export',
    handle: (req) => {
      const s = reportById(req.params.report_id);
      if (!s) return notFound('Billing report');
      const r = materializeReport(s);
      if (r.status !== 'ready') return fail(409, 'CONFLICT', 'Report is not ready yet');
      const keys = (r.group_by ?? ['api_key', 'model']) as string[];
      const rows = (r.line_items ?? []).map((li) => [...keys.map((k) => li.labels?.[k] ?? li.group[k]), li.price_plan_name, li.totals.request_count, li.totals.input_tokens, li.totals.output_tokens, li.totals.cached_tokens, li.totals.gpu_minutes ?? 0, li.cost_breakdown?.gpu_time_usd ?? 0, li.cost_breakdown?.input_tokens_usd ?? 0, li.cost_breakdown?.output_tokens_usd ?? 0, li.cost_breakdown?.cached_tokens_usd ?? 0, li.totals.cost_usd]);
      return csvResponse([[...keys, 'price_plan', 'requests', 'input_tokens', 'output_tokens', 'cached_tokens', 'gpu_minutes', 'gpu_time_usd', 'input_tokens_usd', 'output_tokens_usd', 'cached_tokens_usd', 'cost_usd'], ...rows], `${r.name.replace(/[^a-z0-9]+/gi, '-').toLowerCase()}.csv`);
    },
  },
]);
