/**
 * Automation API layer — rules, rule runs, layouts, swaps, alert channels and
 * alert events, plus the read-only reference lists the builders need
 * (deployments, models, nodes, devices, clusters).
 *
 * Backend prefix: /api/v1 (prepended by lib/api/client). Responses are
 * snake_case; this layer maps them to the camelCase types exported here.
 * `params` / `target` documents are kept exactly as the contract defines
 * them (snake_case) because they are validated against
 * libs/contracts/rules/*.schema.json and rendered from those schemas.
 */

import { apiDelete, apiGet, apiPatch, apiPost, apiRequest, toQuery } from '@/lib/api/client';
import type { components } from '@/lib/api/generated/admin';

type S = components['schemas'];

// ─── Paginated GET (keeps the envelope's `meta`) ──────────────────────────────

/** A page of mapped items plus the contract's ListMeta. */
export interface Paged<T> {
  items: T[];
  total: number;
  page: number;
  pageSize: number;
  hasMore: boolean;
}

/**
 * `lib/api/client` unwraps `data` and drops `meta`, so list endpoints go
 * through this sibling that keeps the pagination block. Same credentials,
 * same error classes. Shared by the usage and security API layers too.
 */
export async function pagedGet<TRaw, T>(path: string, map: (raw: TRaw) => T): Promise<Paged<T>> {
  const { data, meta } = await apiRequest<TRaw[] | null>('GET', path);
  const items = (Array.isArray(data) ? data : []).map(map);
  return {
    items,
    total: meta?.total ?? items.length,
    page: meta?.page ?? 1,
    pageSize: meta?.page_size ?? items.length,
    hasMore: meta?.has_more ?? false,
  };
}

// ─── Rules ────────────────────────────────────────────────────────────────────

export type RuleType = S['rule_type'];
export type RuleRunOutcome = S['rule_run_outcome'];
export type ModelPriority = S['model_priority'];
export type ModelType = S['model_type'];
export type EngineName = S['engine_name'];

export const RULE_TYPES: RuleType[] = [
  'idle_unload',
  'time_schedule',
  'low_traffic_shrink',
  'high_load_grow',
  'make_room_by_priority',
  'auto_heal',
];

/** libs/contracts/rules/target-selector.schema.json — kept snake_case (contract document). */
export interface TargetSelector {
  deployment_ids?: string[];
  model_ids?: string[];
  cluster_ids?: string[];
  node_ids?: string[];
  model_types?: ModelType[];
  priorities?: ModelPriority[];
  engines?: EngineName[];
  labels?: Record<string, string>;
  name_glob?: string;
  exclude_deployment_ids?: string[];
}

export type RuleParams = Record<string, unknown>;

export interface RuleRunSummary {
  id: string;
  outcome: RuleRunOutcome;
  fired: boolean;
  dryRun: boolean;
  evaluatedAt: string;
}

export interface Rule {
  id: string;
  name: string;
  description: string | null;
  type: RuleType;
  params: RuleParams;
  target: TargetSelector;
  enabled: boolean;
  dryRun: boolean;
  cooldownSeconds: number;
  evaluationIntervalSeconds: number;
  priority: number;
  createdAt: string;
  updatedAt: string | null;
  lastEvaluatedAt: string | null;
  /** When the evaluator next looks at the rule (server-computed; next fire time for schedules). */
  nextEvaluationAt: string | null;
  lastFiredAt: string | null;
  fireCount: number;
  lastRun: RuleRunSummary | null;
}

export interface RuleInput {
  name: string;
  description?: string | null;
  type: RuleType;
  params: RuleParams;
  target?: TargetSelector;
  enabled: boolean;
  dry_run: boolean;
  cooldown_seconds: number;
  evaluation_interval_seconds: number;
  priority: number;
}

export interface RuleRunAction {
  action: string;
  targetType: string;
  targetId: string | null;
  targetName: string | null;
  status: string | null;
  message: string | null;
  swapOperationId: string | null;
}

export interface RuleRun {
  id: string;
  ruleId: string;
  ruleName: string | null;
  ruleType: RuleType;
  outcome: RuleRunOutcome;
  fired: boolean;
  dryRun: boolean;
  evaluatedAt: string;
  durationMs: number | null;
  decision: Record<string, unknown>;
  actions: RuleRunAction[];
  errorMessage: string | null;
  createdAt: string | null;
}

