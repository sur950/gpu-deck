/**
 * Pure mapping from what the web app already receives (the `/metrics/overview`
 * snapshot and `/live/events` frames for alerts, rule runs and swaps) to the
 * desktop tray status and native notifications. A `Tracker` remembers what was
 * already announced, so a poll and a live frame for the same item notify once
 * and the first snapshot after start-up is a silent baseline.
 */

import type { components } from '@/lib/api/generated/admin';
import type { AlertSummary, Overview, RuleRunSummary, SwapSummary } from '@/types/overview';
import type { DesktopNotification, DesktopSeverity, DesktopTrayStatus } from '@/lib/desktop/host';

type LiveEvent = components['schemas']['LiveEvent'];

const TERMINAL_SWAP = new Set(['completed', 'failed', 'rolled_back', 'cancelled']);
const MAX_ITEMS = 5;

export function isTerminalSwap(state: string): boolean {
  return TERMINAL_SWAP.has(state);
}

function severityOf(value: unknown): DesktopSeverity {
  return value === 'critical' || value === 'warning' ? value : 'info';
}

function str(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value.trim() : null;
}

function swapLabel(s: Pick<SwapSummary, 'deploymentName' | 'kind' | 'state'>): string {
  const what = s.deploymentName ?? s.kind.replace(/_/g, ' ');
  return `${what} · ${s.state.replace(/_/g, ' ')}`;
}

function alertLabel(a: Pick<AlertSummary, 'severity' | 'title'>): string {
  const sev = a.severity === 'critical' ? 'Critical' : a.severity === 'warning' ? 'Warning' : 'Info';
  return `${sev}: ${a.title}`;
}

export function trayStatusFromOverview(overview: Overview | undefined, reachable = true): DesktopTrayStatus {
  if (!overview) {
    return { health: reachable ? 'unknown' : 'down', activeSwaps: [], openAlerts: [] };
  }
  const c = overview.counts;
  const openAlerts = overview.recentAlerts.filter((a) => a.status === 'open');
  const activeSwaps = overview.recentSwaps.filter((s) => !isTerminalSwap(s.state));
  let health: DesktopTrayStatus['health'] = 'ok';
  if (!reachable || (c.nodes > 0 && c.nodesOnline === 0)) health = 'down';
  else if (
    c.devicesUnhealthy > 0 ||
    c.nodesOnline < c.nodes ||
    openAlerts.some((a) => a.severity === 'critical')
  ) {
    health = 'degraded';
  }
  return {
    health,
    nodesOnline: c.nodesOnline,
    nodesTotal: c.nodes,
    devicesUnhealthy: c.devicesUnhealthy,
    deploymentsRunning: c.deploymentsRunning,
    swapsInProgress: Math.max(c.swapsInProgress, activeSwaps.length),
    alertsOpen: Math.max(c.alertsOpen, openAlerts.length),
    activeSwaps: activeSwaps.slice(0, MAX_ITEMS).map((s) => ({ label: swapLabel(s), path: `/swaps/${s.id}` })),
    openAlerts: openAlerts.slice(0, MAX_ITEMS).map((a) => ({ label: alertLabel(a), path: '/alerts' })),
  };
}

export function alertNotification(a: AlertSummary): DesktopNotification {
  return {
    title: alertLabel(a),
    body: [a.resourceName, a.message].filter(Boolean).join(' · ') || a.eventType,
    path: '/alerts',
    severity: a.severity,
  };
}

export type SwapNotice = Pick<SwapSummary, 'id' | 'state' | 'deploymentName' | 'errorMessage' | 'ruleName' | 'trigger'>;

export function swapNotification(s: SwapNotice): DesktopNotification {
  const what = s.deploymentName ?? 'a deployment';
  const failed = s.state === 'failed' || s.state === 'rolled_back';
  const verbs: Record<string, string> = {
    completed: 'Swap completed',
    failed: 'Swap failed',
    rolled_back: 'Swap rolled back',
    cancelled: 'Swap cancelled',
  };
  const verb = verbs[s.state] ?? `Swap ${s.state.replace(/_/g, ' ')}`;
  return {
    title: `${verb}: ${what}`,
    body: s.errorMessage ?? (s.ruleName ? `Triggered by rule ${s.ruleName}` : `Trigger: ${s.trigger}`),
    path: `/swaps/${s.id}`,
    severity: failed ? 'critical' : 'info',
  };
}

