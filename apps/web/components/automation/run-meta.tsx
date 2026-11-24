/**
 * Rule run outcomes (labels, colours, icons), one-line run summaries built
 * from the `decision` payload, and the "next evaluation" estimate.
 */

import { CircleDashed, CircleSlash, FlaskConical, Hourglass, TriangleAlert, Zap } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import type { Rule, RuleRun, RuleRunOutcome } from '@/hooks/api/automation-api';
import type { BadgeVariant } from '@/components/shared/StatusBadge';
import { nextOccurrences } from '@/components/automation/schedule';
import { formatMinutes, humanize } from '@/components/automation/rule-meta';

export interface OutcomeMeta {
  label: string;
  /** Short verb for timelines: "Acted", "Would act". */
  short: string;
  icon: LucideIcon;
  badge: BadgeVariant;
  /** Dot / tick background class. */
  dot: string;
  /** Text colour class. */
  text: string;
  description: string;
}

export const OUTCOME_META: Record<RuleRunOutcome, OutcomeMeta> = {
  fired: {
    label: 'Fired',
    short: 'Acted',
    icon: Zap,
    badge: 'success',
    dot: 'bg-success',
    text: 'text-success-dark dark:text-success-light',
    description: 'The condition held and the actions ran.',
  },
  dry_run: {
    label: 'Dry run',
    short: 'Would act',
    icon: FlaskConical,
    badge: 'info',
    dot: 'bg-info',
    text: 'text-info-dark dark:text-info-light',
    description: 'The condition held; the planned actions were recorded but nothing changed.',
  },
  skipped: {
    label: 'Skipped',
    short: 'Held back',
    icon: CircleSlash,
    badge: 'warning',
    dot: 'bg-warning',
    text: 'text-warning-dark dark:text-warning-light',
    description: 'The condition held, but a protection stopped it (always-on, replica floor, capacity…).',
  },
  cooldown: {
    label: 'Cooldown',
    short: 'Cooling down',
    icon: Hourglass,
    badge: 'default',
    dot: 'bg-chart-4',
    text: 'text-foreground/80',
    description: 'The condition held, but the rule acted on this deployment too recently.',
  },
  no_match: {
    label: 'No match',
    short: 'Nothing to do',
    icon: CircleDashed,
    badge: 'default',
    dot: 'bg-muted-foreground/40',
    text: 'text-muted-foreground',
    description: 'The condition did not hold for any target.',
  },
  error: {
    label: 'Error',
    short: 'Failed',
    icon: TriangleAlert,
    badge: 'error',
    dot: 'bg-error',
    text: 'text-error-dark dark:text-error-light',
    description: 'The evaluator raised an error; nothing changed.',
  },
};

export const OUTCOME_ORDER: RuleRunOutcome[] = ['fired', 'dry_run', 'skipped', 'cooldown', 'no_match', 'error'];

export const REASON_TEXT: Record<string, string> = {
  always_on: 'the target is always-on',
  min_replicas: 'already at the replica floor',
  max_replicas: 'already at the replica ceiling',
  no_capacity: 'no GPU has enough free memory',
  swap_capacity: 'too many swaps already in flight',
  outside_time_window: 'outside the allowed time window',
  drain_in_progress: 'a drain is already in progress',
  node_draining: 'the node is draining',
  layout_already_applied: 'the layout is already in place',
  min_loaded_grace: 'loaded too recently',
};

export function reasonText(reason: unknown): string | null {
  if (typeof reason !== 'string' || !reason) return null;
  return REASON_TEXT[reason] ?? humanize(reason).toLowerCase();
}

function num(v: unknown): number | null {
  return typeof v === 'number' && Number.isFinite(v) ? v : null;
}

type Row = Record<string, unknown>;