export interface RuleListParams {
  page?: number;
  pageSize?: number;
  search?: string;
  sort?: string;
  type?: RuleType | '';
  enabled?: boolean | null;
}

export interface RuleRunListParams {
  page?: number;
  pageSize?: number;
  outcome?: RuleRunOutcome | '';
  fired?: boolean | null;
  from?: string;
  to?: string;
  ruleType?: RuleType | '';
  deploymentId?: string;
}

function mapRunSummary(r: S['RuleRunSummary'] | null | undefined): RuleRunSummary | null {
  if (!r) return null;
  return { id: r.id, outcome: r.outcome, fired: r.fired, dryRun: r.dry_run, evaluatedAt: r.evaluated_at };
}

export function mapRule(r: S['Rule']): Rule {
  return {
    id: r.id,
    name: r.name,
    description: r.description ?? null,
    type: r.type,
    params: (r.params ?? {}) as RuleParams,
    target: (r.target ?? {}) as TargetSelector,
    enabled: r.enabled ?? true,
    dryRun: r.dry_run ?? false,
    cooldownSeconds: r.cooldown_seconds ?? 300,
    evaluationIntervalSeconds: r.evaluation_interval_seconds ?? 30,
    priority: r.priority ?? 100,
    createdAt: r.created_at,
    updatedAt: r.updated_at ?? null,
    lastEvaluatedAt: r.last_evaluated_at ?? null,
    nextEvaluationAt: r.next_evaluation_at ?? null,
    lastFiredAt: r.last_fired_at ?? null,
    fireCount: r.fire_count ?? 0,
    lastRun: mapRunSummary(r.last_run),
  };
}

function mapRunAction(a: S['RuleRunAction']): RuleRunAction {
  return {
    action: a.action,
    targetType: a.target_type,
    targetId: a.target_id ?? null,
    targetName: a.target_name ?? null,
    status: a.status ?? null,
    message: a.message ?? null,
    swapOperationId: a.swap_operation_id ?? null,
  };
}

export function mapRuleRun(r: S['RuleRun']): RuleRun {
  return {
    id: r.id,
    ruleId: r.rule_id,
    ruleName: r.rule_name ?? null,
    ruleType: r.rule_type,
    outcome: r.outcome,
    fired: r.fired,
    dryRun: r.dry_run,
    evaluatedAt: r.evaluated_at,
    durationMs: r.duration_ms ?? null,
    decision: r.decision ?? {},
    actions: (r.actions ?? []).map(mapRunAction),
    errorMessage: r.error_message ?? null,
    createdAt: r.created_at ?? null,
  };
}

export function listRules(params: RuleListParams = {}): Promise<Paged<Rule>> {
  return pagedGet<S['Rule'], Rule>(
    `/rules${toQuery({
      page: params.page,
      page_size: params.pageSize,
      search: params.search,
      sort: params.sort,
      type: params.type || undefined,
      enabled: params.enabled === null || params.enabled === undefined ? undefined : params.enabled,
    })}`,
    mapRule,
  );
}

export async function getRule(id: string): Promise<Rule> {
  return mapRule(await apiGet<S['Rule']>(`/rules/${id}`));
}

export async function createRule(body: RuleInput): Promise<Rule> {
  return mapRule(await apiPost<S['Rule']>('/rules', body));
}

export async function updateRule(id: string, body: Partial<RuleInput>): Promise<Rule> {
  return mapRule(await apiPatch<S['Rule']>(`/rules/${id}`, body));
}

export function deleteRule(id: string): Promise<void> {
  return apiDelete<void>(`/rules/${id}`);
}

export async function enableRule(id: string): Promise<Rule> {
  return mapRule(await apiPost<S['Rule']>(`/rules/${id}/enable`));
}

export async function disableRule(id: string): Promise<Rule> {
  return mapRule(await apiPost<S['Rule']>(`/rules/${id}/disable`));
}

export async function dryRunRule(id: string): Promise<RuleRun> {
  return mapRuleRun(await apiPost<S['RuleRun']>(`/rules/${id}/dry-run`));
}

