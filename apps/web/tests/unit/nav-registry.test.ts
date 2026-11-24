import { describe, expect, it } from 'vitest';
import { ALL_NAV_HREFS, NAV_ENTRIES, NAV_SECTIONS, breadcrumbLabel, findNavEntry, isDynamicSegment, isNavItemActive } from '@/lib/nav/registry';

const crumbs = (path: string) => {
  const segments = path.split('/').filter(Boolean);
  return segments.map((_, i) => breadcrumbLabel(segments, i));
};

const UUID = '3f1c2a9e-8b7d-4c11-9a0e-5d2b6f7a8c90';

describe('breadcrumb labels are route-scoped', () => {
  it('gives every feature its own "new" label', () => {
    expect(crumbs('/rules/new')).toEqual(['Rules', 'New rule']);
    expect(crumbs('/api-keys/new')).toEqual(['API keys', 'New API key']);
    expect(crumbs('/deployments/new')).toEqual(['Deployments', 'New deployment']);
    expect(crumbs('/layouts/new')).toEqual(['Layouts', 'New layout']);
    expect(crumbs('/gpu-instances/new')).toEqual(['SSH instances', 'Request an instance']);
    expect(crumbs('/cloud/new')).toEqual(['Cloud VMs', 'Provision a GPU VM']);
    expect(crumbs('/catalog/new')).toEqual(['Catalog', 'Add a model']);
  });

  it('resolves nested segments below an id through the owning page', () => {
    expect(crumbs(`/rules/${UUID}/edit`)).toEqual(['Rules', 'Rule details', 'Edit rule']);
    expect(crumbs(`/deployments/${UUID}/edit`)).toEqual(['Deployments', 'Deployment details', 'Edit']);
  });

  it('uses page titles for registered hrefs and title-cases the rest', () => {
    expect(crumbs('/observability/logs')).toEqual(['Observability', 'Logs']);
    expect(crumbs('/billing/reports')).toEqual(['Billing', 'Reports']);
    expect(crumbs('/nodes/join')).toEqual(['Nodes', 'Add a node']);
    expect(crumbs('/unknown-thing/some-page')).toEqual(['Unknown Thing', 'Some Page']);
  });

  it('labels ids with the owner detail label', () => {
    expect(crumbs(`/nodes/${UUID}`)).toEqual(['Nodes', 'Node details']);
    expect(crumbs('/observability/traces/4bf92f3577b34da6a3ce929d0e0e4736')).toEqual(['Observability', 'Traces', 'Trace details']);
  });

  it('recognises dynamic segments', () => {
    expect(isDynamicSegment(UUID)).toBe(true);
    expect(isDynamicSegment('4bf92f3577b34da6a3ce929d0e0e4736')).toBe(true);
    expect(isDynamicSegment('42')).toBe(true);
    expect(isDynamicSegment('new')).toBe(false);
  });
});

describe('registry structure', () => {
  it('has unique ids and hrefs', () => {
    const ids = NAV_ENTRIES.map((e) => e.id);
    expect(new Set(ids).size).toBe(ids.length);
    expect(new Set(ALL_NAV_HREFS).size).toBe(ALL_NAV_HREFS.length);
  });

  it('sorts sections by order', () => {
    for (const section of NAV_SECTIONS) {
      const orders = section.items.map((i) => i.order ?? 100);
      expect([...orders].sort((a, b) => a - b)).toEqual(orders);
    }
  });

  it('finds the longest matching entry', () => {
    expect(findNavEntry('/observability/logs/anything')?.id).toBe('observability-logs');
    expect(findNavEntry(`/rules/${UUID}`)?.id).toBe('rules');
    expect(findNavEntry('/nope')).toBeUndefined();
  });
});

describe('active nav rule', () => {
  it('matches the dashboard exactly and prefers the longer sibling', () => {
    expect(isNavItemActive('/dashboard', '/dashboard')).toBe(true);
    expect(isNavItemActive('/dashboard', '/dashboard/x')).toBe(false);
    expect(isNavItemActive('/nodes', `/nodes/${UUID}`)).toBe(true);
    const hrefs = ['/billing', '/billing/reports'];
    expect(isNavItemActive('/billing', '/billing/reports/1', hrefs)).toBe(false);
    expect(isNavItemActive('/billing/reports', '/billing/reports/1', hrefs)).toBe(true);
  });
});
