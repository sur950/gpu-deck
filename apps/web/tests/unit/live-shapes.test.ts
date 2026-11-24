/**
 * Shapes the live backend sends that the fixtures did not: node
 * engine objects, ISO `day` buckets, empty usage group keys, negative derived
 * counters, agent version gates and partial PATCH bodies.
 */
import { describe, expect, it } from 'vitest';
import { clampSeries } from '@/components/charts/series-utils';
import { bucketStartMs } from '@/components/usage/usage-shared';
import { mapSummaryRow } from '@/hooks/api/usage-api';
import { isTerminalCommand, nodeEngines, versionAtLeast } from '@/types/fleet';
import type { DeploymentUpdate, ModelUpdate } from '@/types/models';
import type { components } from '@/lib/api/generated/admin';

describe('nodeEngines', () => {
  it('reads agent 1.1 engine objects and legacy plain names', () => {
    expect(
      nodeEngines({
        engines: [
          { name: 'llama_cpp', path: '/opt/llama-server', runtime: 'native', versions: ['b8798'] },
          { name: 'sglang', path: null, runtime: 'container', versions: [] },
          'mlx',
          { bogus: true },
        ],
      }),
    ).toEqual([
      { name: 'llama_cpp', runtime: 'native', path: '/opt/llama-server', versions: ['b8798'] },
      { name: 'sglang', runtime: 'container', path: null, versions: [] },
      { name: 'mlx', runtime: null, path: null, versions: [] },
    ]);
    expect(nodeEngines(null)).toEqual([]);
    expect(nodeEngines({ engines: 'llama_cpp' })).toEqual([]);
  });
});

describe('versionAtLeast / isTerminalCommand', () => {
  it('compares dotted versions numerically', () => {
    expect(versionAtLeast('1.1.0', '1.1.0')).toBe(true);
    expect(versionAtLeast('1.10.0', '1.1.0')).toBe(true);
    expect(versionAtLeast('v1.2.3+abc', '1.1.0')).toBe(true);
    expect(versionAtLeast('0.1.0+a1f9c2d', '1.1.0')).toBe(false);
    expect(versionAtLeast(null, '1.1.0')).toBe(false);
  });

  it('knows which agent command states are final', () => {
    expect(isTerminalCommand('succeeded')).toBe(true);
    expect(isTerminalCommand('timed_out')).toBe(true);
    expect(isTerminalCommand('running')).toBe(false);
    expect(isTerminalCommand('sent')).toBe(false);
  });
});

describe('usage buckets and groups', () => {
  it('parses ISO and bare-date day buckets as UTC', () => {
    expect(bucketStartMs('2026-11-18T00:00:00Z')).toBe(Date.UTC(2026, 10, 18));
    expect(bucketStartMs('2026-11-18')).toBe(Date.UTC(2026, 10, 18));
    expect(Number.isNaN(bucketStartMs(undefined))).toBe(true);
  });

  it('names the empty group the API uses for unrouted requests', () => {
    const totals = { request_count: 3 } as components['schemas']['UsageSummaryRow']['totals'];
    const row = mapSummaryRow({ group: { model: '' }, labels: { model: '' }, totals });
    expect(row.group.model).toBe('unrouted');
    expect(row.labels.model).toBe('Unrouted (no model)');
    const idle = mapSummaryRow({ group: { api_key: '' }, labels: { api_key: 'unallocated' }, totals });
    expect(idle.group.api_key).toBe('unallocated');
    const named = mapSummaryRow({ group: { node: 'n1' }, labels: {}, totals });
    expect(named.labels.node).toBe('n1');
  });
});

describe('clampSeries', () => {
  it('raises negative derived values to zero and keeps gaps', () => {
    const out = clampSeries({ metric: 'computed_tokens', unit: 'tokens', labels: {}, points: [{ t: 1, v: -6308 }, { t: 2, v: null }, { t: 3, v: 31 }] });
    expect(out?.points.map((p) => p.v)).toEqual([0, null, 31]);
    expect(clampSeries(undefined)).toBeUndefined();
  });
});

describe('generated request types', () => {
  it('lets PATCH bodies leave defaulted fields out', () => {
    // Compile-time check (pnpm type:check): before the gen-api fix these needed
    // replicas / gpu_count / tp_size … and allow_pickle / trust_remote_code.
    const deployment: DeploymentUpdate = { name: 'renamed' };
    const model: ModelUpdate = { description: null };
    expect(Object.keys(deployment)).toEqual(['name']);
    expect(model.description).toBeNull();
  });
});