export async function runRuleNow(id: string): Promise<RuleRun> {
  return mapRuleRun(await apiPost<S['RuleRun']>(`/rules/${id}/run`));
}

function runQuery(params: RuleRunListParams): string {
  return toQuery({
    page: params.page,
    page_size: params.pageSize,
    outcome: params.outcome || undefined,
    fired: params.fired === null || params.fired === undefined ? undefined : params.fired,
    from: params.from,
    to: params.to,
    rule_type: params.ruleType || undefined,
    deployment_id: params.deploymentId,
  });
}

export function listRuleRuns(ruleId: string, params: RuleRunListParams = {}): Promise<Paged<RuleRun>> {
  return pagedGet<S['RuleRun'], RuleRun>(`/rules/${ruleId}/runs${runQuery(params)}`, mapRuleRun);
}

export function listAllRuleRuns(params: RuleRunListParams = {}): Promise<Paged<RuleRun>> {
  return pagedGet<S['RuleRun'], RuleRun>(`/rule-runs${runQuery(params)}`, mapRuleRun);
}

export async function getRuleRun(id: string): Promise<RuleRun> {
  return mapRuleRun(await apiGet<S['RuleRun']>(`/rule-runs/${id}`));
}

/** JSON Schema documents served verbatim from libs/contracts/rules. */
export interface RuleSchemas {
  params: Record<string, JsonSchema>;
  targetSelector: JsonSchema;
  rule: JsonSchema;
  layout: JsonSchema;
}

/** The subset of JSON Schema the rule builder renders. */
export interface JsonSchema {
  title?: string;
  description?: string;
  type?: string | string[];
  properties?: Record<string, JsonSchema>;
  required?: string[];
  default?: unknown;
  minimum?: number;
  maximum?: number;
  enum?: unknown[];
  const?: unknown;
  pattern?: string;
  format?: string;
  items?: JsonSchema;
  minItems?: number;
  uniqueItems?: boolean;
  additionalProperties?: boolean | JsonSchema;
  anyOf?: JsonSchema[];
  allOf?: JsonSchema[];
  if?: JsonSchema;
  then?: JsonSchema;
}

export async function getRuleSchemas(): Promise<RuleSchemas> {
  const raw = await apiGet<S['RuleSchemas']>('/rules/schemas');
  return {
    params: (raw.params ?? {}) as Record<string, JsonSchema>,
    targetSelector: (raw.target_selector ?? {}) as JsonSchema,
    rule: (raw.rule ?? {}) as JsonSchema,
    layout: (raw.layout ?? {}) as JsonSchema,
  };
}

// ─── Layouts ──────────────────────────────────────────────────────────────────

export interface LayoutItem {
  id: string;
  deploymentId: string;
  deploymentName: string | null;
  nodeId: string | null;
  nodeName: string | null;
  deviceIds: string[];
  replicas: number;
  sliceVramBytes: number | null;
  sliceComputePct: number | null;
  trafficWeight: number;
  position: number;
}

export interface Layout {
  id: string;
  name: string;
  description: string | null;
  unlistedPolicy: 'keep' | 'unload';
  isActive: boolean;
  appliedAt: string | null;
  items: LayoutItem[];
  usedByRuleIds: string[];
  createdAt: string;
  updatedAt: string | null;
}

/** Mirrors libs/contracts/rules/layout.schema.json items[] (request shape, snake_case). */
export type LayoutItemInput = S['LayoutItemInput'];

export interface LayoutInput {
  name: string;
  description?: string | null;
  unlisted_policy?: 'keep' | 'unload';
  items: LayoutItemInput[];
}

export interface LayoutDiffEntry {
  action: S['LayoutDiffEntry']['action'];
  deploymentId: string;
  deploymentName: string | null;
  from: Record<string, unknown> | null;
  to: Record<string, unknown> | null;
  needsPrefetchBytes: number;
  protected: boolean;
  placementFeasible: boolean | null;
  placementReasons: string[];
}

export interface LayoutDiff {
  layoutId: string;
  feasible: boolean;
  estimatedSeconds: number | null;
  warnings: string[];
  entries: LayoutDiffEntry[];
}