export function ruleRunNotification(r: RuleRunSummary): DesktopNotification {
  const actions = r.actions.map((a) => `${a.action.replace(/_/g, ' ')} ${a.targetName ?? ''}`.trim());
  return {
    title: r.outcome === 'error' ? `Rule failed: ${r.ruleName}` : `Rule fired: ${r.ruleName}`,
    body: r.errorMessage ?? (actions.join(', ') || 'No actions'),
    path: `/rules/${r.ruleId}`,
    severity: r.outcome === 'error' ? 'warning' : 'info',
  };
}

/** Notification for one `/live/events` frame (alert, swap, rule_run topics), or null. */
export function liveEventNotification(e: LiveEvent): { key: string; notification: DesktopNotification } | null {
  const d = e.data ?? {};
  if (e.event === 'alert') {
    // Acks and resolves are state updates, not new alerts — never notify for them.
    const status = str(d.status);
    if (status === 'acknowledged' || status === 'acked' || status === 'resolved') return null;
    const title = str(d.title) ?? (d.kind === 'test' ? 'Test alert' : null);
    if (!title) return null;
    const id = str(d.id) ?? str(d.event_id) ?? `${title}:${e.ts}`;
    const channel = str(d.channel_name);
    return {
      key: `alert:${id}`,
      notification: {
        title,
        body: str(d.message) ?? (channel ? `Sent to ${channel}` : (str(d.resource_name) ?? undefined)),
        path: '/alerts',
        severity: severityOf(d.severity),
      },
    };
  }
  if (e.event === 'swap') {
    const id = str(d.swap_id) ?? str(d.id);
    const state = str(d.state);
    if (!id || !state || !isTerminalSwap(state)) return null;
    return {
      key: `swap:${id}:${state}`,
      notification: swapNotification({
        id,
        state: state as SwapSummary['state'],
        deploymentName: str(d.deployment_name),
        errorMessage: str(d.error_message),
        ruleName: str(d.rule_name),
        trigger: (str(d.trigger) ?? 'manual') as SwapSummary['trigger'],
      }),
    };
  }
  if (e.event === 'rule_run') {
    const id = str(d.rule_run_id) ?? str(d.id);
    const outcome = str(d.outcome);
    if (!id || (outcome !== 'fired' && outcome !== 'error') || d.dry_run === true) return null;
    return {
      key: `rule_run:${id}`,
      notification: {
        title: outcome === 'error' ? `Rule failed: ${str(d.rule_name) ?? 'rule'}` : `Rule fired: ${str(d.rule_name) ?? 'rule'}`,
        body: str(d.message) ?? str(d.error_message) ?? undefined,
        path: str(d.rule_id) ? `/rules/${str(d.rule_id)}` : '/rules',
        severity: outcome === 'error' ? 'warning' : 'info',
      },
    };
  }
  return null;
}

/** Remembers announced items; the first `fromOverview` call only records a baseline. */
export class Tracker {
  private seen = new Set<string>();
  private baselined = false;

  /** Mark a key as announced; true when it is new. */
  claim(key: string): boolean {
    if (this.seen.has(key)) return false;
    this.seen.add(key);
    if (this.seen.size > 2000) {
      // Keep memory bounded on long-running windows.
      this.seen = new Set([...this.seen].slice(-1000));
    }
    return true;
  }

  fromOverview(overview: Overview): DesktopNotification[] {
    const out: DesktopNotification[] = [];
    const quiet = !this.baselined;
    this.baselined = true;
    for (const a of overview.recentAlerts) {
      if (a.status === 'open' && this.claim(`alert:${a.id}`) && !quiet) out.push(alertNotification(a));
    }
    for (const s of overview.recentSwaps) {
      if (isTerminalSwap(s.state) && this.claim(`swap:${s.id}:${s.state}`) && !quiet && !s.dryRun) {
        out.push(swapNotification(s));
      }
    }
    for (const r of overview.recentRuleRuns) {
      if ((r.outcome === 'fired' || r.outcome === 'error') && !r.dryRun && this.claim(`rule_run:${r.id}`) && !quiet) {
        out.push(ruleRunNotification(r));
      }
    }
    return out;
  }

  fromLive(e: LiveEvent): DesktopNotification | null {
    const hit = liveEventNotification(e);
    return hit && this.claim(hit.key) ? hit.notification : null;
  }
}

/** True for in-app paths the shell may navigate to. */
export function isAppPath(path: unknown): path is string {
  return typeof path === 'string' && path.startsWith('/') && !path.startsWith('//') && !path.includes('\\');
}
