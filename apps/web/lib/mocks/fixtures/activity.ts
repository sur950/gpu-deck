/**
 * Recent activity fixtures: rule runs, swap operations and alert events, as
 * the dashboard and the live stream show them.
 */

import { IDS } from '@/lib/mocks/fixtures/fleet';
import { hash32, uuid } from '@/lib/mocks/fixtures/random';
import type { Schemas } from '@/lib/mocks/types';

type SwapOperation = Schemas['SwapOperation'];
type RuleRun = Schemas['RuleRun'];
type AlertEvent = Schemas['AlertEvent'];
type SwapStep = Schemas['SwapStep'];

const iso = (ts: number) => new Date(ts).toISOString();
const minutesAgo = (now: number, m: number) => now - m * 60_000;

function steps(names: string[], startedAt: number, durationsMs: number[], failAt?: number): SwapStep[] {
  let t = startedAt;
  return names.map((name, i) => {
    const started = t;
    const finished = t + (durationsMs[i] ?? 1000);
    t = finished;
    if (failAt !== undefined && i > failAt) return { name, state: 'skipped', started_at: null, finished_at: null, error: null, command_id: null, details: null };
    if (failAt !== undefined && i === failAt) return { name, state: 'failed', started_at: iso(started), finished_at: iso(finished), error: 'health check failed: GET /health 503 after 90 s', command_id: uuid(`cmd:${name}:${startedAt}`), details: null };
    return { name, state: 'completed', started_at: iso(started), finished_at: iso(finished), error: null, command_id: name === 'drain' ? null : uuid(`cmd:${name}:${startedAt}`), details: null };
  });
}