export interface LayoutApplyResult {
  layoutId: string;
  swapOperationIds: string[];
  commandIds: string[];
  diff: LayoutDiff;
}

function mapLayoutItem(i: S['LayoutItem']): LayoutItem {
  return {
    id: i.id,
    deploymentId: i.deployment_id,
    deploymentName: i.deployment_name ?? null,
    nodeId: i.node_id ?? null,
    nodeName: i.node_name ?? null,
    deviceIds: i.device_ids ?? [],
    replicas: i.replicas,
    sliceVramBytes: i.slice_vram_bytes ?? null,
    sliceComputePct: i.slice_compute_pct ?? null,
    trafficWeight: i.traffic_weight,
    position: i.position,
  };
}

export function mapLayout(l: S['Layout']): Layout {
  return {
    id: l.id,
    name: l.name,
    description: l.description ?? null,
    unlistedPolicy: l.unlisted_policy ?? 'keep',
    isActive: l.is_active,
    appliedAt: l.applied_at ?? null,
    items: (l.items ?? []).map(mapLayoutItem).sort((a, b) => a.position - b.position),
    usedByRuleIds: l.used_by_rule_ids ?? [],
    createdAt: l.created_at,
    updatedAt: l.updated_at ?? null,
  };
}

function mapDiff(d: S['LayoutDiff']): LayoutDiff {
  return {
    layoutId: d.layout_id,
    feasible: d.feasible,
    estimatedSeconds: d.estimated_seconds ?? null,
    warnings: d.warnings ?? [],
    entries: (d.entries ?? []).map((e) => ({
      action: e.action,
      deploymentId: e.deployment_id,
      deploymentName: e.deployment_name ?? null,
      from: e.from ?? null,
      to: e.to ?? null,
      needsPrefetchBytes: e.needs_prefetch_bytes ?? 0,
      protected: e.protected ?? false,
      placementFeasible: e.placement ? e.placement.feasible : null,
      placementReasons: e.placement?.rejected?.map((r) => r.reason ?? '').filter(Boolean) ?? [],
    })),
  };
}

export function listLayouts(params: { page?: number; pageSize?: number; search?: string; sort?: string } = {}): Promise<Paged<Layout>> {
  return pagedGet<S['Layout'], Layout>(
    `/layouts${toQuery({ page: params.page, page_size: params.pageSize, search: params.search, sort: params.sort })}`,
    mapLayout,
  );
}

export async function getLayout(id: string): Promise<Layout> {
  return mapLayout(await apiGet<S['Layout']>(`/layouts/${id}`));
}

export async function createLayout(body: LayoutInput): Promise<Layout> {
  return mapLayout(await apiPost<S['Layout']>('/layouts', body));
}

export async function updateLayout(id: string, body: Partial<LayoutInput>): Promise<Layout> {
  return mapLayout(await apiPatch<S['Layout']>(`/layouts/${id}`, body));
}

export function deleteLayout(id: string): Promise<void> {
  return apiDelete<void>(`/layouts/${id}`);
}

export async function previewLayout(id: string): Promise<LayoutDiff> {
  return mapDiff(await apiPost<S['LayoutDiff']>(`/layouts/${id}/preview`));
}

export async function applyLayout(id: string, body: { drain_timeout_seconds: number; prefetch_only: boolean }): Promise<LayoutApplyResult> {
  const r = await apiPost<S['LayoutApplyResult']>(`/layouts/${id}/apply`, body);
  return { layoutId: r.layout_id, swapOperationIds: r.swap_operation_ids ?? [], commandIds: r.command_ids ?? [], diff: mapDiff(r.diff) };
}

export async function captureLayout(body: { name: string; description?: string | null; cluster_id?: string | null }): Promise<Layout> {
  return mapLayout(await apiPost<S['Layout']>('/layouts/capture', body));
}

// ─── Swaps ────────────────────────────────────────────────────────────────────

export type SwapKind = S['swap_kind'];
export type SwapState = S['swap_state'];
export type SwapTrigger = S['swap_trigger'];
export type SwapStepState = S['SwapStep']['state'];

export const SWAP_TERMINAL_STATES: SwapState[] = ['completed', 'failed', 'rolled_back', 'cancelled'];
export const SWAP_STEP_ORDER = ['plan', 'route_off', 'drain', 'unload', 'load', 'health', 'route_on'] as const;

