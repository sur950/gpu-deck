/**
 * Reading and validating the rule params JSON Schemas
 * (libs/contracts/rules/*.schema.json). The builder renders fields from the
 * schema and validates them here with the same bounds the backend enforces:
 * required, type, nullability, minimum / maximum, pattern, enum, minItems,
 * uniqueItems, the "at least one condition" `anyOf` and the `if / then`
 * requirement.
 */

import type { JsonSchema, RuleType } from '@/hooks/api/automation-api';
import { isValidTimeZone, parseCron } from '@/components/automation/schedule';

export type FieldKind = 'integer' | 'number' | 'string' | 'boolean' | 'array' | 'object' | 'unknown';

export function fieldKind(schema: JsonSchema | undefined): { kind: FieldKind; nullable: boolean } {
  const t = schema?.type;
  const types = Array.isArray(t) ? t : t ? [t] : [];
  const nullable = types.includes('null') || (Array.isArray(schema?.enum) && schema.enum.includes(null));
  const base = types.find((x) => x !== 'null') as FieldKind | undefined;
  return { kind: base ?? (schema?.enum ? 'string' : 'unknown'), nullable };
}

/** "N: idle for (minutes)" → { variable: 'N', label: 'idle for' }. */
export function parseTitle(title: string | undefined): { variable: string | null; label: string } {
  if (!title) return { variable: null, label: '' };
  const m = /^([A-Z]):\s*(.*)$/.exec(title);
  const text = (m ? m[2] : title).replace(/\s*\([^)]*\)\s*$/, '');
  return { variable: m ? m[1] : null, label: text.charAt(0).toUpperCase() + text.slice(1) };
}

function isSet(value: unknown): boolean {
  return value !== undefined && value !== null && value !== '';
}

function requiredMet(branch: JsonSchema, value: Record<string, unknown>): boolean {
  return (branch.required ?? []).every((k) => isSet(value[k]));
}

function branchMatches(branch: JsonSchema, value: Record<string, unknown>): boolean {
  if (!requiredMet(branch, value)) return false;
  for (const [key, prop] of Object.entries(branch.properties ?? {})) {
    if (prop.const !== undefined && value[key] !== prop.const) return false;
  }
  return true;
}

function checkValue(key: string, prop: JsonSchema, value: unknown, required: boolean): string | null {
  const { kind, nullable } = fieldKind(prop);
  if (!isSet(value)) {
    if (value === null && nullable) return null;
    // An explicit null on a non-nullable field fails the backend's schema check even when the
    // field is optional (only a missing key falls back to the default).
    if (value === null && kind !== 'unknown') return required ? 'Required' : 'Invalid value';
    return required ? 'Required' : null;
  }
  switch (kind) {
    case 'integer':
    case 'number': {
      if (typeof value !== 'number' || Number.isNaN(value)) return 'Enter a number';
      if (kind === 'integer' && !Number.isInteger(value)) return 'Whole numbers only';
      if (prop.minimum !== undefined && value < prop.minimum) return prop.maximum !== undefined ? `Between ${prop.minimum} and ${prop.maximum}` : `At least ${prop.minimum}`;
      if (prop.maximum !== undefined && value > prop.maximum) return prop.minimum !== undefined ? `Between ${prop.minimum} and ${prop.maximum}` : `At most ${prop.maximum}`;
      return null;
    }
    case 'string': {
      if (typeof value !== 'string') return 'Invalid value';
      if (prop.enum && !prop.enum.includes(value)) return 'Pick one of the options';
      if (prop.pattern && !new RegExp(prop.pattern).test(value)) return key === 'at' ? 'Use 24-hour HH:MM' : 'Invalid format';
      if (key === 'timezone' && !isValidTimeZone(value)) return 'Unknown timezone';
      if (key === 'cron' && !parseCron(value)) return 'Use 5 fields: minute hour day month weekday';
      return null;
    }
    case 'array': {
      if (!Array.isArray(value)) return 'Invalid list';
      if (prop.minItems !== undefined && value.length < prop.minItems) return prop.minItems === 1 ? 'Pick at least one' : `Pick at least ${prop.minItems}`;
      const items = prop.items;
      if (items?.pattern) {
        const re = new RegExp(items.pattern);
        if (value.some((v) => typeof v !== 'string' || !re.test(v))) return key === 'respect_time_windows' ? 'Each window is HH:MM-HH:MM' : 'Invalid entry';
      }
      if (items?.enum && value.some((v) => !items.enum?.includes(v))) return 'Invalid choice';
      if (prop.uniqueItems && new Set(value.map((v) => JSON.stringify(v))).size !== value.length) return 'Each entry only once';
      return null;
    }
    case 'boolean':
      return typeof value === 'boolean' ? null : 'Invalid value';
    case 'object': {
      if (typeof value !== 'object') return 'Invalid value';
      const obj = value as Record<string, unknown>;
      for (const [k, sub] of Object.entries(prop.properties ?? {})) {
        const err = checkValue(k, sub, obj[k], (prop.required ?? []).includes(k));
        if (err) return `${k === 'vram_bytes' ? 'Size' : k === 'compute_pct' ? 'Compute share' : k}: ${err}`;
      }
      return null;
    }
    default:
      return null;
  }
}

