/**
 * Automation mocks: rules and rule runs, layouts, swap operations,
 * alert channels and alert events.
 *
 * Not canned answers — the mock evaluates rules against the fixture metrics
 * (the same curves the observability pages draw), so "Dry run now" shows a
 * believable decision; layouts diff against a tracked "current placement";
 * swaps advance through plan → route_off → drain → unload → load → health →
 * route_on in real time after they are created. The overview's recent
 * activity (fixtures/activity.ts) is part of the history, so dashboard
 * drill-downs land on real records.
 */

import { IDS, GiB } from '@/lib/mocks/fixtures/fleet';
import { activeSlices, fleetStore } from '@/lib/mocks/handlers/fleet';
import { recentAlerts, recentRuleRuns, recentSwaps } from '@/lib/mocks/fixtures/activity';
import { hash32, round, uuid } from '@/lib/mocks/fixtures/random';
import {
  engineActiveSessions,
  engineErrorRatePct,
  engineKvUsagePct,
  engineRequestsPerMinute,
  engineWaitingSessions,
} from '@/lib/mocks/fixtures/timeseries';
import { bodyOf, created, fail, noContent, notFound, ok, paginate, registerMockHandlers, sortBy } from '@/lib/mocks/registry';
import type { MockRequest, MockResult, Schemas } from '@/lib/mocks/types';
import { recordAudit } from '@/lib/mocks/handlers/security';
import { inTimeWindows, nextOccurrences, occurrencesBetween, previousOccurrence } from '@/components/automation/schedule';
import { matchesTarget, type MatchableDeployment } from '@/components/automation/target-match';
import type { TargetSelector } from '@/hooks/api/automation-api';
import ruleSchema from '../../../../../libs/contracts/rules/rule.schema.json';
import targetSelectorSchema from '../../../../../libs/contracts/rules/target-selector.schema.json';
import layoutSchema from '../../../../../libs/contracts/rules/layout.schema.json';
import idleUnloadSchema from '../../../../../libs/contracts/rules/idle_unload.schema.json';
import timeScheduleSchema from '../../../../../libs/contracts/rules/time_schedule.schema.json';
import lowTrafficShrinkSchema from '../../../../../libs/contracts/rules/low_traffic_shrink.schema.json';
import highLoadGrowSchema from '../../../../../libs/contracts/rules/high_load_grow.schema.json';
import makeRoomSchema from '../../../../../libs/contracts/rules/make_room_by_priority.schema.json';
import autoHealSchema from '../../../../../libs/contracts/rules/auto_heal.schema.json';

type Rule = Schemas['Rule'];
type RuleRun = Schemas['RuleRun'];
type RuleRunAction = Schemas['RuleRunAction'];
type Layout = Schemas['Layout'];
type LayoutItem = Schemas['LayoutItem'];
type LayoutDiff = Schemas['LayoutDiff'];
type LayoutDiffEntry = Schemas['LayoutDiffEntry'];
type SwapOperation = Schemas['SwapOperation'];
type SwapStep = Schemas['SwapStep'];
type AlertChannel = Schemas['AlertChannel'];
type AlertEvent = Schemas['AlertEvent'];
type Deployment = Schemas['Deployment'];

const MIN = 60_000;
const HOUR = 3_600_000;
const BOOT = Date.now();
const iso = (ms: number) => new Date(ms).toISOString();

// ─── Fleet state (the fleet mocks' in-memory store, so rule decisions follow drains, new deployments…) ──

const liveInstances = () => fleetStore.instances.filter((i) => i.state !== 'stopped');
const allDeployments = () => fleetStore.deployments.filter((d) => d.status !== 'deleting');
const deploymentById = (id: string) => fleetStore.deployments.find((d) => d.id === id);
const nodeById = (id: string) => fleetStore.nodes.find((n) => n.id === id);
const deviceById = (id: string) => fleetStore.devices.find((d) => d.id === id);

// ─── Small helpers ────────────────────────────────────────────────────────────

function asMatchable(d: Deployment): MatchableDeployment {
  const instances = liveInstances().filter((i) => i.deployment_id === d.id);
  const nodeIds = [...new Set(instances.map((i) => i.node_id))];
  return {
    id: d.id,
    name: d.name,
    modelId: d.model_id ?? '',
    clusterId: d.cluster_id ?? null,
    modelType: d.model_type ?? null,
    priority: d.effective_priority ?? d.priority_override ?? 'normal',
    engine: d.engine_name ?? null,
    nodeIds,
    nodeLabels: nodeIds.map((id) => nodeById(id)?.labels ?? {}),
  };
}

function targetDeployments(target: TargetSelector | undefined): Deployment[] {
  return allDeployments().filter((d) => matchesTarget(asMatchable(d), target ?? {}));
}

function priorityOf(d: Deployment): string {
  return d.effective_priority ?? d.priority_override ?? 'normal';
}

function instancesOf(deploymentId: string) {
  return liveInstances().filter((i) => i.deployment_id === deploymentId);
}

function instanceVram(deploymentId: string, nodeId?: string | null): number {
  const list = instancesOf(deploymentId);
  const onNode = nodeId ? list.find((i) => i.node_id === nodeId) : undefined;
  const pick = onNode ?? list.sort((a, b) => Number(b.resources?.vram_bytes ?? 0) - Number(a.resources?.vram_bytes ?? 0))[0];
  const v = pick?.resources?.vram_bytes;
  return typeof v === 'number' ? v : 4 * GiB;
}

function deviceLabel(deviceId: string): string {
  const d = deviceById(deviceId);
  if (!d) return 'GPU';
  const short = d.vendor === 'apple' ? 'Apple M5' : d.name.replace('NVIDIA ', '');
  return `${d.node_name ?? 'node'} / ${short}${d.vendor === 'apple' ? '' : ` #${d.index}`}`;
}

/** Minutes since the deployment last served a request (gemma drifts, like the live rule-run feed). */
function idleMinutes(d: Deployment, now: number): number {
  if (d.id === IDS.deployments.gemma) return (hash32(`idle:${Math.floor(now / 45_000)}`) % 28) + 1 + (hash32(`idle-b:${Math.floor(now / 600_000)}`) % 14);
  if (d.id === IDS.deployments.nomic) return hash32(`idle-n:${Math.floor(now / 60_000)}`) % 4;
  return 0;
}

function freeVramByDevice(): Map<string, number> {
  const free = new Map<string, number>();
  for (const dev of fleetStore.devices) {
    const reserved = activeSlices(dev.id).reduce((sum, sl) => sum + (sl.vram_bytes ?? 0), 0);
    free.set(dev.id, dev.vram_total_bytes - Math.max(reserved, dev.vram_reserved_bytes ?? 0));
  }
  return free;
}

function bestDeviceFor(deploymentId: string, exclude: string[] = []): { deviceId: string; freeBytes: number } | null {
  const hosting = new Set(instancesOf(deploymentId).flatMap((i) => i.device_ids ?? []));
  let best: { deviceId: string; freeBytes: number } | null = null;
  for (const [deviceId, freeBytes] of freeVramByDevice()) {
    if (exclude.includes(deviceId)) continue;
    const score = freeBytes - (hosting.has(deviceId) ? 64 * GiB : 0);
    if (!best || score > best.freeBytes - (hosting.has(best.deviceId) ? 64 * GiB : 0)) best = { deviceId, freeBytes };
  }
  return best;
}

const gib = (bytes: number) => `${round(bytes / GiB, 1)} GiB`;

/** Copy of `row` without the internal id fields (decisions show names, not ids). */
function publicRow<T extends Record<string, unknown>>(row: T): Record<string, unknown> {
  return Object.fromEntries(Object.entries(row).filter(([k]) => k !== 'deployment_id' && k !== 'instance_id'));
}

// ─── Rules ────────────────────────────────────────────────────────────────────

const RULE_IDS = { ...IDS.rules, nightSwitch: uuid('rule:night-layout-22'), makeRoom: uuid('rule:make-room-chat') };
const EXTRA_LAYOUT = uuid('layout:eval-burst');

function rule(fields: Omit<Rule, 'created_at' | 'updated_at' | 'fire_count'> & { createdDaysAgo: number; fireCount: number }): Rule {
  const { createdDaysAgo, fireCount, ...rest } = fields;
  return {
    ...rest,
    created_at: iso(BOOT - createdDaysAgo * 24 * HOUR),
    updated_at: iso(BOOT - Math.max(1, createdDaysAgo - 2) * 24 * HOUR),
    fire_count: fireCount,
  };
}