export interface SwapStep {
  name: string;
  state: SwapStepState;
  startedAt: string | null;
  finishedAt: string | null;
  commandId: string | null;
  error: string | null;
  details: Record<string, unknown> | null;
}

export interface SwapOperation {
  id: string;
  kind: SwapKind;
  state: SwapState;
  trigger: SwapTrigger;
  deploymentId: string | null;
  deploymentName: string | null;
  fromInstanceId: string | null;
  toInstanceId: string | null;
  nodeId: string | null;
  layoutId: string | null;
  ruleRunId: string | null;
  ruleName: string | null;
  dryRun: boolean;
  drainTimeoutSeconds: number | null;
  currentStep: number;
  steps: SwapStep[];
  plan: Record<string, unknown>;
  rollback: Record<string, unknown> | null;
  errorMessage: string | null;
  createdAt: string;
  startedAt: string | null;
  finishedAt: string | null;
  updatedAt: string | null;
}

export interface SwapListParams {
  page?: number;
  pageSize?: number;
  state?: SwapState | '';
  kind?: SwapKind | '';
  trigger?: SwapTrigger | '';
  deploymentId?: string;
  nodeId?: string;
  activeOnly?: boolean;
  from?: string;
  to?: string;
  sort?: string;
}

export type SwapCreateInput = S['SwapCreate'];

export function mapSwap(s: S['SwapOperation']): SwapOperation {
  return {
    id: s.id,
    kind: s.kind,
    state: s.state,
    trigger: s.trigger,
    deploymentId: s.deployment_id ?? null,
    deploymentName: s.deployment_name ?? null,
    fromInstanceId: s.from_instance_id ?? null,
    toInstanceId: s.to_instance_id ?? null,
    nodeId: s.node_id ?? null,
    layoutId: s.layout_id ?? null,
    ruleRunId: s.rule_run_id ?? null,
    ruleName: s.rule_name ?? null,
    dryRun: s.dry_run ?? false,
    drainTimeoutSeconds: s.drain_timeout_seconds ?? null,
    currentStep: s.current_step ?? 0,
    steps: (s.steps ?? []).map((st) => ({
      name: st.name,
      state: st.state,
      startedAt: st.started_at ?? null,
      finishedAt: st.finished_at ?? null,
      commandId: st.command_id ?? null,
      error: st.error ?? null,
      details: st.details ?? null,
    })),
    plan: s.plan ?? {},
    rollback: s.rollback ?? null,
    errorMessage: s.error_message ?? null,
    createdAt: s.created_at,
    startedAt: s.started_at ?? null,
    finishedAt: s.finished_at ?? null,
    updatedAt: s.updated_at ?? null,
  };
}

export function listSwaps(params: SwapListParams = {}): Promise<Paged<SwapOperation>> {
  return pagedGet<S['SwapOperation'], SwapOperation>(
    `/swaps${toQuery({
      page: params.page,
      page_size: params.pageSize,
      state: params.state || undefined,
      kind: params.kind || undefined,
      trigger: params.trigger || undefined,
      deployment_id: params.deploymentId,
      node_id: params.nodeId,
      active_only: params.activeOnly ? true : undefined,
      from: params.from,
      to: params.to,
      sort: params.sort,
    })}`,
    mapSwap,
  );
}

export async function getSwap(id: string): Promise<SwapOperation> {
  return mapSwap(await apiGet<S['SwapOperation']>(`/swaps/${id}`));
}

export async function createSwap(body: SwapCreateInput): Promise<SwapOperation> {
  return mapSwap(await apiPost<S['SwapOperation']>('/swaps', body));
}

export async function cancelSwap(id: string): Promise<SwapOperation> {
  return mapSwap(await apiPost<S['SwapOperation']>(`/swaps/${id}/cancel`));
}

export async function retrySwap(id: string): Promise<SwapOperation> {
  return mapSwap(await apiPost<S['SwapOperation']>(`/swaps/${id}/retry`));
}

// ─── Alerts ───────────────────────────────────────────────────────────────────

export type AlertChannelType = S['alert_channel_type'];
export type AlertSeverity = S['alert_severity'];
export type AlertEventStatus = S['alert_event_status'];
export type AlertDeliveryStatus = S['alert_delivery_status'];

