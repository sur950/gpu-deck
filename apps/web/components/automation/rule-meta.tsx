/**
 * Rule type metadata and the plain-language model of a rule: labels, icons,
 * a tiny glyph per trigger, unit formatting, and `ruleSentence()` — the live
 * "what this rule does" sentence shown in the builder, the list and detail.
 */

import { ArrowDownUp, CalendarClock, HeartPulse, Moon, TrendingDown, TrendingUp } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import type { RuleType, TargetSelector } from '@/hooks/api/automation-api';
import { describeDays } from '@/components/automation/schedule';
import { isEmptyTarget } from '@/components/automation/target-match';

export interface RuleTypeMeta {
  type: RuleType;
  label: string;
  icon: LucideIcon;
  /** One line on the type card. */
  tagline: string;
  /** Plain-language explanation (no jargon). */
  description: string;
  /** What it watches → what it does, for the card footer. */
  when: string;
  then: string;
}

export const RULE_TYPE_META: Record<RuleType, RuleTypeMeta> = {
  idle_unload: {
    type: 'idle_unload',
    label: 'Idle unload',
    icon: Moon,
    tagline: 'Free the GPU when nobody is using a model',
    description: 'If a model has had no requests for a while, drain it and unload it so its VRAM goes back to the pool. Always-on models are never touched.',
    when: 'No requests for N minutes',
    then: 'Drain, then unload',
  },
  time_schedule: {
    type: 'time_schedule',
    label: 'Time schedule',
    icon: CalendarClock,
    tagline: 'Switch GPU layouts at set times',
    description: 'At a clock time on chosen days, switch the fleet to a saved layout — for example a lean Night layout at 22:00 and the full Day layout at 08:00. Weights can be prefetched ahead of time.',
    when: 'A clock time in a timezone',
    then: 'Apply a layout',
  },
  low_traffic_shrink: {
    type: 'low_traffic_shrink',
    label: 'Low traffic shrink',
    icon: TrendingDown,
    tagline: 'Give back replicas when traffic is light',
    description: 'When a deployment gets very few requests for a sustained period, remove a replica (never below the floor you set) or move it to a smaller GPU slice.',
    when: 'Under N req/min for M minutes',
    then: 'Remove a replica',
  },
  high_load_grow: {
    type: 'high_load_grow',
    label: 'High load grow',
    icon: TrendingUp,
    tagline: 'Add replicas when requests start queueing',
    description: 'When requests are waiting or the KV cache is nearly full for a few minutes, add a replica on a free GPU — optionally evicting idle lower-priority models to make space.',
    when: 'Queue or KV cache high for M minutes',
    then: 'Add a replica',
  },
  make_room_by_priority: {
    type: 'make_room_by_priority',
    label: 'Make room by priority',
    icon: ArrowDownUp,
    tagline: 'Evict idle low-priority models for important ones',
    description: 'When a more important model cannot be placed because no GPU has enough free memory, unload the lowest-priority idle model on the best GPU, then place it. Always-on models are never evicted.',
    when: 'A higher-priority model needs VRAM',
    then: 'Evict an idle model',
  },
  auto_heal: {
    type: 'auto_heal',
    label: 'Auto heal',
    icon: HeartPulse,
    tagline: 'Restart or move instances that fail',
    description: 'When an instance keeps failing health checks or returns too many errors, restart it in place — and if that keeps happening, move it to another GPU. Healing never reduces capacity.',
    when: 'Health checks fail or errors spike',
    then: 'Restart, then move',
  },
};