/** Field → message for every problem; `_form` carries cross-field errors. */
export function validateParams(schema: JsonSchema | undefined, params: Record<string, unknown>): Record<string, string> {
  const errors: Record<string, string> = {};
  if (!schema?.properties) return errors;
  const required = new Set(schema.required ?? []);
  for (const [key, prop] of Object.entries(schema.properties)) {
    const err = checkValue(key, prop, params[key], required.has(key));
    if (err) errors[key] = err;
  }
  if (schema.anyOf?.length && !schema.anyOf.some((branch) => requiredMet(branch, params))) {
    const keys = schema.anyOf.flatMap((b) => b.required ?? []);
    errors._form = 'Turn on at least one condition';
    for (const k of keys) errors[k] = errors[k] ?? 'Turn on at least one condition';
  }
  if (schema.if && schema.then && branchMatches(schema.if, params)) {
    for (const k of schema.then.required ?? []) {
      if (!isSet(params[k])) errors[k] = 'Required for this action';
    }
  }
  return errors;
}

/** Contract defaults for a params schema (`default`, then `const`). */
export function defaultsFor(schema: JsonSchema | undefined): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [key, prop] of Object.entries(schema?.properties ?? {})) {
    if (prop.default !== undefined) out[key] = Array.isArray(prop.default) ? [...prop.default] : prop.default;
    else if (prop.const !== undefined) out[key] = prop.const;
  }
  return out;
}

/** Drop keys the schema does not know (additionalProperties: false). */
export function pickKnown(schema: JsonSchema | undefined, params: Record<string, unknown>): Record<string, unknown> {
  const props = schema?.properties;
  if (!props) return params;
  return Object.fromEntries(Object.entries(params).filter(([k]) => k in props));
}

/** Which fields lead the form for each type; anything else in the schema lands in "More options". */
export const FIELD_LAYOUT: Record<RuleType, { primary: string[]; secondary: string[] }> = {
  idle_unload: { primary: ['idle_minutes', 'keep_replicas'], secondary: ['respect_time_windows', 'timezone', 'min_loaded_minutes'] },
  time_schedule: { primary: ['layout_id', 'at', 'days', 'timezone'], secondary: ['prefetch_minutes_before', 'catch_up'] },
  low_traffic_shrink: { primary: ['requests_per_minute_below', 'for_minutes', 'action'], secondary: ['step_replicas', 'min_replicas', 'shrink_slice'] },
  high_load_grow: { primary: ['waiting_requests_above', 'kv_cache_pct_above', 'condition', 'for_minutes'], secondary: ['step_replicas', 'max_replicas', 'make_room'] },
  make_room_by_priority: { primary: ['evict_priorities', 'victim_idle_minutes', 'max_evictions_per_run'], secondary: ['trigger', 'protect_always_on'] },
  auto_heal: { primary: ['health_failures', 'error_rate_pct_above', 'for_minutes', 'action'], secondary: ['min_requests_in_window', 'max_restarts_per_hour', 'exclude_failed_device'] },
};

export interface FieldCopy {
  label?: string;
  help?: string;
  unit?: string;
  /** Labels for enum values (key `null` for the null option). */
  options?: Record<string, string>;
  /** One-click values shown under the input. */
  presets?: number[];
  /** Show as GiB (stored as bytes). */
  bytes?: boolean;
  /** Toggle label for nullable conditions. */
  toggle?: string;
}