export function recentSwaps(now: number): SwapOperation[] {
  const restartStart = minutesAgo(now, 26);
  const scaleStart = minutesAgo(now, 71);
  const unloadStart = minutesAgo(now, 128);
  const failedStart = minutesAgo(now, 193);
  const loadStart = minutesAgo(now, 6 * 60 + 4);
  return [
    {
      id: uuid('swap:restart-qwen-l40s'),
      kind: 'restart',
      state: 'completed',
      trigger: 'auto_heal',
      rule_run_id: uuid('rule-run:auto-heal:fired'),
      rule_name: 'Auto heal',
      layout_id: null,
      deployment_id: IDS.deployments.qwen,
      deployment_name: 'qwen3-0.6b',
      from_instance_id: IDS.instances.qwenL40s,
      to_instance_id: IDS.instances.qwenL40s,
      node_id: IDS.nodes.gpu,
      plan: { reason: 'error_rate 6.4% > 5% for 3m', action: 'restart in place' },
      steps: steps(['drain', 'unload', 'load', 'health_check'], restartStart, [38_000, 4_200, 11_900, 6_100]),
      current_step: 4,
      rollback: {},
      drain_timeout_seconds: 120,
      dry_run: false,
      started_at: iso(restartStart),
      finished_at: iso(restartStart + 60_200),
      error_message: null,
      created_at: iso(restartStart - 800),
      updated_at: iso(restartStart + 60_200),
    },
    {
      id: uuid('swap:scale-up-qwen'),
      kind: 'scale_up',
      state: 'completed',
      trigger: 'rule',
      rule_run_id: uuid('rule-run:high-load:fired'),
      rule_name: 'qwen3 grow on queue',
      layout_id: null,
      deployment_id: IDS.deployments.qwen,
      deployment_name: 'qwen3-0.6b',
      from_instance_id: null,
      to_instance_id: IDS.instances.qwenL40s,
      node_id: IDS.nodes.gpu,
      plan: { reason: 'waiting_sessions 6 > 4 for 3m', target: 'gpu-box-01 / L40S #0 (24 GiB slice)' },
      steps: steps(['reserve_slice', 'prefetch', 'load', 'health_check', 'route'], scaleStart, [900, 2_400, 14_600, 5_800, 300]),
      current_step: 5,
      rollback: {},
      drain_timeout_seconds: 120,
      dry_run: false,
      started_at: iso(scaleStart),
      finished_at: iso(scaleStart + 24_000),
      error_message: null,
      created_at: iso(scaleStart - 500),
      updated_at: iso(scaleStart + 24_000),
    },
    {
      id: uuid('swap:unload-gemma-idle'),
      kind: 'unload',
      state: 'completed',
      trigger: 'rule',
      rule_run_id: uuid('rule-run:idle-unload:fired'),
      rule_name: 'Night idle unload',
      layout_id: null,
      deployment_id: IDS.deployments.gemma,
      deployment_name: 'gemma-3-1b',
      from_instance_id: uuid('instance:gemma-3-1b:mac:prev'),
      to_instance_id: null,
      node_id: IDS.nodes.mac,
      plan: { reason: 'no requests for 32m (threshold 30m)', freed_bytes: 4 * 1024 ** 3 },
      steps: steps(['drain', 'unload', 'release_slice'], unloadStart, [1_200, 3_100, 200]),
      current_step: 3,
      rollback: {},
      drain_timeout_seconds: 120,
      dry_run: false,
      started_at: iso(unloadStart),
      finished_at: iso(unloadStart + 4_500),
      error_message: null,
      created_at: iso(unloadStart - 300),
      updated_at: iso(unloadStart + 4_500),
    },
    {
      id: uuid('swap:move-qwen-failed'),
      kind: 'move',
      state: 'rolled_back',
      trigger: 'manual',
      rule_run_id: null,
      rule_name: null,
      layout_id: null,
      deployment_id: IDS.deployments.qwen,
      deployment_name: 'qwen3-0.6b',
      from_instance_id: IDS.instances.qwenMac,
      to_instance_id: null,
      node_id: IDS.nodes.gpu,
      plan: { reason: 'manual move to gpu-box-01 / L40S #1', target: 'L40S #1 (12 GiB slice)' },
      steps: steps(['reserve_slice', 'load', 'health_check', 'drain', 'unload'], failedStart, [700, 15_200, 90_000, 0, 0], 2),
      current_step: 2,
      rollback: { released_slice: true, kept_source: true },
      drain_timeout_seconds: 120,
      dry_run: false,
      started_at: iso(failedStart),
      finished_at: iso(failedStart + 108_000),
      error_message: 'health check failed on L40S #1: GET /health 503 after 90 s — source instance kept',
      created_at: iso(failedStart - 400),
      updated_at: iso(failedStart + 108_000),
    },
    {
      id: uuid('swap:load-gemma-day'),
      kind: 'apply_layout',
      state: 'completed',
      trigger: 'layout',
      rule_run_id: uuid('rule-run:schedule:fired'),
      rule_name: 'Day layout at 08:00',
      layout_id: IDS.layouts.day,
      deployment_id: IDS.deployments.gemma,
      deployment_name: 'gemma-3-1b',
      from_instance_id: null,
      to_instance_id: IDS.instances.gemmaMac,
      node_id: IDS.nodes.mac,
      plan: { layout: 'Day', changes: 1 },
      steps: steps(['reserve_slice', 'load', 'health_check', 'route'], loadStart, [300, 9_800, 2_100, 200]),
      current_step: 4,
      rollback: {},
      drain_timeout_seconds: 120,
      dry_run: false,
      started_at: iso(loadStart),
      finished_at: iso(loadStart + 12_400),
      error_message: null,
      created_at: iso(loadStart - 200),
      updated_at: iso(loadStart + 12_400),
    },
  ];
}

function run(
  overrides: Partial<RuleRun> & Pick<RuleRun, 'id' | 'rule_id' | 'rule_name' | 'rule_type' | 'evaluated_at' | 'outcome'>,
): RuleRun {
  const fired = overrides.outcome === 'fired';
  return {
    fired,
    dry_run: overrides.outcome === 'dry_run',
    decision: {},
    actions: [],
    error_message: null,
    duration_ms: 12,
    created_at: overrides.evaluated_at,
    ...overrides,
  };
}

