/**
 * Network latency mocks: live RTT per node (agent hub + two API layers in two regions), the
 * per-minute RTT history and the request timing breakdown. Synthetic, deterministic values.
 */

import { NODES } from '@/lib/mocks/fixtures/fleet';
import { hash32 } from '@/lib/mocks/fixtures/random';
import { buildSeries, parseRange } from '@/lib/mocks/fixtures/timeseries';
import { ok, registerMockHandlers } from '@/lib/mocks/registry';
import type { Schemas } from '@/lib/mocks/types';

const LAYERS = [
  { id: 'edge-eu-1', region: 'eu-west', mode: 'tunnel' as const },
  { id: 'edge-us-1', region: 'us-east', mode: 'tunnel' as const },
];

/** Base RTT (ms) of a path: the first node sits next to the eu API layer, the others farther. */
function baseRtt(nodeIndex: number, path: string): number {
  if (path === 'hub') return 1.5 + nodeIndex * 6;
  if (path === 'edge-eu-1') return 0.8 + nodeIndex * 7.5;
  return 72 + nodeIndex * 4;
}

function wobble(key: string, t: number, base: number): number {
  const phase = (hash32(key) % 1000) / 1000;
  return Math.round((base * (1 + 0.08 * Math.sin(t / 240_000 + phase * 6.28)) + (base < 5 ? 0.05 : 0.6)) * 100) / 100;
}

function stats(key: string, base: number, now: number): Schemas['LatencyStats'] {
  const rtt = wobble(key, now, base);
  return {
    rtt_ms: rtt,
    jitter_ms: Math.round(base * 0.05 * 100) / 100,
    last_ms: rtt,
    min_ms: Math.round(base * 0.93 * 100) / 100,
    max_ms: Math.round(base * 1.2 * 100) / 100,
    p50_ms: rtt,
    p95_ms: Math.round(base * 1.12 * 100) / 100,
    samples: 360,
    failures: 0,
    updated_at: new Date(now - 4_000).toISOString(),
  };
}

function liveNode(node: Schemas['Node'], index: number, now: number): Schemas['NodeLatency'] {
  const layers = LAYERS.map((l) => ({ ...stats(`${node.id}:${l.id}`, baseRtt(index, l.id), now), api_layer_id: l.id, region: l.region, mode: l.mode })).sort((a, b) => (a.rtt_ms ?? 0) - (b.rtt_ms ?? 0));
  return {
    node_id: node.id,
    node_name: node.name,
    status: node.status,
    hub: stats(`${node.id}:hub`, baseRtt(index, 'hub'), now),
    api_layers: layers,
    best_rtt_ms: layers[0]?.rtt_ms ?? null,
  };
}

const PARTS: Record<string, number> = {
  part_api_ms: 0.6,
  part_network_ms: 9,
  part_node_ms: 0.4,
  part_unsplit_ms: 0,
  part_engine_ms: 6,
  part_first_token_ms: 140,
};

registerMockHandlers('latency', [
  {
    method: 'GET',
    path: '/metrics/latency/live',
    handle: (req) => {
      const now = Date.now();
      const nodeId = req.query.get('node_id');
      const nodes = NODES.map((n, i) => liveNode(n, i, now)).filter((n) => !nodeId || n.node_id === nodeId);
      const data: Schemas['LatencyLive'] = {
        generated_at: new Date(now).toISOString(),
        api_layers: LAYERS.map((l) => ({ id: l.id, region: l.region, seen_at: new Date(now - 3_000).toISOString(), probe_seconds: 10, nodes: NODES.length })),
        nodes,
      };
      return ok(data);
    },
  },
  {
    method: 'GET',
    path: '/metrics/latency',
    handle: (req) => {
      const range = parseRange(req.query);
      const nodeIds = req.query.getAll('node_id').flatMap((v) => v.split(',')).filter(Boolean);
      const series: Schemas['TimeSeries'][] = [];
      const summaries: Schemas['LatencySummary'][] = [];
      NODES.forEach((node, index) => {
        if (nodeIds.length > 0 && !nodeIds.includes(node.id)) return;
        for (const path of ['hub', ...LAYERS.map((l) => l.id)]) {
          const layer = LAYERS.find((l) => l.id === path);
          const base = baseRtt(index, path);
          const labels: Record<string, string> = { node_id: node.id, node: node.name, source: layer ? 'api_layer' : 'hub', api_layer_id: layer?.id ?? '' };
          if (layer) Object.assign(labels, { region: layer.region, mode: layer.mode });
          series.push(buildSeries('rtt_ms', 'ms', labels, range, (t) => wobble(`${node.id}:${path}`, t, base)));
          series.push(buildSeries('jitter_ms', 'ms', labels, range, () => Math.round(base * 0.05 * 100) / 100));
          summaries.push({
            node_id: node.id,
            node_name: node.name,
            source: layer ? 'api_layer' : 'hub',
            api_layer_id: layer?.id ?? null,
            region: layer?.region ?? null,
            mode: layer?.mode ?? null,
            points: 60,
            p50_ms: base,
            p95_ms: Math.round(base * 1.12 * 100) / 100,
            min_ms: Math.round(base * 0.93 * 100) / 100,
            max_ms: Math.round(base * 1.2 * 100) / 100,
            avg_ms: base,
            jitter_ms: Math.round(base * 0.05 * 100) / 100,
            last_at: new Date().toISOString(),
          });
        }
      });
      const data: Schemas['LatencyHistory'] = { from: range.from.toISOString(), to: range.to.toISOString(), step: `${range.stepSeconds}s`, series, summaries };
      return ok(data);
    },
  },
  {
    method: 'GET',
    path: '/metrics/request-timing',
    handle: (req) => {
      const range = parseRange(req.query);
      const series = [...Object.keys(PARTS), 'requests', 'rtt_ms', 'ttft_ms'].map((metric) =>
        buildSeries(metric, metric === 'requests' ? 'requests' : 'ms', { phase: metric.replace(/_ms$/, '') }, range, (t) => {
          if (metric === 'requests') return 40;
          if (metric === 'rtt_ms') return wobble('rtt', t, 8.5);
          if (metric === 'ttft_ms') return Object.values(PARTS).reduce((a, b) => a + b, 0);
          return PARTS[metric] ? wobble(metric, t, PARTS[metric]) : 0;
        }),
      );
      const ttft = Object.values(PARTS).reduce((a, b) => a + b, 0);
      const data: Schemas['RequestTiming'] = {
        from: range.from.toISOString(),
        to: range.to.toISOString(),
        step: `${range.stepSeconds}s`,
        series,
        summary: { requests: 2400, ...PARTS, rtt_ms: 8.5, ttft_ms: ttft, ttft_p50_ms: ttft * 0.9, ttft_p95_ms: ttft * 1.8, net_ms: PARTS.part_network_ms },
        regions: [
          { region: 'eu-west', requests: 1700, part_network_ms: 2.1, rtt_ms: 1.8, ttft_ms: ttft - 7 },
          { region: 'us-east', requests: 700, part_network_ms: 26, rtt_ms: 24, ttft_ms: ttft + 17 },
        ],
      };
      return ok(data);
    },
  },
]);