/** Small line drawing of each trigger, drawn in `currentColor` (no colour of its own). */
export function RuleGlyph({ type, className }: { type: RuleType; className?: string }) {
  const common = { fill: 'none', stroke: 'currentColor', strokeWidth: 1.5, strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const };
  return (
    <svg viewBox="0 0 64 28" className={className} aria-hidden>
      {type === 'idle_unload' && (
        <>
          <path {...common} d="M2 20 L8 12 L13 17 L18 9 L23 20 L40 20" />
          <path {...common} d="M40 20 L62 20" strokeDasharray="2 3" opacity={0.55} />
          <path {...common} d="M44 5 V23" strokeDasharray="1.5 2.5" opacity={0.7} />
          <circle cx="44" cy="5" r="1.8" fill="currentColor" stroke="none" />
        </>
      )}
      {type === 'time_schedule' && (
        <>
          <rect x="2" y="9" width="28" height="10" rx="2" fill="currentColor" opacity={0.18} />
          <rect x="34" y="9" width="28" height="10" rx="2" fill="currentColor" opacity={0.45} />
          <path {...common} d="M32 4 V24" />
          <circle cx="32" cy="4" r="1.8" fill="currentColor" stroke="none" />
        </>
      )}
      {type === 'low_traffic_shrink' && (
        <>
          <path {...common} d="M2 6 H20 V13 H40 V21 H62" />
          <path {...common} d="M2 24 H62" opacity={0.25} />
        </>
      )}
      {type === 'high_load_grow' && (
        <>
          <path {...common} d="M2 13 H62" strokeDasharray="2 3" opacity={0.55} />
          <path {...common} d="M2 23 L14 20 L24 17 L32 10 L40 8 L50 6 L62 5" />
          <circle cx="32" cy="10" r="1.8" fill="currentColor" stroke="none" />
        </>
      )}
      {type === 'make_room_by_priority' && (
        <>
          <rect x="4" y="8" width="16" height="13" rx="2" fill="currentColor" opacity={0.45} />
          <rect x="24" y="8" width="16" height="13" rx="2" fill="currentColor" opacity={0.18} />
          <rect x="46" y="4" width="14" height="10" rx="2" {...common} strokeDasharray="2 2" />
          <path {...common} d="M42 14.5 L46 11 M42 14.5 L46 18" opacity={0.7} />
        </>
      )}
      {type === 'auto_heal' && <path {...common} d="M2 15 H18 L22 5 L27 24 L31 15 H40 L43 11 L46 15 H62" />}
    </svg>
  );
}

// ─── Units ────────────────────────────────────────────────────────────────────

export function formatMinutes(minutes: number | null | undefined): string {
  if (minutes === null || minutes === undefined || Number.isNaN(minutes)) return '—';
  if (minutes < 60) return `${trim(minutes)} min`;
  const h = Math.floor(minutes / 60);
  const m = Math.round(minutes % 60);
  if (h >= 24 && m === 0 && h % 24 === 0) return `${h / 24} day${h === 24 ? '' : 's'}`;
  return m ? `${h} h ${m} min` : `${h} h`;
}

export function formatSeconds(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || Number.isNaN(seconds)) return '—';
  if (seconds < 60) return `${trim(seconds)} s`;
  if (seconds % 60 === 0) return formatMinutes(seconds / 60);
  const m = Math.floor(seconds / 60);
  return `${m} min ${Math.round(seconds % 60)} s`;
}

function trim(n: number): string {
  return Number.isInteger(n) ? String(n) : n.toFixed(1).replace(/\.0$/, '');
}

export const GiB = 1024 ** 3;