export function recentRuleRuns(now: number): RuleRun[] {
  return [
    run({
      id: uuid('rule-run:auto-heal:fired'),
      rule_id: IDS.rules.autoHeal,
      rule_name: 'Auto heal',
      rule_type: 'auto_heal',
      evaluated_at: iso(minutesAgo(now, 26)),
      outcome: 'fired',
      decision: { error_rate_pct: 6.4, threshold_pct: 5, window_minutes: 3, instance: 'qwen3-0.6b@gpu-box-01' },
      actions: [{ action: 'restart', target_type: 'instance', target_id: IDS.instances.qwenL40s, target_name: 'qwen3-0.6b@gpu-box-01', swap_operation_id: uuid('swap:restart-qwen-l40s'), status: 'completed', message: 'restarted in place, healthy after 60 s' }],
      duration_ms: 61_400,
    }),
    run({
      id: uuid('rule-run:idle-unload:nomatch'),
      rule_id: IDS.rules.idleUnload,
      rule_name: 'Night idle unload',
      rule_type: 'idle_unload',
      evaluated_at: iso(minutesAgo(now, 2)),
      outcome: 'no_match',
      decision: { candidates: 1, 'gemma-3-1b': { idle_minutes: 1, threshold_minutes: 30 } },
    }),
    run({
      id: uuid('rule-run:high-load:dry'),
      rule_id: IDS.rules.highLoad,
      rule_name: 'qwen3 grow on queue',
      rule_type: 'high_load_grow',
      evaluated_at: iso(minutesAgo(now, 9)),
      outcome: 'dry_run',
      decision: { waiting_sessions: 5, threshold: 4, window_minutes: 3, kv_cache_pct: 71 },
      actions: [{ action: 'scale_up', target_type: 'deployment', target_id: IDS.deployments.qwen, target_name: 'qwen3-0.6b', swap_operation_id: null, status: 'dry_run', message: 'would add a replica on gpu-box-01 / L40S #1 (free 36 GiB)' }],
    }),
    run({
      id: uuid('rule-run:high-load:fired'),
      rule_id: IDS.rules.highLoad,
      rule_name: 'qwen3 grow on queue',
      rule_type: 'high_load_grow',
      evaluated_at: iso(minutesAgo(now, 71)),
      outcome: 'fired',
      decision: { waiting_sessions: 6, threshold: 4, window_minutes: 3 },
      actions: [{ action: 'scale_up', target_type: 'deployment', target_id: IDS.deployments.qwen, target_name: 'qwen3-0.6b', swap_operation_id: uuid('swap:scale-up-qwen'), status: 'completed', message: 'replica added on gpu-box-01 / L40S #0' }],
      duration_ms: 24_800,
    }),
    run({
      id: uuid('rule-run:low-traffic:cooldown'),
      rule_id: IDS.rules.lowTraffic,
      rule_name: 'Shrink qwen3 at night',
      rule_type: 'low_traffic_shrink',
      evaluated_at: iso(minutesAgo(now, 14)),
      outcome: 'cooldown',
      decision: { requests_per_minute: 11, threshold: 15, cooldown_remaining_minutes: 46 },
    }),
    run({
      id: uuid('rule-run:idle-unload:fired'),
      rule_id: IDS.rules.idleUnload,
      rule_name: 'Night idle unload',
      rule_type: 'idle_unload',
      evaluated_at: iso(minutesAgo(now, 128)),
      outcome: 'fired',
      decision: { 'gemma-3-1b': { idle_minutes: 32, threshold_minutes: 30 } },
      actions: [{ action: 'unload', target_type: 'instance', target_id: uuid('instance:gemma-3-1b:mac:prev'), target_name: 'gemma-3-1b@mac-m5-studio', swap_operation_id: uuid('swap:unload-gemma-idle'), status: 'completed', message: 'freed 4.0 GiB on Apple M5' }],
      duration_ms: 4_900,
    }),
    run({
      id: uuid('rule-run:schedule:fired'),
      rule_id: IDS.rules.schedule,
      rule_name: 'Day layout at 08:00',
      rule_type: 'time_schedule',
      evaluated_at: iso(minutesAgo(now, 6 * 60 + 4)),
      outcome: 'fired',
      decision: { layout: 'Day', timezone: 'Asia/Kolkata' },
      actions: [{ action: 'apply_layout', target_type: 'layout', target_id: IDS.layouts.day, target_name: 'Day', swap_operation_id: uuid('swap:load-gemma-day'), status: 'completed', message: '1 change applied' }],
      duration_ms: 12_900,
    }),
  ].sort((a, b) => b.evaluated_at.localeCompare(a.evaluated_at));
}