const rules: Rule[] = [
  rule({
    id: RULE_IDS.idleUnload,
    name: 'Night idle unload',
    description: 'Free GPU memory overnight when a normal or evictable model has nobody talking to it.',
    type: 'idle_unload',
    params: { idle_minutes: 30, min_loaded_minutes: 10, keep_replicas: 0, drain_timeout_seconds: 120, respect_time_windows: ['20:00-08:00'], timezone: 'Asia/Kolkata' },
    target: { priorities: ['normal', 'evictable'] },
    enabled: true,
    dry_run: false,
    cooldown_seconds: 1800,
    evaluation_interval_seconds: 60,
    priority: 50,
    createdDaysAgo: 12,
    fireCount: 13,
  }),
  rule({
    id: RULE_IDS.highLoad,
    name: 'qwen3 grow on queue',
    description: 'Add a qwen3 replica when requests start queueing or the KV cache fills up. Watching in dry run before going live.',
    type: 'high_load_grow',
    params: { waiting_requests_above: 4, kv_cache_pct_above: 85, condition: 'any', for_minutes: 3, step_replicas: 1, max_replicas: 3, placement_strategy: null, min_free_vram_bytes: null, make_room: true },
    target: { deployment_ids: [IDS.deployments.qwen] },
    enabled: true,
    dry_run: true,
    cooldown_seconds: 600,
    evaluation_interval_seconds: 30,
    priority: 20,
    createdDaysAgo: 4,
    fireCount: 2,
  }),
  rule({
    id: RULE_IDS.autoHeal,
    name: 'Auto heal',
    description: 'Restart instances that fail health checks or throw errors; move them to another GPU if restarts do not help.',
    type: 'auto_heal',
    params: { health_failures: 3, error_rate_pct_above: 5, for_minutes: 3, min_requests_in_window: 20, action: 'restart_then_move', max_restarts_per_hour: 3, move_placement_strategy: null, exclude_failed_device: true, drain_timeout_seconds: 30 },
    target: {},
    enabled: true,
    dry_run: false,
    cooldown_seconds: 300,
    evaluation_interval_seconds: 15,
    priority: 10,
    createdDaysAgo: 18,
    fireCount: 4,
  }),
  rule({
    id: RULE_IDS.lowTraffic,
    name: 'Shrink qwen3 at night',
    description: 'Give back the second qwen3 replica when traffic drops off.',
    type: 'low_traffic_shrink',
    params: { requests_per_minute_below: 15, for_minutes: 20, action: 'remove_replica', step_replicas: 1, min_replicas: 1, shrink_slice: null, drain_timeout_seconds: 120 },
    target: { deployment_ids: [IDS.deployments.qwen] },
    enabled: true,
    dry_run: false,
    cooldown_seconds: 3600,
    evaluation_interval_seconds: 60,
    priority: 60,
    createdDaysAgo: 9,
    fireCount: 6,
  }),
  rule({
    id: RULE_IDS.schedule,
    name: 'Day layout at 08:00',
    description: 'Bring the full day placement back before the office comes online.',
    type: 'time_schedule',
    params: { at: '08:00', days: ['mon', 'tue', 'wed', 'thu', 'fri'], cron: null, timezone: 'Asia/Kolkata', layout_id: IDS.layouts.day, prefetch_minutes_before: 15, drain_timeout_seconds: 300, catch_up: true },
    target: {},
    enabled: true,
    dry_run: false,
    cooldown_seconds: 300,
    evaluation_interval_seconds: 30,
    priority: 5,
    createdDaysAgo: 15,
    fireCount: 11,
  }),
  rule({
    id: RULE_IDS.nightSwitch,
    name: 'Night layout at 22:00',
    description: 'Drop to the lean night placement: always-on chat only, embeddings on the L40S box.',
    type: 'time_schedule',
    params: { at: '22:00', days: ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'], cron: null, timezone: 'Asia/Kolkata', layout_id: IDS.layouts.night, prefetch_minutes_before: 5, drain_timeout_seconds: 300, catch_up: true },
    target: {},
    enabled: true,
    dry_run: false,
    cooldown_seconds: 300,
    evaluation_interval_seconds: 30,
    priority: 5,
    createdDaysAgo: 15,
    fireCount: 15,
  }),
  rule({
    id: RULE_IDS.makeRoom,
    name: 'Make room for chat models',
    description: 'If a chat or thinking model cannot be placed, evict an idle evictable model from the best GPU.',
    type: 'make_room_by_priority',
    params: { evict_priorities: ['evictable'], victim_idle_minutes: 5, max_evictions_per_run: 1, trigger: 'both', drain_timeout_seconds: 120, protect_always_on: true },
    target: { model_types: ['chat', 'thinking'] },
    enabled: false,
    dry_run: false,
    cooldown_seconds: 300,
    evaluation_interval_seconds: 60,
    priority: 30,
    createdDaysAgo: 7,
    fireCount: 1,
  }),
];

/** When each rule was disabled (frozen last_evaluated_at). */
const disabledAt = new Map<string, number>([[RULE_IDS.makeRoom, BOOT - 2 * 24 * HOUR - 17 * MIN]]);

// ─── Rule runs (history) ──────────────────────────────────────────────────────

function mkRun(r: Rule, evaluatedAt: number, outcome: RuleRun['outcome'], decision: Record<string, unknown>, actions: RuleRunAction[] = [], extra: Partial<RuleRun> = {}): RuleRun {
  return {
    id: uuid(`run:${r.id}:${evaluatedAt}:${outcome}`),
    rule_id: r.id,
    rule_name: r.name,
    rule_type: r.type,
    outcome,
    fired: outcome === 'fired',
    dry_run: outcome === 'dry_run',
    evaluated_at: iso(evaluatedAt),
    duration_ms: outcome === 'fired' ? 4_000 + (hash32(`${r.id}:${evaluatedAt}`) % 50_000) : 6 + (hash32(`${r.id}:${evaluatedAt}`) % 40),
    decision,
    actions,
    error_message: null,
    created_at: iso(evaluatedAt),
    ...extra,
  };
}

function ruleById(id: string): Rule | undefined {
  return rules.find((r) => r.id === id);
}

function seedHistory(): RuleRun[] {
  const runs: RuleRun[] = [...recentRuleRuns(BOOT)];
  const seen = new Set(runs.map((r) => r.id));
  const push = (run: RuleRun) => {
    if (!seen.has(run.id)) {
      seen.add(run.id);
      runs.push(run);
    }
  };
  const idle = ruleById(RULE_IDS.idleUnload) as Rule;
  const grow = ruleById(RULE_IDS.highLoad) as Rule;
  const heal = ruleById(RULE_IDS.autoHeal) as Rule;
  const shrink = ruleById(RULE_IDS.lowTraffic) as Rule;
  const room = ruleById(RULE_IDS.makeRoom) as Rule;

  // Hourly no_match rows (the evaluator logs no_match at most once an hour per rule).
  for (let h = 1; h <= 60; h += 1) {
    const t = Math.floor((BOOT - h * HOUR) / HOUR) * HOUR + (hash32(`nm:${h}`) % 50) * MIN;
    push(mkRun(idle, t + 2 * MIN, 'no_match', { threshold_minutes: 30, in_window: inTimeWindows(['20:00-08:00'], 'Asia/Kolkata', t), targets: [{ deployment: 'gemma-3-1b', idle_minutes: hash32(`gi:${h}`) % 22, active_sessions: hash32(`ga:${h}`) % 2 }, { deployment: 'nomic-embed', idle_minutes: hash32(`ni:${h}`) % 4, active_sessions: 1 }] }));
    push(mkRun(heal, t + 7 * MIN, 'no_match', { health_failures_threshold: 3, error_rate_threshold_pct: 5, instances_checked: 4, worst: { instance: 'qwen3-0.6b@gpu-box-01', error_rate_pct: round((hash32(`er:${h}`) % 30) / 10, 1) } }));
    push(mkRun(shrink, t + 11 * MIN, 'no_match', { threshold_rpm: 15, window_minutes: 20, min_rpm_in_window: 20 + (hash32(`rpm:${h}`) % 60), targets: [{ deployment: 'qwen3-0.6b', replicas: 2 }] }));
    if (h % 3 === 0) {
      push(mkRun(grow, t + 23 * MIN, 'dry_run', { condition: 'any', window_minutes: 3, targets: [{ deployment: 'qwen3-0.6b', waiting_avg: 5 + (hash32(`w:${h}`) % 4), kv_cache_pct_max: 80 + (hash32(`kv:${h}`) % 15), satisfied_minutes: 3 }] }, [{ action: 'scale_up', target_type: 'deployment', target_id: IDS.deployments.qwen, target_name: 'qwen3-0.6b', status: 'dry_run', swap_operation_id: null, message: 'would add a replica on gpu-box-01 / L40S #1 (free 37.0 GiB)' }]));
    } else {
      push(mkRun(grow, t + 23 * MIN, 'no_match', { condition: 'any', window_minutes: 3, targets: [{ deployment: 'qwen3-0.6b', waiting_avg: hash32(`w:${h}`) % 4, kv_cache_pct_max: 40 + (hash32(`kv:${h}`) % 40), satisfied_minutes: hash32(`sm:${h}`) % 3 }] }));
    }
  }
  // Notable runs.
  push(mkRun(shrink, BOOT - 26 * HOUR - 50 * MIN, 'fired', { threshold_rpm: 15, window_minutes: 20, max_rpm_in_window: 9.4, targets: [{ deployment: 'qwen3-0.6b', replicas: 2, min_replicas: 1 }] }, [{ action: 'scale_down', target_type: 'deployment', target_id: IDS.deployments.qwen, target_name: 'qwen3-0.6b', status: 'completed', swap_operation_id: uuid('swap:hist:shrink'), message: 'removed the replica on mac-m5-studio (3.0 GiB freed)' }]));
  push(mkRun(shrink, BOOT - 25 * HOUR - 40 * MIN, 'skipped', { reason: 'min_replicas', replicas: 1, min_replicas: 1, max_rpm_in_window: 6.1 }));
  push(mkRun(heal, BOOT - 30 * HOUR - 12 * MIN, 'skipped', { reason: 'swap_capacity', in_flight_swaps: 2, limit: 2, instance: 'nomic-embed@gpu-box-01', health_failures: 3 }));
  push(mkRun(heal, BOOT - 44 * HOUR - 3 * MIN, 'error', { stage: 'inputs' }, [], { error_message: 'metric query timed out after 5 s (metric_rollups_engine_1m)' }));
  push(mkRun(idle, BOOT - 27 * HOUR - 8 * MIN, 'fired', { threshold_minutes: 30, in_window: true, targets: [{ deployment: 'gemma-3-1b', idle_minutes: 34.2, active_sessions: 0 }] }, [{ action: 'unload', target_type: 'instance', target_id: uuid('instance:gemma-3-1b:mac:hist'), target_name: 'gemma-3-1b@mac-m5-studio', status: 'completed', swap_operation_id: uuid('swap:hist:idle'), message: 'freed 4.0 GiB on Apple M5' }]));
  push(mkRun(idle, BOOT - 26 * HOUR - 37 * MIN, 'cooldown', { deployment: 'gemma-3-1b', cooldown_remaining_seconds: 1_060 }));
  push(mkRun(room, BOOT - 3 * 24 * HOUR - 4 * HOUR, 'fired', { requester: 'qwen3-0.6b', requester_priority: 'always_on', needed_bytes: 24 * GiB, device: 'gpu-box-01 / L40S #0', victims: [{ deployment: 'gemma-3-1b', priority: 'evictable', idle_minutes: 41 }] }, [{ action: 'unload', target_type: 'deployment', target_id: IDS.deployments.gemma, target_name: 'gemma-3-1b', status: 'completed', swap_operation_id: null, message: 'evicted gemma-3-1b, then placed qwen3-0.6b' }]));
  push(mkRun(room, BOOT - 2 * 24 * HOUR - 40 * MIN, 'no_match', { pending_deployments: 0 }));

  // Schedules: one fired run per occurrence over the last 3 days.
  for (const id of [RULE_IDS.schedule, RULE_IDS.nightSwitch]) {
    const r = ruleById(id) as Rule;
    const p = r.params as { layout_id: string; at: string; days: string[]; timezone: string };
    const layoutName = p.layout_id === IDS.layouts.day ? 'Day' : 'Night';
    for (const t of occurrencesBetween(p, BOOT - 3 * 24 * HOUR, BOOT)) {
      push(mkRun(r, t, 'fired', { layout: layoutName, timezone: p.timezone, scheduled_for: iso(t), changes: layoutName === 'Day' ? 2 : 2 }, [{ action: 'apply_layout', target_type: 'layout', target_id: p.layout_id, target_name: layoutName, status: 'completed', swap_operation_id: uuid(`swap:sched:${id}:${t}`), message: layoutName === 'Day' ? 'loaded gemma-3-1b and a second qwen3 replica' : 'unloaded gemma-3-1b and the mac qwen3 replica' }], { duration_ms: 20_000 + (hash32(`d:${t}`) % 30_000) }));
    }
  }
  return runs;
}

const runs: RuleRun[] = seedHistory();

function runsFor(ruleId: string): RuleRun[] {
  return runs.filter((r) => r.rule_id === ruleId).sort((a, b) => b.evaluated_at.localeCompare(a.evaluated_at));
}

function scheduleParams(r: Rule) {
  return r.params as { at?: string; days?: string[]; cron?: string | null; timezone?: string; layout_id?: string };
}

/** Rule with its derived run fields as of `now`. */
function ruleView(r: Rule, now = Date.now()): Rule {
  const history = runsFor(r.id);
  const lastFired = history.find((x) => x.outcome === 'fired');
  const firedHere = history.filter((x) => x.outcome === 'fired' && Date.parse(x.evaluated_at) >= BOOT).length;
  let lastEvaluated: number | null;
  if (!r.enabled) {
    lastEvaluated = disabledAt.get(r.id) ?? (history[0] ? Date.parse(history[0].evaluated_at) : null);
  } else if (r.type === 'time_schedule') {
    lastEvaluated = previousOccurrence(scheduleParams(r), now);
  } else {
    const interval = r.evaluation_interval_seconds * 1000;
    lastEvaluated = Math.floor(now / interval) * interval;
  }
  const latest = history[0];
  // Same rule as the backend: schedules → next fire time in the rule zone;
  // others → last evaluation + interval (now when never evaluated); null while disabled.
  let nextEvaluation: number | null = null;
  if (r.enabled) {
    nextEvaluation =
      r.type === 'time_schedule'
        ? (nextOccurrences(scheduleParams(r), now, 1)[0] ?? null)
        : Math.max(now, (lastEvaluated ?? now) + r.evaluation_interval_seconds * 1000);
  }
  return {
    ...r,
    fire_count: (r.fire_count ?? 0) + firedHere,
    last_evaluated_at: lastEvaluated ? iso(lastEvaluated) : null,
    next_evaluation_at: nextEvaluation === null ? null : iso(nextEvaluation),
    last_fired_at: lastFired?.evaluated_at ?? null,
    last_run: latest ? { id: latest.id, outcome: latest.outcome, fired: latest.fired, dry_run: latest.dry_run, evaluated_at: latest.evaluated_at } : null,
  };
}

// ─── Evaluator ────────────────────────────────────────────────────────────────

interface Evaluation {
  outcome: RuleRun['outcome'];
  decision: Record<string, unknown>;
  actions: RuleRunAction[];
  /** Swap plans to start when the run fires. */
  plans: SwapSeed[];
}

function windowBuckets(now: number, minutes: number): number[] {
  const end = Math.floor(now / MIN) * MIN;
  return Array.from({ length: minutes }, (_, k) => end - (k + 1) * MIN);
}

function inCooldown(r: Rule, deploymentId: string, now: number): number {
  const last = runsFor(r.id).find((x) => x.outcome === 'fired' && x.actions.some((a) => a.target_id === deploymentId || a.target_name?.startsWith(deploymentById(deploymentId)?.name ?? '§')));
  if (!last) return 0;
  const remaining = r.cooldown_seconds * 1000 - (now - Date.parse(last.evaluated_at));
  return remaining > 0 ? Math.ceil(remaining / 1000) : 0;
}

function evaluate(r: Rule, now: number, mode: 'dry_run' | 'run'): Evaluation {
  const act = mode === 'run' && !r.dry_run;
  const p = r.params as Record<string, unknown>;
  const num = (key: string, fallback: number) => (typeof p[key] === 'number' ? (p[key] as number) : fallback);
  const targets = targetDeployments(r.target as TargetSelector);
  const actionStatus = act ? 'pending' : 'dry_run';
  const met = (actions: RuleRunAction[], decision: Record<string, unknown>, plans: SwapSeed[]): Evaluation => ({
    outcome: act ? 'fired' : 'dry_run',
    decision,
    actions,
    plans: act ? plans : [],
  });

  switch (r.type) {
    case 'idle_unload': {
      const threshold = num('idle_minutes', 30);
      const windows = (p.respect_time_windows as string[] | undefined) ?? [];
      const tz = (p.timezone as string | undefined) ?? 'UTC';
      const inWindow = inTimeWindows(windows, tz, now);
      const rows = targets.map((d) => {
        const idle = idleMinutes(d, now);
        const active = instancesOf(d.id).reduce((s, i) => s + engineActiveSessions(i.id, now), 0);
        const protectedReason = priorityOf(d) === 'always_on' ? 'always_on' : null;
        const meets = idle >= threshold && active === 0;
        return { deployment: d.name, deployment_id: d.id, idle_minutes: idle, active_sessions: active, meets, protected: protectedReason };
      });
      const decision = { threshold_minutes: threshold, windows, timezone: tz, in_window: inWindow, targets: rows.map(publicRow) };
      const candidates = rows.filter((x) => x.meets);
      if (!candidates.length) return { outcome: 'no_match', decision, actions: [], plans: [] };
      if (!inWindow) return { outcome: 'skipped', decision: { ...decision, reason: 'outside_time_window' }, actions: [], plans: [] };
      const eligible = candidates.filter((x) => !x.protected);
      if (!eligible.length) return { outcome: 'skipped', decision: { ...decision, reason: 'always_on' }, actions: [], plans: [] };
      const actions: RuleRunAction[] = [];
      const plans: SwapSeed[] = [];
      for (const c of eligible) {
        const keep = num('keep_replicas', 0);
        for (const inst of instancesOf(c.deployment_id).slice(keep)) {
          const swapId = uuid(`swap:run:${r.id}:${inst.id}:${now}`);
          actions.push({ action: 'unload', target_type: 'instance', target_id: inst.id, target_name: `${c.deployment}@${inst.node_name}`, status: actionStatus, swap_operation_id: act ? swapId : null, message: `${act ? 'unloading' : 'would unload'} (frees ${gib(Number(inst.resources?.vram_bytes ?? 0))} on ${deviceLabel(inst.device_ids?.[0] ?? '')})` });
          plans.push({ id: swapId, kind: 'unload', deploymentId: c.deployment_id, nodeId: inst.node_id, fromInstanceId: inst.id, toInstanceId: null, trigger: 'rule', ruleName: r.name, drain: num('drain_timeout_seconds', 120), reason: `idle ${c.idle_minutes} min ≥ ${threshold} min` });
        }
      }
      return met(actions, decision, plans);
    }

    case 'high_load_grow': {
      const waitingAbove = p.waiting_requests_above as number | null;
      const kvAbove = p.kv_cache_pct_above as number | null;
      const condition = (p.condition as string) ?? 'any';
      const minutes = num('for_minutes', 2);
      const maxReplicas = num('max_replicas', 4);
      const rows = targets.map((d) => {
        const inst = instancesOf(d.id);
        const buckets = windowBuckets(now, minutes).map((t) => {
          const waiting = inst.reduce((s, i) => s + engineWaitingSessions(i.id, t), 0);
          const kv = Math.max(0, ...inst.map((i) => engineKvUsagePct(i.id, t)));
          const w = waitingAbove !== null && waitingAbove !== undefined ? waiting > waitingAbove : null;
          const k = kvAbove !== null && kvAbove !== undefined ? kv > kvAbove : null;
          const checks = [w, k].filter((x): x is boolean => x !== null);
          const ok = condition === 'all' ? checks.every(Boolean) : checks.some(Boolean);
          return { waiting, kv, ok };
        });
        const satisfied = buckets.filter((b) => b.ok).length;
        return {
          deployment: d.name,
          deployment_id: d.id,
          replicas: inst.length,
          waiting_avg: round(buckets.reduce((s, b) => s + b.waiting, 0) / Math.max(1, buckets.length), 1),
          kv_cache_pct_max: round(Math.max(0, ...buckets.map((b) => b.kv)), 1),
          satisfied_minutes: satisfied,
          meets: satisfied === minutes && buckets.length > 0,
        };
      });
      const decision = { condition, window_minutes: minutes, waiting_threshold: waitingAbove, kv_threshold_pct: kvAbove, max_replicas: maxReplicas, targets: rows.map(publicRow) };
      const hits = rows.filter((x) => x.meets);
      if (!hits.length) return { outcome: 'no_match', decision, actions: [], plans: [] };
      const room = hits.filter((x) => x.replicas < maxReplicas);
      if (!room.length) return { outcome: 'skipped', decision: { ...decision, reason: 'max_replicas' }, actions: [], plans: [] };
      const actions: RuleRunAction[] = [];
      const plans: SwapSeed[] = [];
      for (const h of room) {
        const dev = bestDeviceFor(h.deployment_id);
        const need = instanceVram(h.deployment_id);
        if (!dev || dev.freeBytes < need) {
          return { outcome: 'skipped', decision: { ...decision, reason: 'no_capacity', needed_bytes: need }, actions: [], plans: [] };
        }
        const device = deviceById(dev.deviceId);
        const swapId = uuid(`swap:run:${r.id}:${h.deployment_id}:${now}`);
        actions.push({ action: 'scale_up', target_type: 'deployment', target_id: h.deployment_id, target_name: h.deployment, status: actionStatus, swap_operation_id: act ? swapId : null, message: `${act ? 'adding' : 'would add'} a replica on ${deviceLabel(dev.deviceId)} (free ${gib(dev.freeBytes)}, needs ${gib(need)})` });
        plans.push({ id: swapId, kind: 'scale_up', deploymentId: h.deployment_id, nodeId: device?.node_id ?? null, fromInstanceId: null, toInstanceId: uuid(`instance:new:${swapId}`), trigger: 'rule', ruleName: r.name, drain: 120, reason: `waiting ${h.waiting_avg} / KV ${h.kv_cache_pct_max}% for ${minutes} min` });
      }
      return met(actions, decision, plans);
    }

    case 'low_traffic_shrink': {
      const below = num('requests_per_minute_below', 1);
      const minutes = num('for_minutes', 15);
      const minReplicas = num('min_replicas', 1);
      const rows = targets.map((d) => {
        const inst = instancesOf(d.id);
        const series = windowBuckets(now, minutes).map((t) => inst.reduce((s, i) => s + engineRequestsPerMinute(i.id, t), 0));
        const floor = priorityOf(d) === 'always_on' ? Math.max(1, minReplicas) : minReplicas;
        return { deployment: d.name, deployment_id: d.id, replicas: inst.length, min_replicas: floor, max_rpm_in_window: round(Math.max(0, ...series), 1), meets: series.length > 0 && series.every((v) => v < below) };
      });
      const decision = { threshold_rpm: below, window_minutes: minutes, action: p.action ?? 'remove_replica', targets: rows.map(publicRow) };
      const hits = rows.filter((x) => x.meets);
      if (!hits.length) return { outcome: 'no_match', decision, actions: [], plans: [] };
      const shrinkable = hits.filter((x) => x.replicas > x.min_replicas);
      if (!shrinkable.length) return { outcome: 'skipped', decision: { ...decision, reason: 'min_replicas' }, actions: [], plans: [] };
      for (const h of shrinkable) {
        const cool = inCooldown(r, h.deployment_id, now);
        if (cool > 0 && mode === 'dry_run') return { outcome: 'cooldown', decision: { ...decision, cooldown_remaining_seconds: cool }, actions: [], plans: [] };
      }
      const actions: RuleRunAction[] = [];
      const plans: SwapSeed[] = [];
      for (const h of shrinkable) {
        const victim = instancesOf(h.deployment_id).sort((a, b) => (a.traffic_weight ?? 0) - (b.traffic_weight ?? 0))[0];
        const swapId = uuid(`swap:run:${r.id}:${h.deployment_id}:${now}`);
        actions.push({ action: 'scale_down', target_type: 'deployment', target_id: h.deployment_id, target_name: h.deployment, status: actionStatus, swap_operation_id: act ? swapId : null, message: `${act ? 'removing' : 'would remove'} the replica on ${victim?.node_name ?? 'a node'} (${h.replicas} → ${h.replicas - 1})` });
        plans.push({ id: swapId, kind: 'scale_down', deploymentId: h.deployment_id, nodeId: victim?.node_id ?? null, fromInstanceId: victim?.id ?? null, toInstanceId: null, trigger: 'rule', ruleName: r.name, drain: num('drain_timeout_seconds', 120), reason: `max ${h.max_rpm_in_window} rpm < ${below} for ${minutes} min` });
      }
      return met(actions, decision, plans);
    }

    case 'auto_heal': {
      const failures = p.health_failures as number | null;
      const errAbove = p.error_rate_pct_above as number | null;
      const minutes = num('for_minutes', 5);
      const minRequests = num('min_requests_in_window', 10);
      const rows = targets.flatMap((d) =>
        instancesOf(d.id).map((i) => {
          const buckets = windowBuckets(now, minutes);
          const rates = buckets.map((t) => engineErrorRatePct(i.id, t, 60));
          const requests = Math.round(buckets.reduce((s, t) => s + engineRequestsPerMinute(i.id, t), 0));
          const unhealthy = i.health === 'unhealthy';
          const errHit = errAbove !== null && errAbove !== undefined && requests >= minRequests && rates.length > 0 && rates.every((x) => x > errAbove);
          return { instance: `${d.name}@${i.node_name}`, instance_id: i.id, deployment_id: d.id, health: i.health, consecutive_failures: unhealthy ? (failures ?? 3) : 0, error_rate_pct_min: round(Math.min(...rates), 2), error_rate_pct_max: round(Math.max(...rates), 2), requests_in_window: requests, meets: (failures !== null && failures !== undefined && unhealthy) || errHit };
        }),
      );
      const decision = { health_failures_threshold: failures, error_rate_threshold_pct: errAbove, window_minutes: minutes, min_requests_in_window: minRequests, action: p.action, instances: rows.map(publicRow) };
      const hits = rows.filter((x) => x.meets);
      if (!hits.length) return { outcome: 'no_match', decision, actions: [], plans: [] };
      const action = p.action === 'move' ? 'move' : 'restart';
      const actions: RuleRunAction[] = [];
      const plans: SwapSeed[] = [];
      for (const h of hits) {
        const swapId = uuid(`swap:run:${r.id}:${h.instance_id}:${now}`);
        actions.push({ action, target_type: 'instance', target_id: h.instance_id, target_name: h.instance, status: actionStatus, swap_operation_id: act ? swapId : null, message: `${act ? '' : 'would '}${action} (error rate ${h.error_rate_pct_min}–${h.error_rate_pct_max}% over ${minutes} min)` });
        const inst = liveInstances().find((i) => i.id === h.instance_id);
        plans.push({ id: swapId, kind: action, deploymentId: h.deployment_id, nodeId: inst?.node_id ?? null, fromInstanceId: h.instance_id, toInstanceId: h.instance_id, trigger: 'auto_heal', ruleName: r.name, drain: num('drain_timeout_seconds', 30), reason: `error rate above ${errAbove}%` });
      }
      return met(actions, decision, plans);
    }

    case 'make_room_by_priority': {
      const evict = (p.evict_priorities as string[] | undefined) ?? ['evictable'];
      const idleMin = num('victim_idle_minutes', 5);
      const pending = targets.filter((d) => d.status === 'pending' || d.status === 'scheduling');
      const victims = allDeployments().filter((d) => evict.includes(priorityOf(d)) && priorityOf(d) !== 'always_on').map((d) => ({ deployment: d.name, priority: priorityOf(d), idle_minutes: idleMinutes(d, now), eligible: idleMinutes(d, now) >= idleMin, vram_bytes: instancesOf(d.id).reduce((s, i) => s + Number(i.resources?.vram_bytes ?? 0), 0) }));
      return { outcome: pending.length ? 'skipped' : 'no_match', decision: { trigger: p.trigger, pending_deployments: pending.length, evict_priorities: evict, victim_idle_minutes: idleMin, candidate_victims: victims, ...(pending.length ? { reason: 'no_capacity' } : {}) }, actions: [], plans: [] };
    }

    case 'time_schedule': {
      const sp = scheduleParams(r);
      const layout = layouts.find((l) => l.id === sp.layout_id);
      if (!layout) return { outcome: 'error', decision: { layout_id: sp.layout_id }, actions: [], plans: [] };
      const diff = diffLayout(layout);
      const changes = diff.entries.filter((e) => e.action !== 'noop');
      const next = nextOccurrences(sp, now, 1)[0];
      const decision = { layout: layout.name, timezone: sp.timezone, next_scheduled: next ? iso(next) : null, changes: changes.length, feasible: diff.feasible, warnings: diff.warnings ?? [] };
      if (!changes.length) return { outcome: 'no_match', decision: { ...decision, reason: 'layout_already_applied' }, actions: [], plans: [] };
      const actions: RuleRunAction[] = [];
      const plans: SwapSeed[] = [];
      for (const e of changes) {
        const swapId = uuid(`swap:run:${r.id}:${e.deployment_id}:${now}`);
        actions.push({ action: e.action, target_type: 'deployment', target_id: e.deployment_id, target_name: e.deployment_name ?? null, status: actionStatus, swap_operation_id: act ? swapId : null, message: describeEntry(e) });
        plans.push(seedFromEntry(e, swapId, 'layout', r.name, sp.layout_id ?? null, num('drain_timeout_seconds', 300)));
      }
      if (act) applyPlacement(layout);
      return met(actions, decision, plans);
    }
    default:
      return { outcome: 'no_match', decision: {}, actions: [], plans: [] };
  }
}

function recordRun(r: Rule, mode: 'dry_run' | 'run', now = Date.now()): RuleRun {
  const started = performance.now();
  const ev = evaluate(r, now, mode);
  for (const plan of ev.plans) startSwap(plan, now);
  const run = mkRun(r, now, ev.outcome, ev.decision, ev.actions, {
    id: uuid(`run:${r.id}:${now}:${mode}`),
    dry_run: ev.outcome === 'dry_run' || mode === 'dry_run',
    duration_ms: Math.max(4, Math.round(performance.now() - started) + 9),
  });
  runs.push(run);
  if (run.outcome === 'fired') {
    recordAudit({ eventType: 'rule.fired', actorType: 'rule', actorId: r.id, actorName: r.name, resourceType: 'rule', resourceId: r.id, resourceName: r.name, after: { actions: run.actions.map((a) => ({ action: a.action, target: a.target_name })) }, message: 'Run now from the UI', at: now });
  }
  return run;
}

// ─── Layouts and the current placement ────────────────────────────────────────

interface Placement {
  deploymentId: string;
  nodeId: string | null;
  deviceIds: string[];
  replicas: number;
  weight: number;
}

let currentPlacement: Placement[] = liveInstances().map((i) => ({ deploymentId: i.deployment_id, nodeId: i.node_id, deviceIds: i.device_ids ?? [], replicas: 1, weight: i.traffic_weight ?? 100 }));

function item(layoutId: string, idx: number, deploymentId: string, nodeId: string | null, deviceIds: string[], replicas: number, weight = 100, sliceGiB: number | null = null): LayoutItem {
  const d = deploymentById(deploymentId);
  return {
    id: uuid(`layout-item:${layoutId}:${idx}`),
    deployment_id: deploymentId,
    deployment_name: d?.name,
    node_id: nodeId,
    node_name: nodeId ? (nodeById(nodeId)?.name ?? null) : null,
    device_ids: deviceIds,
    replicas,
    slice_vram_bytes: sliceGiB ? sliceGiB * GiB : null,
    slice_compute_pct: sliceGiB ? 100 : null,
    traffic_weight: weight,
    position: idx,
  };
}

const layouts: Layout[] = [
  {
    id: IDS.layouts.day,
    name: 'Day',
    description: 'Office hours: both qwen3 replicas, gemma on the Mac for quick drafts, embeddings on L40S #1.',
    unlisted_policy: 'keep',
    is_active: true,
    applied_at: iso(BOOT - 6 * HOUR - 4 * MIN),
    items: [
      item(IDS.layouts.day, 0, IDS.deployments.qwen, IDS.nodes.gpu, [IDS.devices.l40s0], 1, 70, 24),
      item(IDS.layouts.day, 1, IDS.deployments.qwen, IDS.nodes.mac, [IDS.devices.m5], 1, 30, 3),
      item(IDS.layouts.day, 2, IDS.deployments.gemma, IDS.nodes.mac, [IDS.devices.m5], 1, 100, 4),
      item(IDS.layouts.day, 3, IDS.deployments.nomic, IDS.nodes.gpu, [IDS.devices.l40s1], 1, 100, 8),
    ],
    used_by_rule_ids: [],
    created_at: iso(BOOT - 15 * 24 * HOUR),
    updated_at: iso(BOOT - 3 * 24 * HOUR),
  },
  {
    id: IDS.layouts.night,
    name: 'Night',
    description: 'Lean overnight placement: always-on qwen3 on the L40S box, embeddings for the nightly indexer, the Mac idles.',
    unlisted_policy: 'unload',
    is_active: false,
    applied_at: iso(BOOT - 16 * HOUR),
    items: [
      item(IDS.layouts.night, 0, IDS.deployments.qwen, IDS.nodes.gpu, [IDS.devices.l40s0], 1, 100, 24),
      item(IDS.layouts.night, 1, IDS.deployments.nomic, IDS.nodes.gpu, [IDS.devices.l40s1], 1, 100, 8),
    ],
    used_by_rule_ids: [],
    created_at: iso(BOOT - 15 * 24 * HOUR),
    updated_at: iso(BOOT - 5 * 24 * HOUR),
  },
  {
    id: EXTRA_LAYOUT,
    name: 'Eval burst',
    description: 'For eval runs: gemma moves next to nomic on L40S #1 so the Mac is free for local work.',
    unlisted_policy: 'keep',
    is_active: false,
    applied_at: null,
    items: [
      item(EXTRA_LAYOUT, 0, IDS.deployments.qwen, IDS.nodes.gpu, [IDS.devices.l40s0], 1, 100, 24),
      item(EXTRA_LAYOUT, 1, IDS.deployments.gemma, IDS.nodes.gpu, [IDS.devices.l40s1], 1, 100, 6),
      item(EXTRA_LAYOUT, 2, IDS.deployments.nomic, IDS.nodes.gpu, [IDS.devices.l40s1], 1, 100, 8),
    ],
    used_by_rule_ids: [],
    created_at: iso(BOOT - 2 * 24 * HOUR),
    updated_at: iso(BOOT - 2 * 24 * HOUR),
  },
];

function layoutView(l: Layout): Layout {
  return {
    ...l,
    items: [...l.items].sort((a, b) => a.position - b.position),
    used_by_rule_ids: rules.filter((r) => r.type === 'time_schedule' && scheduleParams(r).layout_id === l.id).map((r) => r.id),
  };
}

function placementKey(nodeId: string | null, deviceIds: string[]): string {
  return `${nodeId ?? '*'}:${[...deviceIds].sort().join('+')}`;
}

function describePlacement(list: { nodeId: string | null; deviceIds: string[]; replicas: number; weight: number }[]) {
  return {
    replicas: list.reduce((s, x) => s + x.replicas, 0),
    placements: list.map((x) => ({
      node: x.nodeId ? (nodeById(x.nodeId)?.name ?? x.nodeId) : 'scheduler',
      devices: x.deviceIds.map(deviceLabel),
      replicas: x.replicas,
      traffic_weight: x.weight,
    })),
  };
}

function describeEntry(e: LayoutDiffEntry): string {
  const from = (e.from as { replicas?: number } | null)?.replicas ?? 0;
  const to = (e.to as { replicas?: number } | null)?.replicas ?? 0;
  switch (e.action) {
    case 'load':
      return `load ${to} replica${to === 1 ? '' : 's'}`;
    case 'unload':
      return `unload ${from} replica${from === 1 ? '' : 's'}`;
    case 'move':
      return 'move to the layout placement';
    case 'scale':
      return `scale ${from} → ${to} replicas`;
    case 'reweight':
      return 'change traffic weights';
    default:
      return 'no change';
  }
}

function diffLayout(l: Layout): LayoutDiff {
  const entries: LayoutDiffEntry[] = [];
  const warnings: string[] = [];
  const byDeployment = new Map<string, LayoutItem[]>();
  for (const it of l.items) byDeployment.set(it.deployment_id, [...(byDeployment.get(it.deployment_id) ?? []), it]);
  const deploymentIds = new Set<string>([...byDeployment.keys(), ...(l.unlisted_policy === 'unload' ? currentPlacement.map((p) => p.deploymentId) : [])]);

  // VRAM feasibility per device.
  const demand = new Map<string, number>();
  for (const it of l.items) {
    if (it.replicas <= 0) continue;
    const bytes = it.slice_vram_bytes ?? instanceVram(it.deployment_id, it.node_id);
    for (const dev of it.device_ids ?? []) demand.set(dev, (demand.get(dev) ?? 0) + (bytes * it.replicas) / Math.max(1, (it.device_ids ?? []).length));
  }
  let feasible = true;
  const rejected: { device_ids: string[]; node_id: string; reason: string }[] = [];
  for (const [dev, bytes] of demand) {
    const device = deviceById(dev);
    if (device && bytes > device.vram_total_bytes) {
      feasible = false;
      rejected.push({ device_ids: [dev], node_id: device.node_id, reason: `needs ${gib(bytes)} of ${gib(device.vram_total_bytes)} on ${deviceLabel(dev)}` });
      warnings.push(`${deviceLabel(dev)} is over-committed: ${gib(bytes)} requested, ${gib(device.vram_total_bytes)} available`);
    }
  }

  let seconds = 0;
  for (const deploymentId of deploymentIds) {
    const d = deploymentById(deploymentId);
    const name = d?.name ?? deploymentId;
    const cur = currentPlacement.filter((p) => p.deploymentId === deploymentId);
    const want = (byDeployment.get(deploymentId) ?? []).filter((it) => it.replicas > 0).map((it) => ({ nodeId: it.node_id ?? null, deviceIds: it.device_ids ?? [], replicas: it.replicas, weight: it.traffic_weight }));
    const curCount = cur.reduce((s, x) => s + x.replicas, 0);
    const wantCount = want.reduce((s, x) => s + x.replicas, 0);
    const isAlwaysOn = d ? priorityOf(d) === 'always_on' : false;
    const from = curCount ? describePlacement(cur) : null;
    const to = wantCount ? describePlacement(want) : null;
    const base = { deployment_id: deploymentId, deployment_name: name, from, to, needs_prefetch_bytes: 0, protected: false };
    if (!byDeployment.has(deploymentId)) {
      if (isAlwaysOn) {
        warnings.push(`${name} is always_on and stays loaded even though the layout unloads unlisted deployments`);
        entries.push({ ...base, action: 'noop', protected: true });
      } else if (curCount > 0) {
        entries.push({ ...base, action: 'unload' });
        seconds += 8;
      }
      continue;
    }
    if (wantCount === 0 && curCount > 0) {
      if (isAlwaysOn) {
        warnings.push(`${name} is always_on — it is kept loaded`);
        entries.push({ ...base, action: 'noop', protected: true });
      } else {
        entries.push({ ...base, action: 'unload' });
        seconds += 8;
      }
      continue;
    }
    if (curCount === 0 && wantCount > 0) {
      entries.push({ ...base, action: 'load', needs_prefetch_bytes: instanceVram(deploymentId) * 0.6, placement: { feasible, placements: [], rejected: rejected.filter((x) => want.some((w) => w.deviceIds.includes(x.device_ids[0]))) } });
      seconds += 25;
      continue;
    }
    const curKeys = new Set(cur.map((x) => placementKey(x.nodeId, x.deviceIds)));
    const wantKeys = new Set(want.map((x) => placementKey(x.nodeId, x.deviceIds)));
    const sameSet = curKeys.size === wantKeys.size && [...curKeys].every((k) => wantKeys.has(k));
    if (sameSet && curCount === wantCount) {
      const sameWeights = want.every((w) => cur.find((c) => placementKey(c.nodeId, c.deviceIds) === placementKey(w.nodeId, w.deviceIds))?.weight === w.weight);
      entries.push({ ...base, action: sameWeights ? 'noop' : 'reweight' });
      if (!sameWeights) seconds += 1;
      continue;
    }
    const overlap = [...wantKeys].some((k) => curKeys.has(k));
    const newNodes = want.filter((w) => !cur.some((c) => c.nodeId === w.nodeId));
    entries.push({
      ...base,
      action: overlap && curCount !== wantCount ? 'scale' : 'move',
      needs_prefetch_bytes: newNodes.length ? instanceVram(deploymentId) * 0.6 : 0,
      placement: { feasible, placements: [], rejected: rejected.filter((x) => want.some((w) => w.deviceIds.includes(x.device_ids[0]))) },
    });
    seconds += overlap ? 22 : 35;
  }
  entries.sort((a, b) => {
    const pa = l.items.find((it) => it.deployment_id === a.deployment_id)?.position ?? 999;
    const pb = l.items.find((it) => it.deployment_id === b.deployment_id)?.position ?? 999;
    return pa - pb;
  });
  return { layout_id: l.id, feasible, estimated_seconds: seconds, warnings, entries };
}

function applyPlacement(l: Layout): void {
  const listed = new Set(l.items.map((it) => it.deployment_id));
  const kept = currentPlacement.filter((p) => {
    if (listed.has(p.deploymentId)) return false;
    if (l.unlisted_policy !== 'unload') return true;
    const d = deploymentById(p.deploymentId);
    return d ? priorityOf(d) === 'always_on' : false;
  });
  const fromLayout = l.items.filter((it) => it.replicas > 0).map((it) => ({ deploymentId: it.deployment_id, nodeId: it.node_id ?? null, deviceIds: it.device_ids ?? [], replicas: it.replicas, weight: it.traffic_weight }));
  // always_on deployments are never unloaded by a layout.
  for (const it of l.items) {
    const d = deploymentById(it.deployment_id);
    if (it.replicas === 0 && d && priorityOf(d) === 'always_on') kept.push(...currentPlacement.filter((p) => p.deploymentId === it.deployment_id));
  }
  currentPlacement = [...kept, ...fromLayout];
  for (const other of layouts) other.is_active = other.id === l.id;
  l.applied_at = iso(Date.now());
}

function layoutFromBody(id: string, body: Partial<Schemas['LayoutCreate']>, base?: Layout): Layout {
  const now = iso(Date.now());
  const items = (body.items ?? base?.items ?? []).map((raw, idx) => {
    const it = raw as Schemas['LayoutItemInput'] & Partial<LayoutItem>;
    const slice = 'slice' in it ? it.slice : null;
    return {
      id: uuid(`layout-item:${id}:${idx}:${now}`),
      deployment_id: it.deployment_id,
      deployment_name: deploymentById(it.deployment_id)?.name,
      node_id: it.node_id ?? null,
      node_name: it.node_id ? (nodeById(it.node_id)?.name ?? null) : null,
      device_ids: it.device_ids ?? [],
      replicas: it.replicas ?? 1,
      slice_vram_bytes: slice?.vram_bytes ?? it.slice_vram_bytes ?? null,
      slice_compute_pct: slice?.compute_pct ?? it.slice_compute_pct ?? null,
      traffic_weight: it.traffic_weight ?? 100,
      position: it.position ?? idx,
    } satisfies LayoutItem;
  });
  return {
    id,
    name: body.name ?? base?.name ?? 'Untitled layout',
    description: body.description !== undefined ? body.description : (base?.description ?? null),
    unlisted_policy: body.unlisted_policy ?? base?.unlisted_policy ?? 'keep',
    is_active: base?.is_active ?? false,
    applied_at: base?.applied_at ?? null,
    items,
    used_by_rule_ids: [],
    created_at: base?.created_at ?? now,
    updated_at: now,
  };
}

// ─── Swaps (live progression) ─────────────────────────────────────────────────

interface SwapSeed {
  id: string;
  kind: SwapOperation['kind'];
  deploymentId: string | null;
  nodeId: string | null;
  fromInstanceId: string | null;
  toInstanceId: string | null;
  trigger: SwapOperation['trigger'];
  ruleName?: string | null;
  ruleRunId?: string | null;
  layoutId?: string | null;
  drain: number;
  reason: string;
  dryRun?: boolean;
  /** Fail the step with this name (then roll back). */
  failStep?: string;
}

interface LiveSwap {
  seed: SwapSeed;
  createdMs: number;
  steps: { name: string; ms: number }[];
  inFlight: number;
  cancelledMs?: number;
}

const STEP_STATE: Record<string, SwapOperation['state']> = {
  plan: 'pending',
  route_off: 'draining',
  drain: 'draining',
  unload: 'unloading',
  load: 'loading',
  health: 'health_check',
  route_on: 'health_check',
};

function stepPlan(kind: SwapOperation['kind'], seedKey: string): { name: string; ms: number }[] {
  const j = (base: number, spread: number, salt: string) => base + (hash32(`${seedKey}:${salt}`) % spread);
  const plan = { name: 'plan', ms: j(500, 700, 'plan') };
  const routeOff = { name: 'route_off', ms: j(200, 300, 'roff') };
  const drain = { name: 'drain', ms: j(7_000, 18_000, 'drain') };
  const unload = { name: 'unload', ms: j(2_500, 3_000, 'unload') };
  const load = { name: 'load', ms: j(9_000, 12_000, 'load') };
  const health = { name: 'health', ms: j(3_000, 5_000, 'health') };
  const routeOn = { name: 'route_on', ms: j(200, 300, 'ron') };
  switch (kind) {
    case 'unload':
    case 'scale_down':
      return [plan, routeOff, drain, unload];
    case 'load':
    case 'scale_up':
      return [plan, load, health, routeOn];
    case 'move':
    case 'swap':
      return [plan, load, health, routeOn, routeOff, drain, unload];
    default:
      return [plan, routeOff, drain, unload, load, health, routeOn];
  }
}

const liveSwaps = new Map<string, LiveSwap>();

function startSwap(seed: SwapSeed, now = Date.now()): LiveSwap {
  const live: LiveSwap = { seed, createdMs: now, steps: stepPlan(seed.kind, seed.id), inFlight: 2 + (hash32(seed.id) % 6) };
  liveSwaps.set(seed.id, live);
  return live;
}

function materialize(live: LiveSwap, now = Date.now()): SwapOperation {
  const { seed } = live;
  const startMs = live.createdMs + 400;
  let t = startMs;
  let state: SwapOperation['state'] = 'pending';
  let current = 0;
  let finished: number | null = null;
  let error: string | null = null;
  let failedAt = -1;
  const steps: SwapStep[] = [];
  const stopAt = live.cancelledMs ?? Infinity;
  for (let i = 0; i < live.steps.length; i += 1) {
    const { name, ms } = live.steps[i];
    const s = t;
    const e = t + ms;
    t = e;
    const commandId = name === 'plan' || name.startsWith('route') ? null : uuid(`cmd:${seed.id}:${name}`);
    if (failedAt >= 0 || (finished !== null && state === 'cancelled')) {
      steps.push({ name, state: 'skipped', started_at: null, finished_at: null, command_id: null, error: null, details: null });
      continue;
    }
    if (stopAt < e && now >= stopAt) {
      // Cancelled while this step was pending or running.
      steps.push({ name, state: stopAt >= s ? 'failed' : 'skipped', started_at: stopAt >= s ? iso(s) : null, finished_at: stopAt >= s ? iso(stopAt) : null, command_id: stopAt >= s ? commandId : null, error: stopAt >= s ? 'cancelled by admin' : null, details: null });
      state = 'cancelled';
      finished = stopAt;
      current = i;
      failedAt = i;
      continue;
    }
    if (now >= e) {
      if (seed.failStep === name) {
        error = name === 'health' ? 'health check failed: GET /_health 503 after 90 s' : `${name} failed: agent reported exit code 1`;
        steps.push({ name, state: 'failed', started_at: iso(s), finished_at: iso(e), command_id: commandId, error, details: null });
        failedAt = i;
        current = i;
        const rollbackDone = e + 3_500;
        state = now >= rollbackDone ? 'rolled_back' : 'rolling_back';
        finished = now >= rollbackDone ? rollbackDone : null;
        continue;
      }
      const details = name === 'drain' ? { in_flight_start: live.inFlight, in_flight: 0, waited_seconds: round(ms / 1000, 1) } : name === 'load' ? { port: 10240 + (hash32(seed.id) % 40) } : null;
      steps.push({ name, state: 'completed', started_at: iso(s), finished_at: iso(e), command_id: commandId, error: null, details });
      current = i + 1;
    } else if (now >= s) {
      const progress = (now - s) / ms;
      const details = name === 'drain' ? { in_flight_start: live.inFlight, in_flight: Math.max(0, Math.ceil(live.inFlight * (1 - progress * 1.15))), waiting: 0, timeout_seconds: seed.drain } : name === 'load' ? { progress_pct: Math.round(progress * 100) } : null;
      steps.push({ name, state: 'running', started_at: iso(s), finished_at: null, command_id: commandId, error: null, details });
      state = STEP_STATE[name] ?? 'pending';
      current = i;
    } else {
      steps.push({ name, state: 'pending', started_at: null, finished_at: null, command_id: null, error: null, details: null });
    }
  }
  if (failedAt < 0 && steps.every((s) => s.state === 'completed')) {
    state = 'completed';
    finished = t;
    current = steps.length;
  }
  const deployment = seed.deploymentId ? deploymentById(seed.deploymentId) : undefined;
  return {
    id: seed.id,
    kind: seed.kind,
    state,
    trigger: seed.trigger,
    rule_run_id: seed.ruleRunId ?? null,
    rule_name: seed.ruleName ?? null,
    layout_id: seed.layoutId ?? null,
    deployment_id: seed.deploymentId,
    deployment_name: deployment?.name ?? null,
    from_instance_id: seed.fromInstanceId,
    to_instance_id: seed.toInstanceId,
    node_id: seed.nodeId,
    plan: { reason: seed.reason, order: seed.kind === 'move' || seed.kind === 'swap' ? 'load_first' : 'drain_first', node: seed.nodeId ? nodeById(seed.nodeId)?.name : null },
    steps,
    current_step: current,
    rollback: failedAt >= 0 && state !== 'cancelled' ? { attempted: true, previous_routing: 'restored', result: state === 'rolled_back' ? 'source instance kept' : 'in progress' } : {},
    drain_timeout_seconds: seed.drain,
    dry_run: seed.dryRun ?? false,
    started_at: now >= startMs ? iso(startMs) : null,
    finished_at: finished ? iso(finished) : null,
    error_message: error,
    created_at: iso(live.createdMs),
    updated_at: iso(Math.min(now, finished ?? now)),
  };
}

/** One swap every 150 s keeps the "live" section alive in demos. */
const CYCLE_MS = 150_000;

const cycleLives = new Map<string, LiveSwap>();

function cycleSwaps(now: number): SwapOperation[] {
  const current = Math.floor(now / CYCLE_MS);
  const first = Math.max(Math.floor(BOOT / CYCLE_MS) - 1, current - 5);
  const out: SwapOperation[] = [];
  for (let c = first; c <= current; c += 1) {
    const id = uuid(`swap:cycle:${c}`);
    let live = cycleLives.get(id);
    if (!live) {
      const even = c % 2 === 0;
      live = {
        seed: even
          ? { id, kind: 'restart', deploymentId: IDS.deployments.nomic, nodeId: IDS.nodes.gpu, fromInstanceId: IDS.instances.nomicL40s, toInstanceId: IDS.instances.nomicL40s, trigger: 'manual', drain: 60, reason: 'manual restart to pick up new launch flags' }
          : { id, kind: 'scale_up', deploymentId: IDS.deployments.qwen, nodeId: IDS.nodes.gpu, fromInstanceId: null, toInstanceId: uuid(`instance:cycle:${c}`), trigger: 'rule', ruleName: 'qwen3 grow on queue', drain: 120, reason: 'waiting requests 6 > 4 for 3 min' },
        createdMs: c * CYCLE_MS + 5_000,
        steps: [],
        inFlight: 3 + (c % 4),
      };
      live.steps = stepPlan(live.seed.kind, id);
      cycleLives.set(id, live);
    }
    if (live.createdMs <= now) out.push(materialize(live, now));
  }
  return out;
}

const staticSwaps: SwapOperation[] = (() => {
  const base = recentSwaps(BOOT);
  const extra: SwapOperation[] = [];
  const hist = (id: string, kind: SwapOperation['kind'], trigger: SwapOperation['trigger'], deploymentId: string, nodeId: string, startMs: number, ruleName: string | null, layoutId: string | null, reason: string) => {
    const live: LiveSwap = { seed: { id, kind, deploymentId, nodeId, fromInstanceId: null, toInstanceId: null, trigger, ruleName, layoutId, drain: 120, reason }, createdMs: startMs, steps: stepPlan(kind, id), inFlight: 2 };
    extra.push(materialize(live, BOOT));
  };
  hist(uuid('swap:hist:shrink'), 'scale_down', 'rule', IDS.deployments.qwen, IDS.nodes.mac, BOOT - 26 * HOUR - 50 * MIN, 'Shrink qwen3 at night', null, 'max 9.4 rpm < 15 for 20 min');
  hist(uuid('swap:hist:idle'), 'unload', 'rule', IDS.deployments.gemma, IDS.nodes.mac, BOOT - 27 * HOUR - 8 * MIN, 'Night idle unload', null, 'idle 34 min ≥ 30 min');
  for (const id of [RULE_IDS.schedule, RULE_IDS.nightSwitch]) {
    const r = ruleById(id) as Rule;
    const p = scheduleParams(r);
    for (const t of occurrencesBetween(p, BOOT - 3 * 24 * HOUR, BOOT - 7 * HOUR)) {
      const day = p.layout_id === IDS.layouts.day;
      hist(uuid(`swap:sched:${id}:${t}`), 'apply_layout', 'layout', IDS.deployments.gemma, IDS.nodes.mac, t + 20_000, r.name, p.layout_id ?? null, day ? 'Day layout: load gemma-3-1b' : 'Night layout: unload gemma-3-1b');
    }
  }
  hist(uuid('swap:hist:bench'), 'load', 'benchmark', IDS.deployments.nomic, IDS.nodes.gpu, BOOT - 50 * HOUR, null, null, 'benchmark warm-up');
  return [...base, ...extra];
})();

const cancelledStatic = new Map<string, SwapOperation>();

function allSwaps(now = Date.now()): SwapOperation[] {
  const user = [...liveSwaps.values()].map((l) => materialize(l, now));
  const list = [...staticSwaps.map((s) => cancelledStatic.get(s.id) ?? s), ...cycleSwaps(now), ...user];
  const seen = new Set<string>();
  return list.filter((s) => (seen.has(s.id) ? false : (seen.add(s.id), true))).sort((a, b) => b.created_at.localeCompare(a.created_at));
}

const TERMINAL = new Set(['completed', 'failed', 'rolled_back', 'cancelled']);

function seedFromEntry(e: LayoutDiffEntry, id: string, trigger: SwapOperation['trigger'], ruleName: string | null, layoutId: string | null, drain: number): SwapSeed {
  const kind: SwapOperation['kind'] = e.action === 'load' ? 'load' : e.action === 'unload' ? 'unload' : e.action === 'move' ? 'move' : e.action === 'scale' ? 'scale_up' : 'apply_layout';
  const placementNode = (e.to as { placements?: { node: string }[] } | null)?.placements?.[0]?.node ?? (e.from as { placements?: { node: string }[] } | null)?.placements?.[0]?.node;
  const node = fleetStore.nodes.find((n) => n.name === placementNode);
  return { id, kind, deploymentId: e.deployment_id, nodeId: node?.id ?? null, fromInstanceId: null, toInstanceId: null, trigger, ruleName, layoutId, drain, reason: describeEntry(e) };
}

// ─── Alert channels and events ────────────────────────────────────────────────

const channels: AlertChannel[] = [
  {
    id: uuid('channel:slack-ops'),
    name: 'Slack #gpu-ops',
    type: 'slack',
    config: { channel: '#gpu-ops', username: 'GPU Deck', mention_on_critical: '@oncall' },
    credential_id: uuid('credential:slack-gpu-ops'),
    enabled: true,
    event_filter: [],
    min_severity: 'info',
    last_test_at: iso(BOOT - 3 * 24 * HOUR),
    last_test_ok: true,
    last_error: null,
    created_at: iso(BOOT - 20 * 24 * HOUR),
    updated_at: iso(BOOT - 3 * 24 * HOUR),
  },
  {
    id: uuid('channel:email-oncall'),
    name: 'Email on-call',
    type: 'email',
    config: { recipients: ['oncall@example.com', 'platform@example.com'], smtp_host: 'smtp.office365.com', smtp_port: 587, from: 'gpudeck@example.com', use_tls: true },
    credential_id: uuid('credential:smtp-password'),
    enabled: true,
    event_filter: ['swap.*', 'heal.*', 'rule.error', 'rule_fired'],
    min_severity: 'warning',
    last_test_at: iso(BOOT - 9 * 24 * HOUR),
    last_test_ok: true,
    last_error: null,
    created_at: iso(BOOT - 20 * 24 * HOUR),
    updated_at: iso(BOOT - 9 * 24 * HOUR),
  },
  {
    id: uuid('channel:webhook-finops'),
    name: 'FinOps webhook',
    type: 'webhook',
    config: { url: 'https://finops.example.com/hooks/gpu-deck', signing: 'hmac-sha256', headers: { 'X-Source': 'gpu-deck' } },
    credential_id: uuid('credential:finops-signing'),
    enabled: true,
    event_filter: ['node.*', 'device.*'],
    min_severity: 'info',
    last_test_at: iso(BOOT - 7 * HOUR),
    last_test_ok: false,
    last_error: 'upstream 503 Service Unavailable after 3 attempts',
    created_at: iso(BOOT - 12 * 24 * HOUR),
    updated_at: iso(BOOT - 7 * HOUR),
  },
];

function delivery(event: string, channel: AlertChannel, status: Schemas['AlertDelivery']['status'], at: number, attempts = 1): Schemas['AlertDelivery'] {
  return {
    id: uuid(`delivery:${event}:${channel.id}`),
    channel_id: channel.id,
    channel_name: channel.name,
    status,
    attempts,
    response_code: status === 'sent' ? (channel.type === 'email' ? 250 : 200) : status === 'failed' ? 503 : null,
    sent_at: status === 'sent' ? iso(at + 900) : null,
    error_message: status === 'failed' ? 'upstream 503 after 3 attempts' : status === 'suppressed' ? 'below channel min_severity' : null,
  };
}

const [slack, email, finops] = channels;

function alert(id: string, severity: AlertEvent['severity'], eventType: string, title: string, message: string, at: number, status: AlertEvent['status'], resource: { type: string; id: string; name: string } | null, deliveries: Schemas['AlertDelivery'][], occurrences = 1): AlertEvent {
  return {
    id,
    severity,
    event_type: eventType,
    title,
    message,
    resource_type: resource?.type ?? null,
    resource_id: resource?.id ?? null,
    resource_name: resource?.name ?? null,
    payload: {},
    dedupe_key: null,
    status,
    occurrences,
    first_seen_at: iso(at),
    last_seen_at: iso(at + (occurrences - 1) * 4 * MIN),
    acknowledged_at: status !== 'open' ? iso(at + 6 * MIN) : null,
    resolved_at: status === 'resolved' ? iso(at + 40 * MIN) : null,
    deliveries,
    created_at: iso(at),
  };
}

const events: AlertEvent[] = (() => {
  const list = [...recentAlerts(BOOT)];
  const t = (h: number, m = 0) => BOOT - h * HOUR - m * MIN;
  list.push(
    alert(uuid('alert:hist:dry-grow'), 'info', 'rule.dry_run', 'qwen3 grow on queue would have added a replica', 'Dry run: waiting requests 7 > 4 for 3 minutes on qwen3-0.6b. Nothing was changed.', t(9, 12), 'open', { type: 'rule', id: RULE_IDS.highLoad, name: 'qwen3 grow on queue' }, [delivery('dry-grow', slack, 'sent', t(9, 12)), delivery('dry-grow', email, 'suppressed', t(9, 12))]),
    alert(uuid('alert:hist:night'), 'info', 'rule.fired', 'Night layout applied', '2 changes: unloaded gemma-3-1b and the mac-m5-studio qwen3 replica. Freed 7.0 GiB on Apple M5.', t(16), 'resolved', { type: 'layout', id: IDS.layouts.night, name: 'Night' }, [delivery('night', slack, 'sent', t(16))]),
    alert(uuid('alert:hist:heal-err'), 'warning', 'rule.error', 'Auto heal could not evaluate', 'metric query timed out after 5 s (metric_rollups_engine_1m). The next tick succeeded.', t(44, 3), 'resolved', { type: 'rule', id: RULE_IDS.autoHeal, name: 'Auto heal' }, [delivery('heal-err', slack, 'sent', t(44, 3)), delivery('heal-err', email, 'sent', t(44, 3))]),
    alert(uuid('alert:hist:node-offline'), 'critical', 'node.offline', 'mac-m5-studio stopped sending heartbeats', 'No heartbeat for 90 s; 2 instances unreachable. The agent reconnected 3 minutes later.', t(52, 40), 'resolved', { type: 'node', id: IDS.nodes.mac, name: 'mac-m5-studio' }, [delivery('node-offline', slack, 'sent', t(52, 40)), delivery('node-offline', email, 'sent', t(52, 40))], 2),
    alert(uuid('alert:hist:dev-temp'), 'warning', 'device.unhealthy', 'L40S #0 throttling at 86 °C', 'gpu-box-01 L40S #0 reported clock throttling (thermal) for 4 minutes during a benchmark.', t(50, 2), 'resolved', { type: 'device', id: IDS.devices.l40s0, name: 'gpu-box-01 / L40S #0' }, [delivery('dev-temp', slack, 'sent', t(50, 2)), delivery('dev-temp', email, 'failed', t(50, 2), 3)]),
    alert(uuid('alert:hist:node-drained'), 'info', 'node.drained', 'gpu-box-01 drained for maintenance', 'All instances moved to the Mac; the node accepts no placements until it is undrained.', t(5 * 24 + 3), 'resolved', { type: 'node', id: IDS.nodes.gpu, name: 'gpu-box-01' }, [delivery('node-drained', finops, 'sent', t(5 * 24 + 3))]),
  );
  return list;
})();

// ─── Route helpers ────────────────────────────────────────────────────────────

function filterRuns(list: RuleRun[], q: URLSearchParams): RuleRun[] {
  const outcome = q.get('outcome');
  const fired = q.get('fired');
  const from = q.get('from');
  const to = q.get('to');
  const type = q.get('rule_type');
  const ruleId = q.get('rule_id');
  const deploymentId = q.get('deployment_id');
  return list
    .filter((r) => !outcome || r.outcome === outcome)
    .filter((r) => fired === null || String(r.fired) === fired)
    .filter((r) => !from || r.evaluated_at >= from)
    .filter((r) => !to || r.evaluated_at <= to)
    .filter((r) => !type || r.rule_type === type)
    .filter((r) => !ruleId || r.rule_id === ruleId)
    .filter((r) => !deploymentId || r.actions.some((a) => a.target_id === deploymentId) || JSON.stringify(r.decision).includes(deploymentById(deploymentId)?.name ?? '§'))
    .sort((a, b) => b.evaluated_at.localeCompare(a.evaluated_at));
}

function validateRuleBody(body: Partial<Schemas['RuleCreate']>): string | null {
  if (body.name !== undefined && !String(body.name).trim()) return 'name must not be empty';
  if (body.type !== undefined && !['idle_unload', 'time_schedule', 'low_traffic_shrink', 'high_load_grow', 'make_room_by_priority', 'auto_heal'].includes(body.type)) return 'unknown rule type';
  if (body.cooldown_seconds !== undefined && (body.cooldown_seconds < 0 || body.cooldown_seconds > 86_400)) return 'cooldown_seconds must be 0–86400';
  if (body.type === 'time_schedule' && body.params && !layouts.some((l) => l.id === (body.params as { layout_id?: string }).layout_id)) return 'params.layout_id: layout not found';
  if (body.type === 'high_load_grow' && body.params) {
    const p = body.params as { waiting_requests_above?: unknown; kv_cache_pct_above?: unknown };
    if (p.waiting_requests_above === null && p.kv_cache_pct_above === null) return 'params: set waiting_requests_above or kv_cache_pct_above';
  }
  return null;
}

function withRule(req: MockRequest, fn: (r: Rule) => MockResult): MockResult {
  const r = ruleById(req.params.rule_id);
  return r ? fn(r) : notFound('Rule');
}

function filterSwaps(list: SwapOperation[], q: URLSearchParams): SwapOperation[] {
  const state = q.get('state');
  const kind = q.get('kind');
  const trigger = q.get('trigger');
  const deploymentId = q.get('deployment_id');
  const nodeId = q.get('node_id');
  const activeOnly = q.get('active_only') === 'true';
  const from = q.get('from');
  const to = q.get('to');
  return list
    .filter((s) => !state || s.state === state)
    .filter((s) => !kind || s.kind === kind)
    .filter((s) => !trigger || s.trigger === trigger)
    .filter((s) => !deploymentId || s.deployment_id === deploymentId)
    .filter((s) => !nodeId || s.node_id === nodeId)
    .filter((s) => !activeOnly || !TERMINAL.has(s.state))
    .filter((s) => !from || s.created_at >= from)
    .filter((s) => !to || s.created_at <= to);
}

function findSwap(id: string): SwapOperation | undefined {
  return allSwaps().find((s) => s.id === id);
}

function channelTest(c: AlertChannel): Schemas['AlertTestResult'] {
  const okResult = !(c.type === 'webhook' && String((c.config as { url?: string }).url ?? '').includes('finops'));
  const now = Date.now();
  c.last_test_at = iso(now);
  c.last_test_ok = okResult;
  c.last_error = okResult ? null : 'upstream 503 Service Unavailable after 3 attempts';
  return {
    ok: okResult,
    latency_ms: okResult ? 120 + (hash32(`${c.id}:${now}`) % 400) : 10_000,
    response_code: okResult ? (c.type === 'email' ? 250 : 200) : 503,
    error: c.last_error,
    sent_at: iso(now),
  };
}

// ─── Handlers ─────────────────────────────────────────────────────────────────

const RULE_SCHEMAS = {
  rule: ruleSchema,
  target_selector: targetSelectorSchema,
  layout: layoutSchema,
  params: {
    idle_unload: idleUnloadSchema,
    time_schedule: timeScheduleSchema,
    low_traffic_shrink: lowTrafficShrinkSchema,
    high_load_grow: highLoadGrowSchema,
    make_room_by_priority: makeRoomSchema,
    auto_heal: autoHealSchema,
  },
};

registerMockHandlers('automation', [
  // ── Rules ──
  { method: 'GET', path: '/rules/schemas', handle: () => ok(RULE_SCHEMAS) },
  {
    method: 'GET',
    path: '/rules',
    handle: (req) => {
      const q = req.query;
      const search = (q.get('search') ?? '').toLowerCase();
      const type = q.get('type');
      const enabled = q.get('enabled');
      const now = Date.now();
      const list = rules
        .map((r) => ruleView(r, now))
        .filter((r) => !search || r.name.toLowerCase().includes(search) || (r.description ?? '').toLowerCase().includes(search))
        .filter((r) => !type || r.type === type)
        .filter((r) => enabled === null || String(r.enabled) === enabled);
      return paginate(sortBy(list as unknown as Record<string, unknown>[], q.get('sort'), 'priority') as unknown as Rule[], q);
    },
  },
  {
    method: 'POST',
    path: '/rules',
    handle: (req) => {
      const body = bodyOf<Schemas['RuleCreate']>(req);
      if (!body.name || !body.type || !body.params) return fail(422, 'VALIDATION_ERROR', 'name, type and params are required');
      const problem = validateRuleBody(body);
      if (problem) return fail(422, 'VALIDATION_ERROR', problem);
      if (rules.some((r) => r.name.toLowerCase() === String(body.name).toLowerCase())) return fail(409, 'CONFLICT', `A rule named "${body.name}" already exists`);
      const now = Date.now();
      const r: Rule = {
        id: uuid(`rule:new:${body.name}:${now}`),
        name: body.name,
        description: body.description ?? null,
        type: body.type,
        params: body.params,
        target: body.target ?? {},
        enabled: body.enabled ?? true,
        dry_run: body.dry_run ?? false,
        cooldown_seconds: body.cooldown_seconds ?? 300,
        evaluation_interval_seconds: body.evaluation_interval_seconds ?? 30,
        priority: body.priority ?? 100,
        created_at: iso(now),
        updated_at: iso(now),
        fire_count: 0,
      };
      rules.push(r);
      recordAudit({ eventType: 'rule.created', resourceType: 'rule', resourceId: r.id, resourceName: r.name, after: { type: r.type, enabled: r.enabled, dry_run: r.dry_run, params: r.params, target: r.target } });
      return created(ruleView(r));
    },
  },
  { method: 'GET', path: '/rules/:rule_id', handle: (req) => withRule(req, (r) => ok(ruleView(r))) },
  {
    method: 'PATCH',
    path: '/rules/:rule_id',
    handle: (req) =>
      withRule(req, (r) => {
        const body = bodyOf<Schemas['RuleUpdate']>(req) as Partial<Schemas['RuleCreate']>;
        const problem = validateRuleBody({ type: r.type, ...body });
        if (problem) return fail(422, 'VALIDATION_ERROR', problem);
        if (body.enabled === false && r.enabled) disabledAt.set(r.id, Date.now());
        const before = Object.fromEntries(Object.keys(body).map((k) => [k, (r as Record<string, unknown>)[k]]));
        Object.assign(r, body, { updated_at: iso(Date.now()) });
        recordAudit({ eventType: 'rule.updated', resourceType: 'rule', resourceId: r.id, resourceName: r.name, before, after: body as Record<string, unknown> });
        return ok(ruleView(r));
      }),
  },
  {
    method: 'DELETE',
    path: '/rules/:rule_id',
    handle: (req) =>
      withRule(req, (r) => {
        rules.splice(rules.indexOf(r), 1);
        recordAudit({ eventType: 'rule.deleted', resourceType: 'rule', resourceId: r.id, resourceName: r.name, before: { type: r.type, params: r.params } });
        return noContent();
      }),
  },
  {
    method: 'POST',
    path: '/rules/:rule_id/enable',
    handle: (req) =>
      withRule(req, (r) => {
        r.enabled = true;
        r.updated_at = iso(Date.now());
        disabledAt.delete(r.id);
        recordAudit({ eventType: 'rule.enabled', resourceType: 'rule', resourceId: r.id, resourceName: r.name, before: { enabled: false }, after: { enabled: true } });
        return ok(ruleView(r));
      }),
  },
  {
    method: 'POST',
    path: '/rules/:rule_id/disable',
    handle: (req) =>
      withRule(req, (r) => {
        r.enabled = false;
        r.updated_at = iso(Date.now());
        disabledAt.set(r.id, Date.now());
        recordAudit({ eventType: 'rule.disabled', resourceType: 'rule', resourceId: r.id, resourceName: r.name, before: { enabled: true }, after: { enabled: false } });
        return ok(ruleView(r));
      }),
  },
  { method: 'POST', path: '/rules/:rule_id/dry-run', handle: (req) => withRule(req, (r) => ok(recordRun(r, 'dry_run'))) },
  { method: 'POST', path: '/rules/:rule_id/run', handle: (req) => withRule(req, (r) => ok(recordRun(r, 'run'))) },
  { method: 'GET', path: '/rules/:rule_id/runs', handle: (req) => withRule(req, (r) => paginate(filterRuns(runsFor(r.id), req.query), req.query, 25)) },
  { method: 'GET', path: '/rule-runs', handle: (req) => paginate(filterRuns(runs, req.query), req.query, 25) },
  {
    method: 'GET',
    path: '/rule-runs/:run_id',
    handle: (req) => {
      const run = runs.find((r) => r.id === req.params.run_id);
      return run ? ok(run) : notFound('Rule run');
    },
  },

  // ── Layouts ──
  {
    method: 'GET',
    path: '/layouts',
    handle: (req) => {
      const search = (req.query.get('search') ?? '').toLowerCase();
      const list = layouts.filter((l) => !search || l.name.toLowerCase().includes(search)).map(layoutView);
      return paginate(sortBy(list as unknown as Record<string, unknown>[], req.query.get('sort'), 'created_at') as unknown as Layout[], req.query);
    },
  },
  {
    method: 'POST',
    path: '/layouts',
    handle: (req) => {
      const body = bodyOf<Schemas['LayoutCreate']>(req);
      if (!body.name?.trim()) return fail(422, 'VALIDATION_ERROR', 'name is required');
      if (!body.items?.length) return fail(422, 'VALIDATION_ERROR', 'items: at least one deployment is required');
      if (layouts.some((l) => l.name.toLowerCase() === body.name?.toLowerCase())) return fail(409, 'CONFLICT', `A layout named "${body.name}" already exists`);
      const l = layoutFromBody(uuid(`layout:new:${body.name}:${Date.now()}`), body);
      layouts.push(l);
      recordAudit({ eventType: 'layout.created', resourceType: 'layout', resourceId: l.id, resourceName: l.name, after: { items: l.items.length, unlisted_policy: l.unlisted_policy } });
      return created(layoutView(l));
    },
  },
  {
    method: 'POST',
    path: '/layouts/capture',
    handle: (req) => {
      const body = bodyOf<Schemas['LayoutCaptureRequest']>(req);
      if (!body.name?.trim()) return fail(422, 'VALIDATION_ERROR', 'name is required');
      const items = currentPlacement.map((p, idx) => ({ deployment_id: p.deploymentId, node_id: p.nodeId, device_ids: p.deviceIds, replicas: p.replicas, traffic_weight: p.weight, position: idx, slice: { vram_bytes: instanceVram(p.deploymentId, p.nodeId), compute_pct: 100 } }));
      const l = layoutFromBody(uuid(`layout:capture:${body.name}:${Date.now()}`), { name: body.name, description: body.description ?? 'Captured from the running placement', unlisted_policy: 'keep', items });
      l.is_active = true;
      for (const other of layouts) other.is_active = false;
      layouts.push(l);
      return created(layoutView(l));
    },
  },
  {
    method: 'GET',
    path: '/layouts/:layout_id',
    handle: (req) => {
      const l = layouts.find((x) => x.id === req.params.layout_id);
      return l ? ok(layoutView(l)) : notFound('Layout');
    },
  },
  {
    method: 'PATCH',
    path: '/layouts/:layout_id',
    handle: (req) => {
      const idx = layouts.findIndex((x) => x.id === req.params.layout_id);
      if (idx < 0) return notFound('Layout');
      const body = bodyOf<Schemas['LayoutUpdate']>(req);
      if (body.items && body.items.length === 0) return fail(422, 'VALIDATION_ERROR', 'items: at least one deployment is required');
      const before = { name: layouts[idx].name, items: layouts[idx].items.length, unlisted_policy: layouts[idx].unlisted_policy };
      layouts[idx] = layoutFromBody(layouts[idx].id, body, layouts[idx]);
      recordAudit({ eventType: 'layout.updated', resourceType: 'layout', resourceId: layouts[idx].id, resourceName: layouts[idx].name, before, after: { name: layouts[idx].name, items: layouts[idx].items.length, unlisted_policy: layouts[idx].unlisted_policy } });
      return ok(layoutView(layouts[idx]));
    },
  },
  {
    method: 'DELETE',
    path: '/layouts/:layout_id',
    handle: (req) => {
      const idx = layouts.findIndex((x) => x.id === req.params.layout_id);
      if (idx < 0) return notFound('Layout');
      const users = rules.filter((r) => r.type === 'time_schedule' && scheduleParams(r).layout_id === req.params.layout_id);
      if (users.length) return fail(409, 'CONFLICT', `Used by ${users.map((r) => `"${r.name}"`).join(', ')} — change or delete those rules first`);
      const [removed] = layouts.splice(idx, 1);
      recordAudit({ eventType: 'layout.deleted', resourceType: 'layout', resourceId: removed.id, resourceName: removed.name });
      return noContent();
    },
  },
  {
    method: 'POST',
    path: '/layouts/:layout_id/preview',
    handle: (req) => {
      const l = layouts.find((x) => x.id === req.params.layout_id);
      return l ? ok(diffLayout(l)) : notFound('Layout');
    },
  },
  {
    method: 'POST',
    path: '/layouts/:layout_id/apply',
    handle: (req) => {
      const l = layouts.find((x) => x.id === req.params.layout_id);
      if (!l) return notFound('Layout');
      const body = bodyOf<Schemas['LayoutApplyRequest']>(req);
      const diff = diffLayout(l);
      if (!diff.feasible) return fail(409, 'CONFLICT', diff.warnings?.[0] ?? 'Layout does not fit on the GPUs');
      const now = Date.now();
      const changes = diff.entries.filter((e) => e.action !== 'noop');
      if (body.prefetch_only) {
        const commandIds = changes.filter((e) => e.action === 'load' || e.action === 'move').map((e) => uuid(`cmd:prefetch:${e.deployment_id}:${now}`));
        return ok({ layout_id: l.id, swap_operation_ids: [], command_ids: commandIds, diff });
      }
      const swapIds: string[] = [];
      changes.forEach((e, i) => {
        const id = uuid(`swap:layout:${l.id}:${e.deployment_id}:${now}`);
        startSwap(seedFromEntry(e, id, 'layout', null, l.id, body.drain_timeout_seconds ?? 300), now + i * 1_500);
        swapIds.push(id);
      });
      applyPlacement(l);
      recordAudit({ eventType: 'layout.applied', resourceType: 'layout', resourceId: l.id, resourceName: l.name, after: { swaps: swapIds.length, drain_timeout_seconds: body.drain_timeout_seconds ?? 300 } });
      return ok({ layout_id: l.id, swap_operation_ids: swapIds, command_ids: [], diff });
    },
  },

  // ── Swaps ──
  {
    method: 'GET',
    path: '/swaps',
    handle: (req) => {
      const list = filterSwaps(allSwaps(), req.query);
      const sort = req.query.get('sort');
      return paginate(sort ? (sortBy(list as unknown as Record<string, unknown>[], sort) as unknown as SwapOperation[]) : list, req.query, 25);
    },
  },
  {
    method: 'POST',
    path: '/swaps',
    handle: (req) => {
      const body = bodyOf<Schemas['SwapCreate']>(req);
      const d = body.deployment_id ? deploymentById(body.deployment_id) : undefined;
      if (!d) return fail(422, 'VALIDATION_ERROR', 'deployment_id: deployment not found');
      if (body.kind === 'unload' && priorityOf(d) === 'always_on' && !body.force) return fail(409, 'CONFLICT', `${d.name} is always_on — pass force to unload it`);
      const id = uuid(`swap:manual:${d.id}:${Date.now()}`);
      const inst = body.instance_id ? liveInstances().find((i) => i.id === body.instance_id) : instancesOf(d.id)[0];
      recordAudit({ eventType: 'swap.created', resourceType: 'swap_operation', resourceId: id, resourceName: `${body.kind} ${d.name}`, after: { kind: body.kind, deployment: d.name, force: body.force ?? false, dry_run: body.dry_run ?? false } });
      const live = startSwap({ id, kind: body.kind ?? 'restart', deploymentId: d.id, nodeId: body.node_id ?? inst?.node_id ?? null, fromInstanceId: body.kind === 'load' ? null : (inst?.id ?? null), toInstanceId: body.kind === 'unload' ? null : (inst?.id ?? null), trigger: 'manual', drain: body.drain_timeout_seconds ?? 120, reason: `manual ${body.kind}`, dryRun: body.dry_run });
      return created(materialize(live));
    },
  },
  {
    method: 'GET',
    path: '/swaps/:swap_id',
    handle: (req) => {
      const s = findSwap(req.params.swap_id);
      return s ? ok(s) : notFound('Swap');
    },
  },
  {
    method: 'POST',
    path: '/swaps/:swap_id/cancel',
    handle: (req) => {
      const s = findSwap(req.params.swap_id);
      if (!s) return notFound('Swap');
      if (TERMINAL.has(s.state)) return fail(409, 'CONFLICT', `Swap already ${s.state.replace('_', ' ')}`);
      const live = liveSwaps.get(s.id) ?? cycleLives.get(s.id);
      recordAudit({ eventType: 'swap.cancelled', resourceType: 'swap_operation', resourceId: s.id, resourceName: `${s.kind} ${s.deployment_name ?? ''}`.trim(), before: { state: s.state } });
      if (live) {
        live.cancelledMs = Date.now();
        return ok(materialize(live));
      }
      const cancelled: SwapOperation = { ...s, state: 'cancelled', finished_at: iso(Date.now()) };
      cancelledStatic.set(s.id, cancelled);
      return ok(cancelled);
    },
  },
  {
    method: 'POST',
    path: '/swaps/:swap_id/retry',
    handle: (req) => {
      const s = findSwap(req.params.swap_id);
      if (!s) return notFound('Swap');
      if (!['failed', 'rolled_back', 'cancelled'].includes(s.state)) return fail(409, 'CONFLICT', 'Only failed, rolled back or cancelled swaps can be retried');
      const id = uuid(`swap:retry:${s.id}:${Date.now()}`);
      const live = startSwap({ id, kind: s.kind, deploymentId: s.deployment_id ?? null, nodeId: s.node_id ?? null, fromInstanceId: s.from_instance_id ?? null, toInstanceId: s.to_instance_id ?? null, trigger: 'manual', drain: s.drain_timeout_seconds ?? 120, reason: `retry of ${s.kind.replace('_', ' ')} ${s.id.slice(0, 8)}`, layoutId: s.layout_id ?? null });
      return created(materialize(live));
    },
  },

  // ── Alert channels ──
  {
    method: 'GET',
    path: '/alert-channels',
    handle: (req) => {
      const search = (req.query.get('search') ?? '').toLowerCase();
      const type = req.query.get('type');
      return paginate(channels.filter((c) => (!search || c.name.toLowerCase().includes(search)) && (!type || c.type === type)), req.query);
    },
  },
  {
    method: 'POST',
    path: '/alert-channels',
    handle: (req) => {
      const body = bodyOf<Schemas['AlertChannelCreate']>(req);
      if (!body.name?.trim() || !body.type) return fail(422, 'VALIDATION_ERROR', 'name and type are required');
      const now = Date.now();
      const c: AlertChannel = {
        id: uuid(`channel:new:${body.name}:${now}`),
        name: body.name,
        type: body.type,
        config: body.config ?? {},
        credential_id: body.secret ? uuid(`credential:${body.name}:${now}`) : (body.credential_id ?? null),
        enabled: body.enabled ?? true,
        event_filter: body.event_filter ?? [],
        min_severity: body.min_severity ?? 'warning',
        last_test_at: null,
        last_test_ok: null,
        last_error: null,
        created_at: iso(now),
        updated_at: iso(now),
      };
      channels.push(c);
      recordAudit({ eventType: 'alert_channel.created', resourceType: 'alert_channel', resourceId: c.id, resourceName: c.name, after: { type: c.type, min_severity: c.min_severity, event_filter: c.event_filter, secret: body.secret ? '•••• (stored in the vault)' : null } });
      return created(c);
    },
  },
  {
    method: 'GET',
    path: '/alert-channels/:channel_id',
    handle: (req) => {
      const c = channels.find((x) => x.id === req.params.channel_id);
      return c ? ok(c) : notFound('Alert channel');
    },
  },
  {
    method: 'PATCH',
    path: '/alert-channels/:channel_id',
    handle: (req) => {
      const c = channels.find((x) => x.id === req.params.channel_id);
      if (!c) return notFound('Alert channel');
      const { secret, ...rest } = bodyOf<Schemas['AlertChannelUpdate']>(req);
      const before = Object.fromEntries(Object.keys(rest).map((k) => [k, (c as Record<string, unknown>)[k]]));
      Object.assign(c, rest, { updated_at: iso(Date.now()) });
      recordAudit({ eventType: 'alert_channel.updated', resourceType: 'alert_channel', resourceId: c.id, resourceName: c.name, before, after: { ...rest, ...(secret ? { secret: '•••• (rotated)' } : {}) } });
      if (secret) c.credential_id = uuid(`credential:${c.id}:${Date.now()}`);
      return ok(c);
    },
  },
  {
    method: 'DELETE',
    path: '/alert-channels/:channel_id',
    handle: (req) => {
      const idx = channels.findIndex((x) => x.id === req.params.channel_id);
      if (idx < 0) return notFound('Alert channel');
      const [removed] = channels.splice(idx, 1);
      recordAudit({ eventType: 'alert_channel.deleted', resourceType: 'alert_channel', resourceId: removed.id, resourceName: removed.name });
      return noContent();
    },
  },
  {
    method: 'POST',
    path: '/alert-channels/:channel_id/test',
    handle: (req) => {
      const c = channels.find((x) => x.id === req.params.channel_id);
      return c ? ok(channelTest(c)) : notFound('Alert channel');
    },
  },

  // ── Alert events ──
  {
    method: 'GET',
    path: '/alert-events',
    handle: (req) => {
      const q = req.query;
      const severity = q.get('severity');
      const status = q.get('status');
      const eventType = q.get('event_type');
      const resourceType = q.get('resource_type');
      const resourceId = q.get('resource_id');
      const from = q.get('from');
      const to = q.get('to');
      const list = events
        .filter((e) => !severity || e.severity === severity)
        .filter((e) => !status || e.status === status)
        .filter((e) => !eventType || e.event_type === eventType || (eventType.endsWith('*') && e.event_type.startsWith(eventType.slice(0, -1))))
        .filter((e) => !resourceType || e.resource_type === resourceType)
        .filter((e) => !resourceId || e.resource_id === resourceId)
        .filter((e) => !from || e.last_seen_at >= from)
        .filter((e) => !to || e.first_seen_at <= to)
        .sort((a, b) => b.last_seen_at.localeCompare(a.last_seen_at));
      return paginate(list, q, 25);
    },
  },
  {
    method: 'GET',
    path: '/alert-events/:event_id',
    handle: (req) => {
      const e = events.find((x) => x.id === req.params.event_id);
      return e ? ok(e) : notFound('Alert event');
    },
  },
  {
    method: 'POST',
    path: '/alert-events/:event_id/acknowledge',
    handle: (req) => {
      const e = events.find((x) => x.id === req.params.event_id);
      if (!e) return notFound('Alert event');
      if (e.status === 'open') {
        e.status = 'acknowledged';
        e.acknowledged_at = iso(Date.now());
      }
      return ok(e);
    },
  },
  {
    method: 'POST',
    path: '/alert-events/:event_id/resolve',
    handle: (req) => {
      const e = events.find((x) => x.id === req.params.event_id);
      if (!e) return notFound('Alert event');
      const now = iso(Date.now());
      e.status = 'resolved';
      e.acknowledged_at = e.acknowledged_at ?? now;
      e.resolved_at = now;
      return ok(e);
    },
  },
  {
    method: 'POST',
    path: '/alert-events/:event_id/resend',
    handle: (req) => {
      const e = events.find((x) => x.id === req.params.event_id);
      if (!e) return notFound('Alert event');
      const only = req.query.get('channel_id');
      const now = Date.now();
      const targets = channels.filter((c) => c.enabled && (!only || c.id === only));
      e.deliveries = [
        ...(e.deliveries ?? []).filter((d) => !targets.some((c) => c.id === d.channel_id)),
        ...targets.map((c) => {
          const result = channelTest(c);
          return { id: uuid(`delivery:resend:${e.id}:${c.id}:${now}`), channel_id: c.id, channel_name: c.name, status: result.ok ? ('sent' as const) : ('failed' as const), attempts: result.ok ? 1 : 3, response_code: result.response_code ?? null, sent_at: result.ok ? result.sent_at : null, error_message: result.error ?? null };
        }),
      ];
      return ok(e);
    },
  },
]);

/** Alert channels, read by the vault mock to count credential references. */
export const mockAlertChannels: readonly AlertChannel[] = channels;