/** A one-line, human summary of what a run saw and did. */
export function summarizeRun(run: Pick<RuleRun, 'outcome' | 'decision' | 'actions' | 'errorMessage' | 'ruleType'>): string {
  if (run.outcome === 'error') return run.errorMessage ?? 'Evaluation failed';
  if (run.actions.length) {
    return run.actions.map((a) => [a.targetName, a.message].filter(Boolean).join(': ') || humanize(a.action)).join(' · ');
  }
  const d = run.decision ?? {};
  const reason = reasonText(d.reason);
  if (run.outcome === 'cooldown') {
    const left = num(d.cooldown_remaining_seconds);
    return `Condition held, cooling down${left !== null ? ` for ${formatMinutes(Math.ceil(left / 60))}` : ''}`;
  }
  const targets = (Array.isArray(d.targets) ? d.targets : Array.isArray(d.instances) ? d.instances : []) as Row[];
  const prefix = reason ? `Held back: ${reason}. ` : '';
  switch (run.ruleType) {
    case 'idle_unload': {
      const threshold = num(d.threshold_minutes);
      const top = [...targets].sort((a, b) => (num(b.idle_minutes) ?? 0) - (num(a.idle_minutes) ?? 0))[0];
      if (top) return `${prefix}${String(top.deployment ?? 'busiest target')} idle ${formatMinutes(num(top.idle_minutes))}${threshold !== null ? ` (needs ${formatMinutes(threshold)})` : ''}`;
      break;
    }
    case 'high_load_grow': {
      const top = targets[0];
      if (top) return `${prefix}${String(top.deployment ?? '')}: ${num(top.waiting_avg) ?? 0} waiting, KV ${num(top.kv_cache_pct_max) ?? 0}% · ${num(top.satisfied_minutes) ?? 0}/${num(d.window_minutes) ?? '?'} min over`;
      break;
    }
    case 'low_traffic_shrink': {
      const top = targets[0];
      const rpm = top ? num(top.max_rpm_in_window) : num(d.max_rpm_in_window) ?? num(d.min_rpm_in_window);
      if (rpm !== null) return `${prefix}peak ${rpm} req/min in window (needs < ${num(d.threshold_rpm) ?? '?'})`;
      break;
    }
    case 'auto_heal': {
      const worst = (targets.length ? [...targets].sort((a, b) => (num(b.error_rate_pct_max) ?? 0) - (num(a.error_rate_pct_max) ?? 0))[0] : (d.worst as Row | undefined)) ?? null;
      if (worst) return `${prefix}worst ${String(worst.instance ?? 'instance')} at ${num(worst.error_rate_pct_max) ?? num(worst.error_rate_pct) ?? 0}% errors (limit ${num(d.error_rate_threshold_pct) ?? '?'}%)`;
      break;
    }
    case 'make_room_by_priority': {
      const pending = num(d.pending_deployments);
      if (pending !== null) return `${prefix}${pending === 0 ? 'No model is waiting for VRAM' : `${pending} model(s) waiting for VRAM`}`;
      break;
    }
    case 'time_schedule':
      if (d.layout) return `${prefix}${String(d.layout)} layout${num(d.changes) !== null ? ` · ${num(d.changes)} change(s)` : ''}`;
      break;
  }
  if (reason) return `Held back: ${reason}`;
  return OUTCOME_META[run.outcome].description;
}

/**
 * When the evaluator next looks at this rule (null when disabled or unknown).
 * The API's `next_evaluation_at` wins; a missing or stale value (more than one
 * interval in the past, e.g. a cached list) falls back to the local estimate:
 * the next schedule occurrence for time schedules, otherwise the last
 * evaluation plus the interval.
 */
export function nextEvaluation(
  rule: Pick<Rule, 'enabled' | 'type' | 'params' | 'lastEvaluatedAt' | 'evaluationIntervalSeconds' | 'nextEvaluationAt'>,
  now: number,
): number | null {
  if (!rule.enabled) return null;
  const interval = Math.max(rule.evaluationIntervalSeconds, 5) * 1000;
  const server = rule.nextEvaluationAt ? Date.parse(rule.nextEvaluationAt) : NaN;
  if (Number.isFinite(server) && server >= now - interval) return server;
  if (rule.type === 'time_schedule') {
    return nextOccurrences(rule.params as { at?: string; days?: string[]; cron?: string | null; timezone?: string }, now, 1)[0] ?? null;
  }
  const last = rule.lastEvaluatedAt ? Date.parse(rule.lastEvaluatedAt) : null;
  if (last === null) return now + interval;
  let next = last + interval;
  while (next < now - 1000) next += interval;
  return next;
}

/** "in 12 s", "in 4 min", "in 3 h 10 min", "now". */
export function formatCountdown(ms: number): string {
  if (ms <= 1000) return 'now';
  const s = Math.round(ms / 1000);
  if (s < 60) return `in ${s} s`;
  const m = Math.round(s / 60);
  if (m < 60) return `in ${m} min`;
  const h = Math.floor(m / 60);
  const rest = m % 60;
  if (h < 24) return `in ${h} h${rest ? ` ${rest} min` : ''}`;
  const d = Math.floor(h / 24);
  return `in ${d} day${d === 1 ? '' : 's'}${h % 24 ? ` ${h % 24} h` : ''}`;
}
