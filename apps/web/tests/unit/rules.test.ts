import { describe, expect, it } from 'vitest';
import { behaviourSentence, describeTarget, EMPTY_REF_NAMES, ruleSentence, sentenceText } from '@/components/automation/rule-meta';
import { validateParams } from '@/components/automation/schema-utils';
import { BUNDLED_RULE_SCHEMAS, schemaDefaults } from '@/hooks/api/automation-schemas';
import { nextEvaluation } from '@/components/automation/run-meta';
import { RULE_TYPES } from '@/hooks/api/automation-api';

const schemas = BUNDLED_RULE_SCHEMAS.params;

describe('rule sentence', () => {
  it('reads an idle-unload rule as one sentence with linked tokens', () => {
    const parts = ruleSentence('idle_unload', { idle_minutes: 30, keep_replicas: 0, min_loaded_minutes: 10 }, {});
    expect(sentenceText(parts)).toBe(
      'When any deployment has had no requests for 30 min and nothing is running, drain it and unload it. Waits 10 min after a load before it can unload again.',
    );
    expect(parts).toContainEqual({ value: '30 min', field: 'idle_minutes' });
  });

  it('names the target from the reference lists', () => {
    const refs = { ...EMPTY_REF_NAMES, deployments: new Map([['d1', 'qwen3-8b']]), nodes: new Map([['n1', 'gpu-box-01']]) };
    expect(describeTarget({ deployment_ids: ['d1'], node_ids: ['n1'] }, refs)).toBe('qwen3-8b on gpu-box-01');
    expect(describeTarget({ priorities: ['evictable'], name_glob: 'qwen*' })).toBe('evictable models named qwen*');
  });

  it('flags a missing layout on a time schedule', () => {
    const parts = ruleSentence('time_schedule', { at: '08:00', days: ['mon', 'tue', 'wed', 'thu', 'fri'], timezone: 'Asia/Kolkata' }, {});
    expect(sentenceText(parts)).toBe('At 08:00 on weekdays (Asia/Kolkata), switch the fleet to the pick a layout layout.');
    expect(parts).toContainEqual({ value: 'pick a layout', field: 'layout_id', tone: 'warn' });
  });

  it('produces a sentence for every rule type from schema defaults', () => {
    for (const type of RULE_TYPES) {
      const text = sentenceText(ruleSentence(type, schemaDefaults(schemas[type]), {}));
      expect(text.length).toBeGreaterThan(20);
      expect(text.endsWith('.')).toBe(true);
    }
  });

  it('adds dry-run and cooldown behaviour', () => {
    expect(sentenceText(behaviourSentence({ dryRun: true, cooldownSeconds: 300, type: 'idle_unload' }))).toBe(
      'Dry run: records what it would do and changes nothing. Waits 5 min before acting on the same deployment again.',
    );
    expect(behaviourSentence({ dryRun: false, cooldownSeconds: 300, type: 'time_schedule' })).toEqual([]);
  });
});

describe('rule params validation (contract schemas)', () => {
  it('accepts the schema defaults for every type that has no required-without-default field', () => {
    expect(validateParams(schemas.idle_unload, schemaDefaults(schemas.idle_unload))).toEqual({});
  });

  it('reports range and type problems per field', () => {
    const errors = validateParams(schemas.idle_unload, { idle_minutes: 0, keep_replicas: 999 });
    expect(Object.keys(errors).sort()).toEqual(['idle_minutes', 'keep_replicas']);
  });

  it('requires a missing required field', () => {
    expect(validateParams(schemas.idle_unload, {})).toHaveProperty('idle_minutes');
  });

  it('requires at least one condition for high-load grow', () => {
    const errors = validateParams(schemas.high_load_grow, { ...schemaDefaults(schemas.high_load_grow), waiting_requests_above: null, kv_cache_pct_above: null });
    expect(errors._form).toBe('Turn on at least one condition');
  });

  it('rejects what the backend schema rejects: null on non-nullable, duplicates, non-booleans', () => {
    const idle = schemaDefaults(schemas.idle_unload);
    expect(validateParams(schemas.idle_unload, { ...idle, keep_replicas: null })).toHaveProperty('keep_replicas');
    expect(validateParams(schemas.idle_unload, { ...idle, timezone: null })).toHaveProperty('timezone');
    const room = schemaDefaults(schemas.make_room_by_priority);
    expect(validateParams(schemas.make_room_by_priority, { ...room, evict_priorities: ['evictable', 'evictable'] })).toHaveProperty('evict_priorities');
    const grow = schemaDefaults(schemas.high_load_grow);
    expect(validateParams(schemas.high_load_grow, { ...grow, make_room: 1 })).toHaveProperty('make_room');
    // Nullable fields still accept null, and a missing optional key falls back to the default.
    expect(validateParams(schemas.high_load_grow, { ...grow, placement_strategy: null })).toEqual({});
    const withoutKeep = { ...idle };
    delete withoutKeep.keep_replicas;
    expect(validateParams(schemas.idle_unload, withoutKeep)).toEqual({});
  });
});

describe('next evaluation', () => {
  const now = Date.parse('2026-11-17T12:00:00Z');
  const base = { enabled: true, type: 'idle_unload' as const, params: {}, evaluationIntervalSeconds: 30, lastEvaluatedAt: '2026-11-17T11:59:50Z', nextEvaluationAt: null };

  it('prefers the server value when the API sends one', () => {
    expect(nextEvaluation({ ...base, nextEvaluationAt: '2026-11-17T12:00:45Z' }, now)).toBe(Date.parse('2026-11-17T12:00:45Z'));
    // A server value in the past (stale cache) falls back to the derivation.
    expect(nextEvaluation({ ...base, nextEvaluationAt: '2026-11-17T11:00:00Z' }, now)).toBe(Date.parse('2026-11-17T12:00:20Z'));
  });

  it('derives it from the last evaluation and interval otherwise', () => {
    expect(nextEvaluation(base, now)).toBe(Date.parse('2026-11-17T12:00:20Z'));
    expect(nextEvaluation({ ...base, lastEvaluatedAt: null }, now)).toBe(now + 30_000);
  });

  it('uses the next schedule occurrence for time schedules and null when disabled', () => {
    const schedule = { ...base, type: 'time_schedule' as const, params: { at: '18:00', timezone: 'UTC' } };
    expect(nextEvaluation(schedule, now)).toBe(Date.parse('2026-11-17T18:00:00Z'));
    expect(nextEvaluation({ ...base, enabled: false }, now)).toBeNull();
  });
});