export interface AlertChannel {
  id: string;
  name: string;
  type: AlertChannelType;
  config: Record<string, unknown>;
  credentialId: string | null;
  enabled: boolean;
  eventFilter: string[];
  minSeverity: AlertSeverity;
  lastTestAt: string | null;
  lastTestOk: boolean | null;
  lastError: string | null;
  createdAt: string | null;
  updatedAt: string | null;
}

export type AlertChannelInput = S['AlertChannelCreate'];
export type AlertChannelUpdateInput = S['AlertChannelUpdate'];

export interface AlertTestResult {
  ok: boolean;
  latencyMs: number | null;
  responseCode: number | null;
  error: string | null;
  sentAt: string;
}

export interface AlertDelivery {
  id: string;
  channelId: string;
  channelName: string | null;
  status: AlertDeliveryStatus;
  attempts: number;
  responseCode: number | null;
  errorMessage: string | null;
  sentAt: string | null;
}

export interface AlertEvent {
  id: string;
  eventType: string;
  severity: AlertSeverity;
  status: AlertEventStatus;
  title: string;
  message: string | null;
  resourceType: string | null;
  resourceId: string | null;
  resourceName: string | null;
  occurrences: number;
  firstSeenAt: string;
  lastSeenAt: string;
  acknowledgedAt: string | null;
  resolvedAt: string | null;
  dedupeKey: string | null;
  payload: Record<string, unknown>;
  deliveries: AlertDelivery[];
  createdAt: string | null;
}

export interface AlertEventListParams {
  page?: number;
  pageSize?: number;
  severity?: AlertSeverity | '';
  status?: AlertEventStatus | '';
  eventType?: string;
  resourceType?: string;
  resourceId?: string;
  from?: string;
  to?: string;
  sort?: string;
}

export function mapAlertChannel(c: S['AlertChannel']): AlertChannel {
  return {
    id: c.id,
    name: c.name,
    type: c.type,
    config: c.config ?? {},
    credentialId: c.credential_id ?? null,
    enabled: c.enabled,
    eventFilter: c.event_filter ?? [],
    minSeverity: c.min_severity,
    lastTestAt: c.last_test_at ?? null,
    lastTestOk: c.last_test_ok ?? null,
    lastError: c.last_error ?? null,
    createdAt: c.created_at ?? null,
    updatedAt: c.updated_at ?? null,
  };
}

export function mapAlertEvent(e: S['AlertEvent']): AlertEvent {
  return {
    id: e.id,
    eventType: e.event_type,
    severity: e.severity,
    status: e.status,
    title: e.title,
    message: e.message ?? null,
    resourceType: e.resource_type ?? null,
    resourceId: e.resource_id ?? null,
    resourceName: e.resource_name ?? null,
    occurrences: e.occurrences ?? 1,
    firstSeenAt: e.first_seen_at,
    lastSeenAt: e.last_seen_at,
    acknowledgedAt: e.acknowledged_at ?? null,
    resolvedAt: e.resolved_at ?? null,
    dedupeKey: e.dedupe_key ?? null,
    payload: e.payload ?? {},
    deliveries: (e.deliveries ?? []).map((d) => ({
      id: d.id,
      channelId: d.channel_id,
      channelName: d.channel_name ?? null,
      status: d.status,
      attempts: d.attempts ?? 0,
      responseCode: d.response_code ?? null,
      errorMessage: d.error_message ?? null,
      sentAt: d.sent_at ?? null,
    })),
    createdAt: e.created_at ?? null,
  };
}

export function listAlertChannels(params: { page?: number; pageSize?: number; search?: string; type?: AlertChannelType | '' } = {}): Promise<Paged<AlertChannel>> {
  return pagedGet<S['AlertChannel'], AlertChannel>(
    `/alert-channels${toQuery({ page: params.page, page_size: params.pageSize, search: params.search, type: params.type || undefined })}`,
    mapAlertChannel,
  );
}

export async function getAlertChannel(id: string): Promise<AlertChannel> {
  return mapAlertChannel(await apiGet<S['AlertChannel']>(`/alert-channels/${id}`));
}

