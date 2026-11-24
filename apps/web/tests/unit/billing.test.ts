/**
 * Billing shapes: idle loaded GPU time arrives as an empty api_key id labelled
 * "unallocated" in both the estimate rows and report line items; the UI keys
 * its "Idle loaded (unallocated)" figures on the normalised id.
 */
import { describe, expect, it } from 'vitest';
import { mapBillingReport, mapSummaryRow, normalizeGroup, UNALLOCATED_KEY } from '@/hooks/api/usage-api';
import type { components } from '@/lib/api/generated/admin';

type S = components['schemas'];

const totals = { request_count: 0, cost_usd: 1.5, gpu_seconds: 600 } as S['UsageTotals'];

describe('normalizeGroup', () => {
  it('maps the backend unallocated bucket to a stable id', () => {
    expect(normalizeGroup({ api_key: '' }, { api_key: 'unallocated' })).toEqual({ group: { api_key: UNALLOCATED_KEY }, labels: { api_key: 'unallocated' } });
  });

  it('labels unresolved dimensions and falls back to the id', () => {
    expect(normalizeGroup({ api_key: '', model: 'm1' }, { api_key: '', model: '' })).toEqual({
      group: { api_key: 'unrouted', model: 'm1' },
      labels: { api_key: 'No API key', model: 'm1' },
    });
  });
});

describe('billing mapping', () => {
  it('estimate rows and report line items both expose the unallocated bucket', () => {
    expect(mapSummaryRow({ group: { api_key: '' }, labels: { api_key: 'unallocated' }, totals }).group.api_key).toBe(UNALLOCATED_KEY);
    const report = mapBillingReport({
      id: 'r1',
      name: 'Nov',
      status: 'ready',
      period_start: '2026-11-01T00:00:00Z',
      period_end: '2026-12-01T00:00:00Z',
      group_by: ['api_key', 'model'],
      created_at: '2026-11-01T00:00:00Z',
      line_items: [{ group: { api_key: '', model: 'm1' }, labels: { api_key: 'unallocated', model: 'qwen3' }, totals }],
    } as unknown as S['BillingReport']);
    expect(report.lineItems[0].group).toEqual({ api_key: UNALLOCATED_KEY, model: 'm1' });
    expect(report.lineItems[0].labels.model).toBe('qwen3');
  });
});
