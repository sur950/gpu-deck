/**
 * Dashboard overview mock (`GET /metrics/overview`) plus read-only recent
 * activity lists the dashboard drills into (rule runs, swaps, alert events).
 * The activity routes are `fallback` stand-ins: the automation feature's
 * handler file owns rules / swaps / alerts and wins over them.
 */

import { DEPLOYMENTS, DEVICES, INSTANCES, NODES } from '@/lib/mocks/fixtures/fleet';
import { recentAlerts, recentRuleRuns, recentSwaps } from '@/lib/mocks/fixtures/activity';
import { overviewLive, usageTotal } from '@/lib/mocks/fixtures/timeseries';
import { asFallback, notFound, ok, paginate, registerMockHandlers } from '@/lib/mocks/registry';
import type { Schemas } from '@/lib/mocks/types';

export function buildOverview(now = Date.now()): Schemas['Overview'] {
  const startOfDay = new Date(now);
  startOfDay.setHours(0, 0, 0, 0);
  const startOfMonth = new Date(now);
  startOfMonth.setDate(1);
  startOfMonth.setHours(0, 0, 0, 0);
  const alerts = recentAlerts(now);
  const swaps = recentSwaps(now);
  return {
    counts: {
      clusters: 1,
      nodes: NODES.length,
      nodes_online: NODES.filter((n) => n.status === 'online').length,
      devices: DEVICES.length,
      devices_unhealthy: DEVICES.filter((d) => d.health === 'unhealthy' || d.health === 'degraded').length,
      models: 3,
      deployments: DEPLOYMENTS.length,
      deployments_running: DEPLOYMENTS.filter((d) => d.status === 'running').length,
      instances_ready: INSTANCES.filter((i) => i.state === 'ready').length,
      api_keys_active: 3,
      rules_enabled: 5,
      swaps_in_progress: swaps.filter((s) => !['completed', 'failed', 'rolled_back', 'cancelled'].includes(s.state)).length,
      alerts_open: alerts.filter((a) => a.status === 'open').length,
    },
    live: overviewLive(now),
    cost_today_usd: usageTotal('cost_usd', startOfDay.getTime(), now),
    cost_month_usd: usageTotal('cost_usd', startOfMonth.getTime(), now),
    recent_alerts: alerts.slice(0, 5),
    recent_swaps: swaps.slice(0, 5),
    recent_rule_runs: recentRuleRuns(now).slice(0, 6),
  };
}

registerMockHandlers('overview', [
  { method: 'GET', path: '/metrics/overview', handle: () => ok(buildOverview()) },
  // Read-only stand-ins: the automation feature (automation.ts) owns these.
  ...asFallback([
  {
    method: 'GET',
    path: '/rule-runs',
    handle: (req) => {
      const outcome = req.query.get('outcome');
      const ruleId = req.query.get('rule_id');
      const list = recentRuleRuns(Date.now()).filter((r) => (!outcome || r.outcome === outcome) && (!ruleId || r.rule_id === ruleId));
      return paginate(list, req.query);
    },
  },
  {
    method: 'GET',
    path: '/rule-runs/:run_id',
    handle: (req) => {
      const run = recentRuleRuns(Date.now()).find((r) => r.id === req.params.run_id);
      return run ? ok(run) : notFound('Rule run');
    },
  },
  {
    method: 'GET',
    path: '/swaps',
    handle: (req) => {
      const state = req.query.get('state');
      const deploymentId = req.query.get('deployment_id');
      const list = recentSwaps(Date.now()).filter((s) => (!state || s.state === state) && (!deploymentId || s.deployment_id === deploymentId));
      return paginate(list, req.query);
    },
  },
  {
    method: 'GET',
    path: '/swaps/:swap_id',
    handle: (req) => {
      const swap = recentSwaps(Date.now()).find((s) => s.id === req.params.swap_id);
      return swap ? ok(swap) : notFound('Swap');
    },
  },
  {
    method: 'GET',
    path: '/alert-events',
    handle: (req) => {
      const status = req.query.get('status');
      const severity = req.query.get('severity');
      const list = recentAlerts(Date.now()).filter((a) => (!status || a.status === status) && (!severity || a.severity === severity));
      return paginate(list, req.query);
    },
  },
  {
    method: 'GET',
    path: '/alert-events/:event_id',
    handle: (req) => {
      const alert = recentAlerts(Date.now()).find((a) => a.id === req.params.event_id);
      return alert ? ok(alert) : notFound('Alert');
    },
  },
  ]),
]);