export async function createAlertChannel(body: AlertChannelInput): Promise<AlertChannel> {
  return mapAlertChannel(await apiPost<S['AlertChannel']>('/alert-channels', body));
}

export async function updateAlertChannel(id: string, body: AlertChannelUpdateInput): Promise<AlertChannel> {
  return mapAlertChannel(await apiPatch<S['AlertChannel']>(`/alert-channels/${id}`, body));
}

export function deleteAlertChannel(id: string): Promise<void> {
  return apiDelete<void>(`/alert-channels/${id}`);
}

export async function testAlertChannel(id: string): Promise<AlertTestResult> {
  const r = await apiPost<S['AlertTestResult']>(`/alert-channels/${id}/test`);
  return { ok: r.ok, latencyMs: r.latency_ms ?? null, responseCode: r.response_code ?? null, error: r.error ?? null, sentAt: r.sent_at };
}

export function listAlertEvents(params: AlertEventListParams = {}): Promise<Paged<AlertEvent>> {
  return pagedGet<S['AlertEvent'], AlertEvent>(
    `/alert-events${toQuery({
      page: params.page,
      page_size: params.pageSize,
      severity: params.severity || undefined,
      status: params.status || undefined,
      event_type: params.eventType,
      resource_type: params.resourceType,
      resource_id: params.resourceId,
      from: params.from,
      to: params.to,
      sort: params.sort,
    })}`,
    mapAlertEvent,
  );
}

export async function getAlertEvent(id: string): Promise<AlertEvent> {
  return mapAlertEvent(await apiGet<S['AlertEvent']>(`/alert-events/${id}`));
}

export async function acknowledgeAlertEvent(id: string): Promise<AlertEvent> {
  return mapAlertEvent(await apiPost<S['AlertEvent']>(`/alert-events/${id}/acknowledge`));
}

export async function resolveAlertEvent(id: string): Promise<AlertEvent> {
  return mapAlertEvent(await apiPost<S['AlertEvent']>(`/alert-events/${id}/resolve`));
}

export async function resendAlertEvent(id: string, channelId?: string): Promise<AlertEvent> {
  return mapAlertEvent(await apiPost<S['AlertEvent']>(`/alert-events/${id}/resend${toQuery({ channel_id: channelId })}`));
}

// ─── Reference data (read-only, used by the fleet / models screens) ──────────

export interface RefInstance {
  id: string;
  deploymentId: string;
  nodeId: string;
  nodeName: string | null;
  deviceIds: string[];
  state: S['instance_state'];
  health: S['instance_health'];
  trafficWeight: number;
  readyAt: string | null;
  lastRequestAt: string | null;
  /** VRAM the instance holds (resources.vram_bytes), when reported. */
  vramBytes: number | null;
  live: {
    activeSessions: number | null;
    waitingSessions: number | null;
    requestsPerMinute: number | null;
    kvCacheUsagePct: number | null;
    errorRatePct: number | null;
  };
}

export interface RefDeployment {
  id: string;
  name: string;
  modelId: string;
  modelName: string | null;
  modelType: ModelType | null;
  engineName: EngineName | null;
  status: S['deployment_status'];
  replicas: number;
  minReplicas: number;
  maxReplicas: number;
  readyReplicas: number;
  gpuCount: number;
  priority: ModelPriority;
  clusterId: string | null;
  sliceVramBytes: number | null;
  lastRequestAt: string | null;
  instances: RefInstance[];
}

export interface RefModel {
  id: string;
  name: string;
  displayName: string | null;
  type: ModelType;
  priority: ModelPriority;
}

export interface RefNode {
  id: string;
  name: string;
  clusterId: string;
  status: S['node_status'];
  labels: Record<string, string>;
  deviceCount: number;
}

export interface RefCluster {
  id: string;
  name: string;
  type: S['cluster_type'];
  status: S['cluster_status'];
  nodes: number;
}

export interface RefDevice {
  id: string;
  name: string;
  index: number;
  nodeId: string;
  nodeName: string | null;
  vendor: S['device_vendor'];
  health: S['device_health'];
  vramTotalBytes: number;
  vramReservedBytes: number;
  memoryUsedBytes: number | null;
  utilizationPct: number | null;
  instanceIds: string[];
  isUnifiedMemory: boolean;
}