export function plural(n: number, one: string, many = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`;
}

/** "idle_unload" → "Idle unload", "rule.dry_run" → "Rule dry run". */
export function humanize(value: string | null | undefined): string {
  if (!value) return '—';
  const s = value.replace(/[._]+/g, ' ').trim();
  return s.charAt(0).toUpperCase() + s.slice(1);
}

export const PRIORITY_LABELS: Record<string, string> = { always_on: 'always-on', normal: 'normal', evictable: 'evictable' };

export const ENGINE_LABELS: Record<string, string> = {
  llama_cpp: 'llama.cpp',
  vllm: 'vLLM',
  vllm_omni: 'vLLM-Omni',
  sglang: 'SGLang',
  sglang_diffusion: 'SGLang Diffusion',
  tensorrt_llm: 'TensorRT-LLM',
  mlx: 'MLX',
  mlx_audio: 'mlx-audio',
  whisper_cpp: 'whisper.cpp',
  stable_diffusion_cpp: 'stable-diffusion.cpp',
  custom: 'custom',
};

export const MODEL_TYPE_LABELS: Record<string, string> = {
  chat: 'chat',
  thinking: 'thinking',
  vision: 'vision',
  embedding: 'embedding',
  rerank: 'rerank',
  stt: 'speech-to-text',
  tts: 'text-to-speech',
  image: 'image',
  video: 'video',
};

// ─── Sentence ─────────────────────────────────────────────────────────────────

/** A sentence is text with highlighted value tokens; `field` lets the builder link a token to its input. */
export type SentencePart = string | { value: string; field?: string; tone?: 'default' | 'muted' | 'warn' };

export interface RefNames {
  deployments: Map<string, string>;
  models: Map<string, string>;
  nodes: Map<string, string>;
  clusters: Map<string, string>;
  layouts: Map<string, string>;
}

export const EMPTY_REF_NAMES: RefNames = {
  deployments: new Map(),
  models: new Map(),
  nodes: new Map(),
  clusters: new Map(),
  layouts: new Map(),
};

function names(ids: string[] | undefined, lookup: Map<string, string>, noun: string): string {
  const list = (ids ?? []).map((id) => lookup.get(id) ?? `${noun} ${id.slice(0, 8)}`);
  if (list.length <= 2) return list.join(' or ');
  return `${list.slice(0, 2).join(', ')} or ${list.length - 2} more`;
}

function orList(values: string[]): string {
  if (values.length <= 1) return values.join('');
  return `${values.slice(0, -1).join(', ')} or ${values[values.length - 1]}`;
}

/** "every deployment", "qwen3-0.6b", "normal or evictable models on gpu-box-01". */
export function describeTarget(target: TargetSelector | null | undefined, refs: RefNames = EMPTY_REF_NAMES): string {
  const t = target ?? {};
  if (isEmptyTarget(t)) return 'any deployment';
  const subject: string[] = [];
  if (t.deployment_ids?.length) subject.push(names(t.deployment_ids, refs.deployments, 'deployment'));
  const adjectives = [
    t.priorities?.length ? orList(t.priorities.map((p) => PRIORITY_LABELS[p] ?? p)) : '',
    t.model_types?.length ? orList(t.model_types.map((m) => MODEL_TYPE_LABELS[m] ?? m)) : '',
  ].filter(Boolean);
  if (!subject.length) {
    const noun = t.model_ids?.length ? names(t.model_ids, refs.models, 'model') : 'models';
    subject.push(adjectives.length ? `${adjectives.join(' ')} ${noun}` : t.model_ids?.length ? noun : 'deployments');
  } else if (adjectives.length) {
    subject.push(`(${adjectives.join(', ')} only)`);
  }
  const where: string[] = [];
  if (t.engines?.length) where.push(`served by ${orList(t.engines.map((e) => ENGINE_LABELS[e] ?? e))}`);
  if (t.node_ids?.length) where.push(`on ${names(t.node_ids, refs.nodes, 'node')}`);
  if (t.cluster_ids?.length) where.push(`in ${names(t.cluster_ids, refs.clusters, 'cluster')}`);
  if (t.labels && Object.keys(t.labels).length) where.push(`on nodes labelled ${Object.entries(t.labels).map(([k, v]) => `${k}=${v}`).join(', ')}`);
  if (t.name_glob) where.push(`named ${t.name_glob}`);
  if (t.exclude_deployment_ids?.length) where.push(`except ${names(t.exclude_deployment_ids, refs.deployments, 'deployment')}`);
  return [...subject, ...where].join(' ');
}

type P = Record<string, unknown>;
const n = (p: P, k: string): number | null => (typeof p[k] === 'number' ? (p[k] as number) : null);
const s = (p: P, k: string): string | null => (typeof p[k] === 'string' ? (p[k] as string) : null);

/** The live sentence for a rule. Parts with `field` highlight the matching input. */
export function ruleSentence(type: RuleType, params: P, target: TargetSelector | null | undefined, refs: RefNames = EMPTY_REF_NAMES): SentencePart[] {
  const who: SentencePart = { value: describeTarget(target, refs), field: 'target' };
  switch (type) {
    case 'idle_unload': {
      const windows = (params.respect_time_windows as string[] | undefined) ?? [];
      const keep = n(params, 'keep_replicas') ?? 0;
      return [
        'When ',
        who,
        ' has had no requests for ',
        { value: formatMinutes(n(params, 'idle_minutes')), field: 'idle_minutes' },
        ' and nothing is running, drain it and ',
        keep > 0 ? 'unload replicas down to ' : 'unload it',
        ...(keep > 0 ? [{ value: plural(keep, 'replica'), field: 'keep_replicas' }] : []),
        ...(windows.length
          ? [' — only between ', { value: windows.join(', '), field: 'respect_time_windows' }, ' ', { value: s(params, 'timezone') ?? 'UTC', field: 'timezone', tone: 'muted' as const }]
          : []),
        '. Waits ',
        { value: formatMinutes(n(params, 'min_loaded_minutes') ?? 0), field: 'min_loaded_minutes', tone: 'muted' },
        ' after a load before it can unload again.',
      ];
    }
    case 'time_schedule': {
      const layout = s(params, 'layout_id');
      const layoutName = layout ? (refs.layouts.get(layout) ?? 'the chosen') : null;
      const cron = s(params, 'cron');
      const prefetch = n(params, 'prefetch_minutes_before') ?? 0;
      return [
        ...(cron
          ? ['On the cron schedule ', { value: cron, field: 'cron' }]
          : ['At ', { value: s(params, 'at') ?? '00:00', field: 'at' }, ' ', { value: describeDays(params.days as string[] | undefined), field: 'days' }]),
        ' (',
        { value: s(params, 'timezone') ?? 'UTC', field: 'timezone', tone: 'muted' },
        '), switch the fleet to the ',
        layoutName ? { value: layoutName, field: 'layout_id' } : { value: 'pick a layout', field: 'layout_id', tone: 'warn' },
        ' layout',
        ...(prefetch > 0 ? [', prefetching model weights ', { value: formatMinutes(prefetch), field: 'prefetch_minutes_before' }, ' before'] : []),
        '.',
      ];
    }
    case 'low_traffic_shrink': {
      const slice = params.shrink_slice as { vram_bytes?: number } | null | undefined;
      return [
        'When ',
        who,
        ' gets fewer than ',
        { value: `${n(params, 'requests_per_minute_below') ?? 0} req/min`, field: 'requests_per_minute_below' },
        ' for ',
        { value: formatMinutes(n(params, 'for_minutes')), field: 'for_minutes' },
        ', ',
        ...(params.action === 'shrink_slice'
          ? ['move it to a smaller slice of ', { value: slice?.vram_bytes ? `${trim(slice.vram_bytes / GiB)} GiB` : 'pick a size', field: 'shrink_slice', tone: slice?.vram_bytes ? ('default' as const) : ('warn' as const) }]
          : ['remove ', { value: plural(n(params, 'step_replicas') ?? 1, 'replica'), field: 'step_replicas' }, ', never going below ', { value: plural(n(params, 'min_replicas') ?? 1, 'replica'), field: 'min_replicas' }]),
        '.',
      ];
    }
    case 'high_load_grow': {
      const waiting = n(params, 'waiting_requests_above');
      const kv = n(params, 'kv_cache_pct_above');
      const join = params.condition === 'all' ? ' and ' : ' or ';
      const conditions: SentencePart[] = [];
      if (waiting !== null) conditions.push('more than ', { value: plural(waiting, 'waiting request'), field: 'waiting_requests_above' });
      if (waiting !== null && kv !== null) conditions.push({ value: join.trim(), field: 'condition', tone: 'muted' }, ' ');
      if (kv !== null) conditions.push(waiting !== null ? 'KV cache above ' : 'its KV cache above ', { value: `${kv}%`, field: 'kv_cache_pct_above' });
      if (!conditions.length) conditions.push({ value: 'no condition set', field: 'waiting_requests_above', tone: 'warn' });
      if (waiting !== null && kv !== null) conditions.splice(2, 0, ' ');
      return [
        'When ',
        who,
        ' has ',
        ...conditions,
        ' for ',
        { value: formatMinutes(n(params, 'for_minutes')), field: 'for_minutes' },
        ', add ',
        { value: plural(n(params, 'step_replicas') ?? 1, 'replica'), field: 'step_replicas' },
        ' on a free GPU, up to ',
        { value: plural(n(params, 'max_replicas') ?? 1, 'replica'), field: 'max_replicas' },
        ...(params.make_room ? [' — evicting idle lower-priority models if no GPU is free'] : []),
        '.',
      ];
    }
    case 'make_room_by_priority': {
      const evict = (params.evict_priorities as string[] | undefined) ?? ['evictable'];
      const idle = n(params, 'victim_idle_minutes') ?? 0;
      const trigger = s(params, 'trigger');
      return [
        'When a more important model ',
        ...(isEmptyTarget(target) ? [] : ['among ', who, ' ']),
        'cannot be placed for lack of VRAM',
        trigger === 'interval' ? ' (checked on every evaluation)' : trigger === 'placement_failure' ? ' (only when placement fails)' : '',
        ', unload up to ',
        { value: plural(n(params, 'max_evictions_per_run') ?? 1, 'model'), field: 'max_evictions_per_run' },
        ' with priority ',
        { value: orList(evict.map((p) => PRIORITY_LABELS[p] ?? p)), field: 'evict_priorities' },
        idle > 0 ? ' that has been idle for ' : ' (even if busy — it is drained first)',
        ...(idle > 0 ? [{ value: formatMinutes(idle), field: 'victim_idle_minutes' }] : []),
        ' on the best GPU, then place it. Always-on models are never evicted.',
      ];
    }
    case 'auto_heal': {
      const failures = n(params, 'health_failures');
      const err = n(params, 'error_rate_pct_above');
      const action = s(params, 'action') ?? 'restart_then_move';
      const conditions: SentencePart[] = [];
      if (failures !== null) conditions.push('fails ', { value: plural(failures, 'health check'), field: 'health_failures' }, ' in a row');
      if (failures !== null && err !== null) conditions.push(' or ');
      if (err !== null)
        conditions.push(
          'returns more than ',
          { value: `${err}% errors`, field: 'error_rate_pct_above' },
          ' for ',
          { value: formatMinutes(n(params, 'for_minutes')), field: 'for_minutes' },
          ' (at least ',
          { value: `${n(params, 'min_requests_in_window') ?? 10} requests`, field: 'min_requests_in_window', tone: 'muted' },
          ')',
        );
      if (!conditions.length) conditions.push({ value: 'no condition set', field: 'health_failures', tone: 'warn' });
      const actionParts: SentencePart[] =
        action === 'restart'
          ? [{ value: 'restart it in place', field: 'action' }]
          : action === 'move'
            ? [{ value: 'move it to another GPU', field: 'action' }]
            : [{ value: 'restart it', field: 'action' }, ', and move it to another GPU after ', { value: `${n(params, 'max_restarts_per_hour') ?? 3} restarts an hour`, field: 'max_restarts_per_hour' }];
      return ['When an instance of ', who, ' ', ...conditions, ', ', ...actionParts, '.'];
    }
  }
}

/** Behaviour suffix: dry run and cooldown, as sentence parts. */
export function behaviourSentence(opts: { dryRun: boolean; cooldownSeconds: number; type: RuleType }): SentencePart[] {
  const parts: SentencePart[] = [];
  if (opts.dryRun) parts.push({ value: 'Dry run', field: 'dry_run', tone: 'warn' }, ': records what it would do and changes nothing. ');
  if (opts.type !== 'time_schedule' && opts.cooldownSeconds > 0) {
    parts.push('Waits ', { value: formatSeconds(opts.cooldownSeconds), field: 'cooldown_seconds' }, ' before acting on the same deployment again.');
  }
  return parts;
}

export function sentenceText(parts: SentencePart[]): string {
  return parts.map((p) => (typeof p === 'string' ? p : p.value)).join('');
}
