/**
 * Mock live stream — stands in for `/live/events` (SSE) and `/logs/stream`.
 *
 * EventSource cannot go through a patched `fetch`, so in mock mode the live
 * hooks subscribe here. Frames have the same shape as the contract's
 * `LiveEvent` and the `metrics` frame carries `{gpu, engine, overview}` built
 * from the same generators the REST mocks use, so charts and live tiles agree.
 */

import type { Schemas } from '@/lib/mocks/types';

type LiveEvent = Schemas['LiveEvent'];

export interface MockLiveOptions {
  topics?: string[];
  intervalSeconds?: number;
  scope?: { clusterId?: string; nodeId?: string; deploymentId?: string; instanceId?: string };
}

type Fixtures = {
  ts: typeof import('@/lib/mocks/fixtures/timeseries');
  fleet: typeof import('@/lib/mocks/fixtures/fleet');
  logs: typeof import('@/lib/mocks/fixtures/logs');
  activity: typeof import('@/lib/mocks/fixtures/activity');
};

let fixturesPromise: Promise<Fixtures> | null = null;

function loadFixtures(): Promise<Fixtures> {
  if (!fixturesPromise) {
    fixturesPromise = Promise.all([
      import('@/lib/mocks/fixtures/timeseries'),
      import('@/lib/mocks/fixtures/fleet'),
      import('@/lib/mocks/fixtures/logs'),
      import('@/lib/mocks/fixtures/activity'),
    ]).then(([ts, fleet, logs, activity]) => ({ ts, fleet, logs, activity }));
  }
  return fixturesPromise;
}

const DEFAULT_TOPICS = ['metrics', 'instance', 'node', 'device', 'swap', 'alert'];

/**
 * Subscribe to mock live events. Returns an unsubscribe function.
 * `onOpen` fires once the fixtures are loaded (the "connection" is open).
 */
export function subscribeMockLive(
  options: MockLiveOptions,
  onEvent: (event: LiveEvent) => void,
  onOpen?: () => void,
): () => void {
  const topics = new Set(options.topics && options.topics.length > 0 ? options.topics : DEFAULT_TOPICS);
  const interval = Math.max(1, Math.min(60, options.intervalSeconds ?? 2)) * 1000;
  const timers: ReturnType<typeof setInterval>[] = [];
  let cancelled = false;

  void loadFixtures().then((fx) => {
    if (cancelled) return;
    onOpen?.();

    const scope = options.scope ?? {};
    const devices = fx.fleet.DEVICES.filter((d) => (!scope.nodeId || d.node_id === scope.nodeId) && (!scope.clusterId || d.cluster_id === scope.clusterId));
    const instances = fx.fleet.INSTANCES.filter(
      (i) =>
        i.state === 'ready' &&
        (!scope.nodeId || i.node_id === scope.nodeId) &&
        (!scope.deploymentId || i.deployment_id === scope.deploymentId) &&
        (!scope.instanceId || i.id === scope.instanceId),
    );

    const emit = (event: LiveEvent['event'], data: Record<string, unknown>) => {
      if (!cancelled) onEvent({ event, ts: new Date().toISOString(), data });
    };

    if (topics.has('metrics')) {
      const tick = () => {
        const now = Date.now();
        emit('metrics', {
          gpu: devices.map((d) => fx.ts.gpuSample(d.id, now)),
          engine: instances.map((i) => fx.ts.engineSample(i.id, now)),
          overview: fx.ts.overviewLive(now),
        });
      };
      tick();
      timers.push(setInterval(tick, interval));
    }

    if (topics.has('log')) {
      let cursor = Date.now();
      timers.push(
        setInterval(() => {
          const now = Date.now();
          for (const chunk of fx.logs.logChunksBetween(cursor, now, scope)) {
            emit('log', chunk as unknown as Record<string, unknown>);
          }
          cursor = now;
        }, 1000),
      );
    }

    if (topics.has('heartbeat')) {
      timers.push(setInterval(() => emit('heartbeat', { sequence: Math.floor(Date.now() / 15000) }), 15_000));
    }

    // Occasional state changes so timelines move while the page is open.
    if (topics.has('instance') || topics.has('swap') || topics.has('alert') || topics.has('rule_run')) {
      timers.push(
        setInterval(() => {
          const now = Date.now();
          const pulse = Math.floor(now / 45_000) % 4;
          if (pulse === 0 && topics.has('rule_run')) emit('rule_run', fx.activity.liveRuleRun(now) as unknown as Record<string, unknown>);
          if (pulse === 1 && topics.has('instance')) {
            const inst = instances[Math.floor(now / 45_000) % Math.max(1, instances.length)];
            if (inst) emit('instance', { ...inst, live: fx.ts.engineSample(inst.id, now) });
          }
          if (pulse === 2 && topics.has('swap')) emit('swap', fx.activity.liveSwap(now) as unknown as Record<string, unknown>);
          if (pulse === 3 && topics.has('alert')) {
            const alert = fx.activity.liveAlert(now);
            if (alert) emit('alert', alert as unknown as Record<string, unknown>);
          }
        }, 45_000),
      );
    }
  });

  return () => {
    cancelled = true;
    for (const t of timers) clearInterval(t);
  };
}
