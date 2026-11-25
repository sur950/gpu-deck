/**
 * UI types for the dashboard overview (`GET /metrics/overview`) and the
 * activity it surfaces (rule runs, swaps, alert events).
 */

import type { components } from '@/lib/api/generated/admin';

export interface OverviewCounts {
  clusters: number;
  nodes: number;
  nodesOnline: number;
  devices: number;
  devicesUnhealthy: number;
  models: number;
  deployments: number;
  deploymentsRunning: number;
  instancesReady: number;
  apiKeysActive: number;
  rulesEnabled: number;
  swapsInProgress: number;
  alertsOpen: number;
}

export interface OverviewLive {
  ts: string;
  gpuUtilizationPctAvg: number | null;
  vramUsedBytes: number;
  vramTotalBytes: number;
  powerWatts: number | null;
  activeSessions: number;
  waitingSessions: number;
  requestsPerMinute: number | null;
  generationTps: number | null;
  ttftMsP95: number | null;
  errorRatePct: number | null;
}

export type RuleType = components['schemas']['rule_type'];
export type RuleRunOutcome = components['schemas']['rule_run_outcome'];

export interface RuleRunAction {
  action: string;
  targetType: string;
  targetId: string | null;
  targetName: string | null;
  swapOperationId: string | null;
  status: string | null;
  message: string | null;
}

export interface RuleRunSummary {
  id: string;
  ruleId: string;
  ruleName: string;
  ruleType: RuleType;
  evaluatedAt: string;
  fired: boolean;
  dryRun: boolean;
  outcome: RuleRunOutcome;
  decision: Record<string, unknown>;
  actions: RuleRunAction[];
  errorMessage: string | null;
  durationMs: number | null;
}

export type SwapKind = components['schemas']['swap_kind'];
export type SwapState = components['schemas']['swap_state'];
export type SwapTrigger = components['schemas']['swap_trigger'];

export interface SwapStep {
  name: string;
  state: components['schemas']['SwapStep']['state'];
  startedAt: string | null;
  finishedAt: string | null;
  error: string | null;
}

export interface SwapSummary {
  id: string;
  kind: SwapKind;
  state: SwapState;
  trigger: SwapTrigger;
  ruleName: string | null;
  deploymentId: string | null;
  deploymentName: string | null;
  nodeId: string | null;
  steps: SwapStep[];
  currentStep: number;
  dryRun: boolean;
  startedAt: string | null;
  finishedAt: string | null;
  errorMessage: string | null;
  createdAt: string;
  plan: Record<string, unknown>;
}

export type AlertSeverity = components['schemas']['alert_severity'];
export type AlertStatus = components['schemas']['alert_event_status'];

export interface AlertSummary {
  id: string;
  severity: AlertSeverity;
  eventType: string;
  title: string;
  message: string | null;
  resourceType: string | null;
  resourceId: string | null;
  resourceName: string | null;
  status: AlertStatus;
  occurrences: number;
  firstSeenAt: string;
  lastSeenAt: string;
  deliveries: { channelName: string; status: string }[];
}

export interface Overview {
  counts: OverviewCounts;
  live: OverviewLive;
  recentAlerts: AlertSummary[];
  recentSwaps: SwapSummary[];
  recentRuleRuns: RuleRunSummary[];
}
