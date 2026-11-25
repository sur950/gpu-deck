'use client';

/**
 * Small building blocks for network latency: RTT formatting and tone, the node-list cell and
 * the colours of the timing parts.
 */

import { useMemo } from 'react';
import { cn } from '@/lib/utils';
import { useLiveLatency } from '@/hooks/use-latency';
import { OTHER_COLOR } from '@/components/charts/chart-theme';
import type { NodeRtt, TimingPartKey } from '@/types/latency';

/** Round trip: "0.4 ms", "8.2 ms", "84 ms", "1.20 s". */
export function formatRtt(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || !Number.isFinite(ms)) return '—';
  if (ms < 10) return `${ms.toFixed(1)} ms`;
  if (ms < 1000) return `${Math.round(ms)} ms`;
  return `${(ms / 1000).toFixed(2)} s`;
}

export type RttTone = 'good' | 'warn' | 'bad' | 'none';

/** Colour of an RTT for the serving path: under 50 ms fine, 50-150 ms far, above 150 ms very far. */
export function rttTone(ms: number | null | undefined): RttTone {
  if (ms === null || ms === undefined || !Number.isFinite(ms)) return 'none';
  if (ms >= 150) return 'bad';
  if (ms >= 50) return 'warn';
  return 'good';
}

const TONE_CLASS: Record<RttTone, string> = {
  good: 'text-foreground',
  warn: 'text-warning-dark dark:text-warning-light',
  bad: 'text-error-dark dark:text-error-light',
  none: 'text-muted-foreground',
};

export function RttValue({ ms, className }: { ms: number | null | undefined; className?: string }) {
  return <span className={cn('font-mono tabular-nums', TONE_CLASS[rttTone(ms)], className)}>{formatRtt(ms)}</span>;
}

/** Live RTT of every node, keyed by node id (one poll for a whole list). */
export function useNodeRttMap(): Map<string, NodeRtt> {
  const live = useLiveLatency();
  return useMemo(() => new Map((live.data?.nodes ?? []).map((n) => [n.nodeId, n])), [live.data]);
}

/** Node list cell: nearest API layer RTT (serving path) over the control-plane RTT. */
export function NodeRttCell({ rtt }: { rtt: NodeRtt | undefined }) {
  if (!rtt || (rtt.bestRttMs === null && !rtt.hub)) return <span className="text-xs text-muted-foreground">—</span>;
  const nearest = rtt.apiLayers[0];
  const title = [
    nearest ? `API layer ${nearest.apiLayerId}${nearest.region ? ` (${nearest.region})` : ''} via ${nearest.mode ?? 'proxy'}: ${formatRtt(nearest.rttMs)}` : 'No API layer measurement',
    rtt.hub ? `Control plane (hub): ${formatRtt(rtt.hub.rttMs)}` : 'Control plane: not measured',
  ].join('\n');
  return (
    <div className="text-xs leading-tight" title={title}>
      <RttValue ms={rtt.bestRttMs} />
      <div className="text-[11px] text-muted-foreground">hub {formatRtt(rtt.hub?.rttMs)}</div>
    </div>
  );
}

export const TIMING_PART_COLORS: Record<TimingPartKey, string> = {
  part_api_ms: 'rgb(var(--chart-5))',
  part_network_ms: 'rgb(var(--chart-2))',
  part_node_ms: 'rgb(var(--chart-4))',
  part_unsplit_ms: OTHER_COLOR,
  part_engine_ms: 'rgb(var(--chart-1))',
  part_first_token_ms: 'rgb(var(--chart-3))',
};