export function recentAlerts(now: number): AlertEvent[] {
  return [
    {
      id: uuid('alert:kv-high-qwen'),
      severity: 'warning',
      event_type: 'kv_cache_high',
      title: 'KV cache above 85% on qwen3-0.6b@gpu-box-01',
      message: 'kv_cache_usage_pct averaged 88% over 5 minutes; waiting sessions climbing. "qwen3 grow on queue" is in dry-run.',
      resource_type: 'instance',
      resource_id: IDS.instances.qwenL40s,
      resource_name: 'qwen3-0.6b@gpu-box-01',
      payload: { kv_cache_usage_pct: 88, waiting_sessions: 5 },
      dedupe_key: `kv_cache_high:${IDS.instances.qwenL40s}`,
      status: 'open',
      occurrences: 3,
      first_seen_at: iso(minutesAgo(now, 17)),
      last_seen_at: iso(minutesAgo(now, 4)),
      acknowledged_at: null,
      resolved_at: null,
      deliveries: [{ id: uuid('delivery:slack:kv'), channel_id: uuid('channel:slack-ops'), channel_name: 'Slack #gpu-ops', status: 'sent', attempts: 1, response_code: 200, sent_at: iso(minutesAgo(now, 17)), error_message: null }],
      created_at: iso(minutesAgo(now, 17)),
    },
    {
      id: uuid('alert:auto-heal-restart'),
      severity: 'critical',
      event_type: 'rule_fired',
      title: 'Auto heal restarted qwen3-0.6b@gpu-box-01',
      message: 'Error rate 6.4% over 3 minutes (threshold 5%). Instance restarted in place and is healthy again.',
      resource_type: 'swap_operation',
      resource_id: uuid('swap:restart-qwen-l40s'),
      resource_name: 'restart qwen3-0.6b',
      payload: { error_rate_pct: 6.4 },
      dedupe_key: null,
      status: 'acknowledged',
      occurrences: 1,
      first_seen_at: iso(minutesAgo(now, 26)),
      last_seen_at: iso(minutesAgo(now, 26)),
      acknowledged_at: iso(minutesAgo(now, 21)),
      resolved_at: null,
      deliveries: [
        { id: uuid('delivery:slack:heal'), channel_id: uuid('channel:slack-ops'), channel_name: 'Slack #gpu-ops', status: 'sent', attempts: 1, response_code: 200, sent_at: iso(minutesAgo(now, 26)), error_message: null },
        { id: uuid('delivery:email:heal'), channel_id: uuid('channel:email-oncall'), channel_name: 'Email on-call', status: 'sent', attempts: 1, response_code: 250, sent_at: iso(minutesAgo(now, 26)), error_message: null },
      ],
      created_at: iso(minutesAgo(now, 26)),
    },
    {
      id: uuid('alert:move-rolled-back'),
      severity: 'warning',
      event_type: 'swap_failed',
      title: 'Move of qwen3-0.6b to L40S #1 rolled back',
      message: 'Health check on the target failed after 90 s (GET /health 503). The source instance on mac-m5-studio was kept.',
      resource_type: 'swap_operation',
      resource_id: uuid('swap:move-qwen-failed'),
      resource_name: 'move qwen3-0.6b',
      payload: {},
      dedupe_key: null,
      status: 'resolved',
      occurrences: 1,
      first_seen_at: iso(minutesAgo(now, 191)),
      last_seen_at: iso(minutesAgo(now, 191)),
      acknowledged_at: iso(minutesAgo(now, 180)),
      resolved_at: iso(minutesAgo(now, 150)),
      deliveries: [{ id: uuid('delivery:slack:move'), channel_id: uuid('channel:slack-ops'), channel_name: 'Slack #gpu-ops', status: 'sent', attempts: 1, response_code: 200, sent_at: iso(minutesAgo(now, 191)), error_message: null }],
      created_at: iso(minutesAgo(now, 191)),
    },
    {
      id: uuid('alert:cert-renew'),
      severity: 'info',
      event_type: 'node.cert_renewal_due',
      title: 'Agent certificate renews in 21 days',
      message: 'gpu-box-01 rotates its mTLS client certificate automatically before it expires.',
      resource_type: 'node',
      resource_id: IDS.nodes.gpu,
      resource_name: 'gpu-box-01',
      payload: { days_left: 21 },
      dedupe_key: 'cert:gpu-box-01:21d',
      status: 'open',
      occurrences: 1,
      first_seen_at: iso(minutesAgo(now, 6 * 60 + 40)),
      last_seen_at: iso(minutesAgo(now, 6 * 60 + 40)),
      acknowledged_at: null,
      resolved_at: null,
      deliveries: [{ id: uuid('delivery:webhook:cert'), channel_id: uuid('channel:webhook-finops'), channel_name: 'FinOps webhook', status: 'failed', attempts: 3, response_code: 503, sent_at: null, error_message: 'upstream 503 after 3 attempts' }],
      created_at: iso(minutesAgo(now, 6 * 60 + 40)),
    },
  ];
}

