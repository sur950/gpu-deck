/**
 * lib/desktop: the bridge must be a no-op in normal browsers, and inside the
 * desktop shell map overview snapshots / live frames to tray status and
 * notifications exactly once.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { Overview } from '@/types/overview';
import {
  desktopNotify,
  desktopSetTrayStatus,
  getDesktopHost,
  isDesktopShell,
  type DesktopHost,
} from '@/lib/desktop/host';
import { isAppPath, liveEventNotification, Tracker, trayStatusFromOverview } from '@/lib/desktop/events';

const push = vi.fn();
vi.mock('next/navigation', () => ({ useRouter: () => ({ push }) }));
const getOverview = vi.fn<() => Promise<Overview>>();
vi.mock('@/lib/api/overview', () => ({ getOverview: () => getOverview() }));
const liveOptions: { onEvent?: (e: unknown) => void; topics?: string[] }[] = [];
vi.mock('@/hooks/use-live-events', () => ({
  useLiveEvents: (opts: { onEvent?: (e: unknown) => void; topics?: string[] }) => {
    liveOptions.push(opts);
    return { status: 'open', latest: {}, frames: 0, lastEventAt: null };
  },
}));

function overview(over: Partial<Overview> = {}): Overview {
  return {
    counts: {
      clusters: 1,
      nodes: 2,
      nodesOnline: 2,
      devices: 4,
      devicesUnhealthy: 0,
      models: 1,
      deployments: 1,
      deploymentsRunning: 1,
      instancesReady: 1,
      apiKeysActive: 1,
      rulesEnabled: 1,
      swapsInProgress: 0,
      alertsOpen: 0,
    },
    live: {} as Overview['live'],
    recentAlerts: [],
    recentSwaps: [],
    recentRuleRuns: [],
    ...over,
  };
}

const alert = (id: string, severity: 'info' | 'warning' | 'critical' = 'warning') => ({
  id,
  severity,
  eventType: 'engine_crashed',
  title: `Alert ${id}`,
  message: 'llama.cpp exited',
  resourceType: 'instance',
  resourceId: null,
  resourceName: 'qwen3-0.6b',
  status: 'open' as const,
  occurrences: 1,
  firstSeenAt: '2026-11-18T00:00:00Z',
  lastSeenAt: '2026-11-18T00:00:00Z',
  deliveries: [],
});

const swap = (id: string, state: string) =>
  ({
    id,
    kind: 'apply_layout',
    state,
    trigger: 'rule',
    ruleName: 'night',
    deploymentId: 'd1',
    deploymentName: 'qwen3',
    nodeId: null,
    steps: [],
    currentStep: 0,
    dryRun: false,
    startedAt: null,
    finishedAt: null,
    errorMessage: null,
    createdAt: '2026-11-18T00:00:00Z',
    plan: {},
  }) as unknown as Overview['recentSwaps'][number];

function fakeHost(): DesktopHost & { notify: ReturnType<typeof vi.fn>; setTrayStatus: ReturnType<typeof vi.fn> } {
  return {
    version: 1,
    platform: 'macos',
    notify: vi.fn(async () => {}),
    setTrayStatus: vi.fn(async () => {}),
  };
}

beforeEach(() => {
  push.mockReset();
  getOverview.mockReset();
  liveOptions.length = 0;
});

afterEach(() => {
  delete window.__GPUDECK_DESKTOP__;
});

describe('desktop host (browser)', () => {
  it('is absent and every call is a no-op', async () => {
    expect(getDesktopHost()).toBeNull();
    expect(isDesktopShell()).toBe(false);
    await expect(desktopNotify({ title: 'x' })).resolves.toBe(false);
    await expect(desktopSetTrayStatus({ health: 'ok', activeSwaps: [], openAlerts: [] })).resolves.toBe(false);
  });

  it('ignores lookalike globals', () => {
    window.__GPUDECK_DESKTOP__ = { version: 2, notify: () => {}, setTrayStatus: () => {} };
    expect(getDesktopHost()).toBeNull();
    window.__GPUDECK_DESKTOP__ = { version: 1, notify: 'x' };
    expect(getDesktopHost()).toBeNull();
  });

  it('swallows host refusals', async () => {
    const host = fakeHost();
    host.notify.mockRejectedValue('origin not allowed');
    window.__GPUDECK_DESKTOP__ = host;
    await expect(desktopNotify({ title: 'x' })).resolves.toBe(false);
  });
});

describe('desktop events mapping', () => {
  it('builds the tray status from the overview', () => {
    const s = trayStatusFromOverview(
      overview({
        counts: { ...overview().counts, nodesOnline: 1, alertsOpen: 1, swapsInProgress: 1 },
        recentAlerts: [alert('a1', 'critical'), { ...alert('a2'), status: 'resolved' }],
        recentSwaps: [swap('s1', 'loading'), swap('s2', 'completed')],
      }),
    );
    expect(s.health).toBe('degraded');
    expect(s.openAlerts).toEqual([{ label: 'Critical: Alert a1', path: '/alerts' }]);
    expect(s.activeSwaps).toEqual([{ label: 'qwen3 · loading', path: '/swaps/s1' }]);
    expect(s).toMatchObject({ nodesOnline: 1, nodesTotal: 2, swapsInProgress: 1, alertsOpen: 1 });
    expect(trayStatusFromOverview(undefined, false).health).toBe('down');
    expect(trayStatusFromOverview(overview()).health).toBe('ok');
    expect(
      trayStatusFromOverview(overview({ counts: { ...overview().counts, nodesOnline: 0 } })).health,
    ).toBe('down');
  });

  it('baselines the first snapshot, then notifies new items once', () => {
    const t = new Tracker();
    expect(t.fromOverview(overview({ recentAlerts: [alert('a1')] }))).toEqual([]);
    const next = t.fromOverview(
      overview({ recentAlerts: [alert('a1'), alert('a2', 'critical')], recentSwaps: [swap('s1', 'failed')] }),
    );
    expect(next.map((n) => n.title)).toEqual(['Critical: Alert a2', 'Swap failed: qwen3']);
    expect(next[1]).toMatchObject({ path: '/swaps/s1', severity: 'critical' });
    // the same alert arriving live is not announced again
    expect(t.fromLive({ event: 'alert', ts: 'now', data: { id: 'a2', title: 'Alert a2' } })).toBeNull();
    expect(t.fromOverview(overview({ recentAlerts: [alert('a2')] }))).toEqual([]);
  });

  it('maps live alert / swap / rule_run frames', () => {
    expect(
      liveEventNotification({ event: 'alert', ts: 't', data: { kind: 'test', channel_name: 'ops-slack' } })?.notification,
    ).toEqual({ title: 'Test alert', body: 'Sent to ops-slack', path: '/alerts', severity: 'info' });
    expect(liveEventNotification({ event: 'swap', ts: 't', data: { swap_id: 's9', state: 'loading' } })).toBeNull();
    expect(
      liveEventNotification({ event: 'swap', ts: 't', data: { swap_id: 's9', state: 'completed', deployment_name: 'qwen3' } }),
    ).toMatchObject({ key: 'swap:s9:completed', notification: { title: 'Swap completed: qwen3', path: '/swaps/s9' } });
    expect(
      liveEventNotification({ event: 'rule_run', ts: 't', data: { id: 'r1', rule_id: 'R', rule_name: 'idle', outcome: 'fired' } })
        ?.notification,
    ).toMatchObject({ title: 'Rule fired: idle', path: '/rules/R' });
    expect(
      liveEventNotification({ event: 'rule_run', ts: 't', data: { id: 'r2', outcome: 'fired', dry_run: true } }),
    ).toBeNull();
    expect(liveEventNotification({ event: 'metrics', ts: 't', data: {} })).toBeNull();
    for (const status of ['acknowledged', 'resolved']) {
      expect(
        liveEventNotification({ event: 'alert', ts: 't', data: { id: 'a1', title: 'GPU hot', status } }),
      ).toBeNull();
    }
    expect(
      liveEventNotification({ event: 'alert', ts: 't', data: { id: 'a1', title: 'GPU hot', status: 'open' } }),
    ).not.toBeNull();
  });

  it('accepts only in-app paths for navigation', () => {
    expect(isAppPath('/deployments/1')).toBe(true);
    expect(isAppPath('//evil.example.com')).toBe(false);
    expect(isAppPath('https://evil.example.com')).toBe(false);
    expect(isAppPath('/\\evil')).toBe(false);
    expect(isAppPath(42)).toBe(false);
  });
});

describe('<DesktopBridge />', () => {
  async function mount() {
    const { DesktopBridge } = await import('@/lib/desktop/desktop-bridge');
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return render(
      <QueryClientProvider client={client}>
        <DesktopBridge />
      </QueryClientProvider>,
    );
  }

  it('does nothing in a normal browser', async () => {
    const { container } = await mount();
    expect(container.innerHTML).toBe('');
    expect(getOverview).not.toHaveBeenCalled();
    expect(liveOptions).toHaveLength(0);
  });

  it('feeds the tray, notifies live events and handles navigation in the shell', async () => {
    const host = fakeHost();
    window.__GPUDECK_DESKTOP__ = host;
    getOverview.mockResolvedValue(overview({ recentAlerts: [alert('a1')] }));
    await mount();
    // 'unknown' until the snapshot arrives, then the real status.
    await waitFor(() =>
      expect(host.setTrayStatus.mock.calls.at(-1)?.[0]).toMatchObject({ health: 'ok', alertsOpen: 1 }),
    );
    expect(host.setTrayStatus.mock.calls[0]?.[0]).toMatchObject({ health: 'unknown' });
    expect(host.notify).not.toHaveBeenCalled(); // baseline
    expect(liveOptions.at(-1)?.topics).toEqual(['alert', 'rule_run', 'swap']);

    liveOptions.at(-1)?.onEvent?.({ event: 'alert', ts: 't', data: { id: 'x1', title: 'GPU 0 hot', severity: 'critical' } });
    await waitFor(() =>
      expect(host.notify).toHaveBeenCalledWith(
        expect.objectContaining({ title: 'GPU 0 hot', severity: 'critical', path: '/alerts' }),
      ),
    );

    const evt = new CustomEvent('gpudeck:navigate', { detail: { path: '/deployments/9' }, cancelable: true });
    expect(window.dispatchEvent(evt)).toBe(false);
    expect(push).toHaveBeenCalledWith('/deployments/9');
    const bad = new CustomEvent('gpudeck:navigate', { detail: { path: '//evil.example.com' }, cancelable: true });
    expect(window.dispatchEvent(bad)).toBe(true);
    expect(push).toHaveBeenCalledTimes(1);
  });
});