function mapInstance(i: S['Instance']): RefInstance {
  return {
    id: i.id,
    deploymentId: i.deployment_id,
    nodeId: i.node_id,
    nodeName: i.node_name ?? null,
    deviceIds: i.device_ids ?? [],
    state: i.state,
    health: i.health,
    trafficWeight: i.traffic_weight,
    readyAt: i.ready_at ?? null,
    lastRequestAt: i.last_request_at ?? null,
    vramBytes: typeof i.resources?.vram_bytes === 'number' ? i.resources.vram_bytes : null,
    live: {
      activeSessions: i.live?.active_sessions ?? null,
      waitingSessions: i.live?.waiting_sessions ?? null,
      requestsPerMinute: i.live?.requests_per_minute ?? null,
      kvCacheUsagePct: i.live?.kv_cache_usage_pct ?? null,
      errorRatePct: i.live?.error_rate_pct ?? null,
    },
  };
}

export function mapRefDeployment(d: S['Deployment']): RefDeployment {
  return {
    id: d.id,
    name: d.name,
    modelId: d.model_id ?? '',
    modelName: d.model_name ?? null,
    modelType: d.model_type ?? null,
    engineName: d.engine_name ?? null,
    status: d.status,
    replicas: d.replicas,
    minReplicas: d.min_replicas,
    maxReplicas: d.max_replicas,
    readyReplicas: d.ready_replicas ?? 0,
    gpuCount: d.gpu_count,
    priority: d.effective_priority ?? d.priority_override ?? 'normal',
    clusterId: d.cluster_id ?? null,
    sliceVramBytes: d.slice_vram_bytes ?? null,
    lastRequestAt: d.last_request_at ?? null,
    instances: (d.instances ?? []).map(mapInstance),
  };
}

export async function listRefDeployments(): Promise<RefDeployment[]> {
  const page = await pagedGet<S['Deployment'], RefDeployment>(`/deployments${toQuery({ page_size: 500, origin: 'managed' })}`, mapRefDeployment);
  return page.items;
}

export async function listRefModels(): Promise<RefModel[]> {
  const page = await pagedGet<S['Model'], RefModel>(`/models${toQuery({ page_size: 500 })}`, (m) => ({
    id: m.id,
    name: m.name,
    displayName: m.display_name ?? null,
    type: m.type,
    priority: m.priority,
  }));
  return page.items;
}

export async function listRefNodes(): Promise<RefNode[]> {
  const page = await pagedGet<S['Node'], RefNode>(`/nodes${toQuery({ page_size: 500 })}`, (n) => ({
    id: n.id,
    name: n.name,
    clusterId: n.cluster_id,
    status: n.status,
    labels: n.labels ?? {},
    deviceCount: n.device_count ?? 0,
  }));
  return page.items;
}

export async function listRefClusters(): Promise<RefCluster[]> {
  const page = await pagedGet<S['Cluster'], RefCluster>(`/clusters${toQuery({ page_size: 500 })}`, (c) => ({
    id: c.id,
    name: c.name,
    type: c.type,
    status: c.status,
    nodes: c.counts?.nodes ?? 0,
  }));
  return page.items;
}

export async function listRefDevices(): Promise<RefDevice[]> {
  const page = await pagedGet<S['Device'], RefDevice>(`/devices${toQuery({ page_size: 500 })}`, (d) => ({
    id: d.id,
    name: d.name,
    index: d.index,
    nodeId: d.node_id,
    nodeName: d.node_name ?? null,
    vendor: d.vendor,
    health: d.health,
    vramTotalBytes: d.vram_total_bytes,
    vramReservedBytes: d.vram_reserved_bytes ?? 0,
    memoryUsedBytes: d.live?.memory_used_bytes ?? null,
    utilizationPct: d.live?.utilization_pct ?? null,
    instanceIds: d.instance_ids ?? [],
    isUnifiedMemory: d.is_unified_memory ?? false,
  }));
  return page.items;
}

export async function listRefInstances(): Promise<RefInstance[]> {
  const page = await pagedGet<S['Instance'], RefInstance>(`/instances${toQuery({ page_size: 500 })}`, mapInstance);
  return page.items;
}