// ─── Live-stream variants (something new every so often) ─────────────────────

export function liveRuleRun(now: number): RuleRun {
  const idle = (hash32(`idle:${Math.floor(now / 45_000)}`) % 28) + 1;
  return run({
    id: uuid(`rule-run:live:${Math.floor(now / 45_000)}`),
    rule_id: IDS.rules.idleUnload,
    rule_name: 'Night idle unload',
    rule_type: 'idle_unload',
    evaluated_at: iso(now),
    outcome: 'no_match',
    decision: { 'gemma-3-1b': { idle_minutes: idle, threshold_minutes: 30 } },
  });
}

export function liveSwap(now: number): SwapOperation {
  const start = now - 5_000;
  return {
    id: uuid(`swap:live:${Math.floor(now / 45_000)}`),
    kind: 'restart',
    state: 'health_check',
    trigger: 'manual',
    rule_run_id: null,
    rule_name: null,
    layout_id: null,
    deployment_id: IDS.deployments.gemma,
    deployment_name: 'gemma-3-1b',
    from_instance_id: IDS.instances.gemmaMac,
    to_instance_id: IDS.instances.gemmaMac,
    node_id: IDS.nodes.mac,
    plan: { reason: 'manual restart' },
    steps: [
      { name: 'drain', state: 'completed', started_at: iso(start), finished_at: iso(start + 1_100), error: null, command_id: null, details: null },
      { name: 'unload', state: 'completed', started_at: iso(start + 1_100), finished_at: iso(start + 2_400), error: null, command_id: uuid('cmd:live:unload'), details: null },
      { name: 'load', state: 'completed', started_at: iso(start + 2_400), finished_at: iso(start + 4_800), error: null, command_id: uuid('cmd:live:load'), details: null },
      { name: 'health_check', state: 'running', started_at: iso(start + 4_800), finished_at: null, error: null, command_id: null, details: null },
    ],
    current_step: 3,
    rollback: {},
    drain_timeout_seconds: 120,
    dry_run: false,
    started_at: iso(start),
    finished_at: null,
    error_message: null,
    created_at: iso(start),
    updated_at: iso(now),
  };
}

export function liveAlert(now: number): AlertEvent | null {
  const base = recentAlerts(now)[0];
  return { ...base, occurrences: base.occurrences + 1, last_seen_at: iso(now) };
}
