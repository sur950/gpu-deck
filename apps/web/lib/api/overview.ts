/**
 * Dashboard overview API (`GET /metrics/overview`) and the mapping of the
 * activity rows it embeds. Also used by the recent-activity drill-downs.
 */

import { apiGet } from '@/lib/api/client';
import type { components } from '@/lib/api/generated/admin';
import type { AlertSummary, Overview, OverviewLive, RuleRunSummary, SwapSummary } from '@/types/overview';

type Schemas = components['schemas'];

export function mapOverviewLive(row: Schemas['OverviewLive']): OverviewLive {
  return {
    ts: row.ts,
    gpuUtilizationPctAvg: row.gpu_utilization_pct_avg ?? null,
    vramUsedBytes: row.vram_used_bytes ?? 0,
    vramTotalBytes: row.vram_total_bytes ?? 0,
    powerWatts: row.power_watts ?? null,
    activeSessions: row.active_sessions ?? 0,
    waitingSessions: row.waiting_sessions ?? 0,
    requestsPerMinute: row.requests_per_minute ?? null,
    generationTps: row.generation_tps ?? null,
    ttftMsP95: row.ttft_ms_p95 ?? null,
    errorRatePct: row.error_rate_pct ?? null,
  };
}

export function mapRuleRun(row: Schemas['RuleRun']): RuleRunSummary {
  return {
    id: row.id,
    ruleId: row.rule_id,
    ruleName: row.rule_name ?? 'Rule',
    ruleType: row.rule_type,
    evaluatedAt: row.evaluated_at,
    fired: row.fired,
    dryRun: row.dry_run,
    outcome: row.outcome,
    decision: row.decision ?? {},
    actions: (row.actions ?? []).map((a) => ({
      action: a.action,
      targetType: a.target_type,
      targetId: a.target_id ?? null,
      targetName: a.target_name ?? null,
      swapOperationId: a.swap_operation_id ?? null,
      status: a.status ?? null,
      message: a.message ?? null,
    })),
    errorMessage: row.error_message ?? null,
    durationMs: row.duration_ms ?? null,
  };
}

export function mapSwap(row: Schemas['SwapOperation']): SwapSummary {
  return {
    id: row.id,
    kind: row.kind,
    state: row.state,
    trigger: row.trigger,
    ruleName: row.rule_name ?? null,
    deploymentId: row.deployment_id ?? null,
    deploymentName: row.deployment_name ?? null,
    nodeId: row.node_id ?? null,
    steps: (row.steps ?? []).map((s) => ({
      name: s.name,
      state: s.state,
      startedAt: s.started_at ?? null,
      finishedAt: s.finished_at ?? null,
      error: s.error ?? null,
    })),
    currentStep: row.current_step,
    dryRun: row.dry_run ?? false,
    startedAt: row.started_at ?? null,
    finishedAt: row.finished_at ?? null,
    errorMessage: row.error_message ?? null,
    createdAt: row.created_at,
    plan: row.plan ?? {},
  };
}

export function mapAlert(row: Schemas['AlertEvent']): AlertSummary {
  return {
    id: row.id,
    severity: row.severity,
    eventType: row.event_type,
    title: row.title,
    message: row.message ?? null,
    resourceType: row.resource_type ?? null,
    resourceId: row.resource_id ?? null,
    resourceName: row.resource_name ?? null,
    status: row.status,
    occurrences: row.occurrences,
    firstSeenAt: row.first_seen_at,
    lastSeenAt: row.last_seen_at,
    deliveries: (row.deliveries ?? []).map((d) => ({ channelName: d.channel_name ?? 'Channel', status: d.status })),
  };
}

export function mapOverview(row: Schemas['Overview']): Overview {
  const c = row.counts ?? {};
  return {
    counts: {
      clusters: c.clusters ?? 0,
      nodes: c.nodes ?? 0,
      nodesOnline: c.nodes_online ?? 0,
      devices: c.devices ?? 0,
      devicesUnhealthy: c.devices_unhealthy ?? 0,
      models: c.models ?? 0,
      deployments: c.deployments ?? 0,
      deploymentsRunning: c.deployments_running ?? 0,
      instancesReady: c.instances_ready ?? 0,
      apiKeysActive: c.api_keys_active ?? 0,
      rulesEnabled: c.rules_enabled ?? 0,
      swapsInProgress: c.swaps_in_progress ?? 0,
      alertsOpen: c.alerts_open ?? 0,
    },
    live: mapOverviewLive(row.live),
    costTodayUsd: row.cost_today_usd ?? 0,
    costMonthUsd: row.cost_month_usd ?? 0,
    recentAlerts: (row.recent_alerts ?? []).map(mapAlert),
    recentSwaps: (row.recent_swaps ?? []).map(mapSwap),
    recentRuleRuns: (row.recent_rule_runs ?? []).map(mapRuleRun),
  };
}

export async function getOverview(): Promise<Overview> {
  return mapOverview(await apiGet<Schemas['Overview']>('/metrics/overview'));
}
