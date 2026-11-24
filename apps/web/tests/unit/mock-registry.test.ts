import { describe, expect, it } from 'vitest';
import { listMockHandlers, matchMockHandler } from '@/lib/mocks/registry';
import { MOCK_HANDLER_FILES } from '@/lib/mocks/handlers';

describe('mock handlers', () => {
  it('loads every handler file', () => {
    expect(MOCK_HANDLER_FILES.length).toBeGreaterThanOrEqual(9);
  });

  it('has exactly one owning handler per method + route (duplicates must be fallback: true)', () => {
    const owners = new Map<string, string[]>();
    for (const h of listMockHandlers()) {
      if (h.fallback) continue;
      const key = `${h.method} ${h.pattern}`;
      owners.set(key, [...(owners.get(key) ?? []), `${h.feature}:${h.path}`]);
    }
    const clashes = [...owners.entries()].filter(([, list]) => list.length > 1);
    expect(clashes).toEqual([]);
  });

  it('prefers owning handlers over fallbacks and literal segments over params', () => {
    expect(matchMockHandler('GET', '/nodes/join-tokens')?.handler.path).toBe('/nodes/join-tokens');
    expect(matchMockHandler('GET', '/price-plans')?.feature).toBe('usage');
    expect(matchMockHandler('PATCH', '/clusters/00000000-0000-0000-0000-000000000001')?.feature).toMatch(/^fleet/);
  });
});