export const FIELD_COPY: Record<string, FieldCopy> = {
  idle_minutes: { label: 'Idle for', unit: 'min', help: 'Minutes since the deployment last served a request.', presets: [10, 30, 60, 240] },
  min_loaded_minutes: { label: 'Grace after a load', unit: 'min', help: 'Never unload an instance that became ready less than this long ago.' },
  keep_replicas: { label: 'Keep running', unit: 'replicas', help: '0 unloads every replica.' },
  drain_timeout_seconds: { label: 'Drain timeout', unit: 's', help: 'How long to wait for in-flight requests before giving up. The swap rolls back instead of cutting requests off.' },
  respect_time_windows: { label: 'Only during', help: 'Windows in the rule timezone; they may cross midnight. Leave empty to allow any time.' },
  timezone: { label: 'Timezone', help: 'IANA zone — schedules follow daylight-saving changes.' },
  at: { label: 'At', help: '24-hour clock in the timezone below.' },
  days: { label: 'On' },
  cron: { label: 'Cron expression', help: 'Five fields: minute hour day-of-month month weekday. When set it replaces At / On.' },
  layout_id: { label: 'Switch to layout' },
  prefetch_minutes_before: { label: 'Prefetch weights', unit: 'min before', help: 'Download and pin model files ahead of the switch so loads are quick. 0 = off.', presets: [0, 5, 15, 30] },
  catch_up: { label: 'Catch up missed runs', help: 'If the scheduler was down at the time, apply the layout when it starts (unless the next run is under an hour away).' },
  requests_per_minute_below: { label: 'Fewer than', unit: 'req/min', help: 'Summed over the deployment’s replicas; every minute of the window must be under it.' },
  for_minutes: { label: 'For', unit: 'min', help: 'Every minute in this window must meet the condition.', presets: [2, 5, 15, 30] },
  action: {
    label: 'Action',
    options: { remove_replica: 'Remove a replica', shrink_slice: 'Move to a smaller slice', restart: 'Restart in place', move: 'Move to another GPU', restart_then_move: 'Restart, then move' },
  },
  step_replicas: { label: 'Replicas per step', unit: 'replicas' },
  min_replicas: { label: 'Never go below', unit: 'replicas', help: 'Always-on models keep at least one replica regardless.' },
  shrink_slice: { label: 'Smaller slice', help: 'VRAM (and optional compute share) of the slice to move to.' },
  waiting_requests_above: { label: 'Waiting requests above', unit: 'requests', toggle: 'Queue', help: 'Requests queued for a slot, summed over replicas.' },
  kv_cache_pct_above: { label: 'KV cache above', unit: '%', toggle: 'KV cache', help: 'Highest KV cache use across replicas.' },
  condition: { label: 'Fire when', options: { any: 'Either is true', all: 'Both are true' } },
  max_replicas: { label: 'Never exceed', unit: 'replicas' },
  placement_strategy: { label: 'Placement', options: { null: 'Deployment default', spread: 'Spread across GPUs', pack: 'Pack onto fewest GPUs' } },
  move_placement_strategy: { label: 'Placement when moving', options: { null: 'Deployment default', spread: 'Spread across GPUs', pack: 'Pack onto fewest GPUs' } },
  min_free_vram_bytes: { label: 'Needs free VRAM', unit: 'GiB', bytes: true, toggle: 'Override', help: 'Off = the scheduler estimates from model size and context.' },
  make_room: { label: 'Make room if no GPU is free', help: 'Evict idle lower-priority models (never always-on) to fit the new replica.' },
  evict_priorities: { label: 'May evict', options: { evictable: 'Evictable', normal: 'Normal' }, help: 'Always-on models can never be evicted. A victim must also rank below the model that needs room.' },
  victim_idle_minutes: { label: 'Victim idle for', unit: 'min', help: '0 allows evicting a busy model — it is drained first.' },
  max_evictions_per_run: { label: 'Max evictions per fire', unit: 'models' },
  trigger: { label: 'Check', options: { both: 'On failure and every evaluation', placement_failure: 'Only when placement fails', interval: 'Every evaluation' } },
  protect_always_on: { label: 'Always-on models are protected', help: 'A guarantee of the engine, shown so it is visible here.' },
  health_failures: { label: 'Failed health checks in a row', unit: 'checks', toggle: 'Health', presets: [2, 3, 5] },
  error_rate_pct_above: { label: 'Error rate above', unit: '%', toggle: 'Errors' },
  min_requests_in_window: { label: 'Ignore below', unit: 'requests', help: 'Error rate is not judged on fewer requests than this.' },
  max_restarts_per_hour: { label: 'Restarts per hour before moving', unit: 'restarts' },
  exclude_failed_device: { label: 'Avoid the failed GPU when moving' },
};
