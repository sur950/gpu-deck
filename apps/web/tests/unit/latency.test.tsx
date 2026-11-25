/**
 * Network latency in the UI: RTT formatting, the node-list cell, the timing parts of the
 * inference view, the API mapping and the mocks.
 */
import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { matchMockHandler } from '@/lib/mocks/registry';
import '@/lib/mocks/handlers';
import type { MockRequest } from '@/lib/mocks/types';
import { mapLatencyLive } from '@/lib/api/latency';
import { NodeRttCell, formatRtt, rttTone } from '@/components/observability/latency';
import { averageParts } from '@/components/observability/request-timing-card';
import { IDS } from '@/lib/mocks/fixtures/fleet';
import type { NodeRtt } from '@/types/latency';

function call(path: string) {
  const url = new URL(`http://api.test/api/v1${path}`);
  const route = url.pathname.replace('/api/v1', '');
  const match = matchMockHandler('GET', route);
  if (!match) throw new Error(`no mock for GET ${route}`);
  const req: MockRequest = { method: 'GET', path: route, params: match.params, query: url.searchParams, body: undefined, url };
  const result = match.handler.handle(req) as { status: number; data: unknown };
  return result;
}

describe('formatRtt / rttTone', () => {
  it('formats sub-millisecond to seconds', () => {
    expect(formatRtt(0.42)).toBe('0.4 ms');
    expect(formatRtt(8.25)).toBe('8.3 ms');
    expect(formatRtt(84.4)).toBe('84 ms');
    expect(formatRtt(1200)).toBe('1.20 s');
    expect(formatRtt(null)).toBe('—');
    expect(formatRtt(Number.NaN)).toBe('—');
  });

  it('tones the serving RTT', () => {
    expect(rttTone(3)).toBe('good');
    expect(rttTone(80)).toBe('warn');
    expect(rttTone(220)).toBe('bad');
    expect(rttTone(undefined)).toBe('none');
  });
});

describe('request timing', () => {
  it('keeps only the parts present on average', () => {
    const parts = averageParts({ requests: 3, part_api_ms: 1, part_network_ms: 12, part_unsplit_ms: 0, part_engine_ms: 5 });
    expect(parts.map((p) => p.key)).toEqual(['part_api_ms', 'part_network_ms', 'part_engine_ms']);
  });
});

describe('node RTT', () => {
  it('maps the live view and renders the node-list cell', () => {
    const live = mapLatencyLive(call('/metrics/latency/live').data as never);
    expect(live.apiLayers.map((l) => l.region)).toEqual(['eu-west', 'us-east']);
    const node = live.nodes.find((n) => n.nodeId === IDS.nodes.mac) as NodeRtt;
    expect(node.apiLayers[0].rttMs).toBeLessThan(node.apiLayers[1].rttMs as number); // nearest first
    expect(node.bestRttMs).toBe(node.apiLayers[0].rttMs);
    render(<NodeRttCell rtt={node} />);
    expect(screen.getByText(formatRtt(node.bestRttMs))).toBeTruthy();
    expect(screen.getByText(`hub ${formatRtt(node.hub?.rttMs)}`)).toBeTruthy();
  });

  it('shows a dash without measurements', () => {
    render(<NodeRttCell rtt={undefined} />);
    expect(screen.getByText('—')).toBeTruthy();
  });

  it('mocks history and request timing in the contract shape', () => {
    const history = call(`/metrics/latency?node_id=${IDS.nodes.gpu}`).data as { series: { labels: Record<string, string> }[]; summaries: unknown[] };
    expect(history.series.every((s) => s.labels.node_id === IDS.nodes.gpu)).toBe(true);
    expect(history.summaries).toHaveLength(3); // hub + two API layers
    const timing = call('/metrics/request-timing').data as { summary: Record<string, number> };
    const parts = Object.entries(timing.summary).filter(([k]) => k.startsWith('part_')).reduce((s, [, v]) => s + v, 0);
    expect(parts).toBeCloseTo(timing.summary.ttft_ms);
  });
});
