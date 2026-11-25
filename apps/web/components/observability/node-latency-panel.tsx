'use client';

/**
 * Node detail › Network: round-trip time from the control plane (agent hub) and from every
 * API layer to this node, now and over a range (p50 / p95, chart).
 */

import { useMemo } from 'react';
import { Activity, Network, RadioTower, Waypoints } from 'lucide-react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { EmptyState } from '@/components/shared/EmptyState';
import { RangeBar } from '@/components/charts/range-bar';
import { StatTile } from '@/components/charts/stat-tile';
import { asResult } from '@/components/charts/series-utils';
import { SERIES_COLORS } from '@/components/charts/chart-theme';
import { MetricsChartCard } from '@/components/observability/entity-chart';
import type { MetricLine } from '@/components/observability/entity-chart';
import { RttValue, formatRtt, rttTone } from '@/components/observability/latency';
import { useLatencyHistory, useLiveLatency } from '@/hooks/use-latency';
import { useTimeRange } from '@/hooks/use-time-range';
import type { LatencySummary, NodeRtt, RttStats } from '@/types/latency';

const RANGES = ['1h', '6h', '24h', '7d'];

interface PathRow {
  key: string;
  label: string;
  detail: string;
  current: RttStats | null;
  summary: LatencySummary | undefined;
}

function pathRows(live: NodeRtt | undefined, summaries: LatencySummary[]): PathRow[] {
  const rows: PathRow[] = [];
  const hubSummary = summaries.find((s) => s.source === 'hub');
  if (live?.hub || hubSummary) {
    rows.push({ key: 'hub', label: 'Control plane', detail: 'agent hub · WebSocket ping', current: live?.hub ?? null, summary: hubSummary });
  }
  const layerIds = new Set<string>([
    ...(live?.apiLayers ?? []).map((l) => l.apiLayerId),
    ...summaries.filter((s) => s.source === 'api_layer' && s.apiLayerId).map((s) => s.apiLayerId as string),
  ]);
  for (const id of layerIds) {
    const current = live?.apiLayers.find((l) => l.apiLayerId === id) ?? null;
    const summary = summaries.find((s) => s.source === 'api_layer' && s.apiLayerId === id);
    const region = current?.region ?? summary?.region;
    const mode = current?.mode ?? summary?.mode;
    rows.push({
      key: `api:${id}`,
      label: `API layer ${id}`,
      detail: [region ? `region ${region}` : null, mode ? `${mode} path` : null].filter(Boolean).join(' · ') || 'serving path',
      current,
      summary,
    });
  }
  return rows;
}

export function NodeLatencyPanel({ nodeId }: { nodeId: string }) {
  const range = useTimeRange('1h');
  const step = range.step === '2s' || range.step === '15s' ? '1m' : range.step;
  const live = useLiveLatency(nodeId);
  const history = useLatencyHistory({ from: range.from, to: range.to, step, nodeId });
  const node = live.data?.nodes.find((n) => n.nodeId === nodeId);
  const summaries = useMemo(() => history.data?.summaries ?? [], [history.data]);
  const rows = useMemo(() => pathRows(node, summaries), [node, summaries]);

  // One chart line per path: series renamed so every path has its own key.
  const { result, lines } = useMemo(() => {
    const series = (history.data?.series ?? []).filter((s) => s.metric === 'rtt_ms');
    const out = series.map((s) => ({ ...s, metric: s.labels.source === 'hub' ? 'hub' : `api:${s.labels.api_layer_id}` }));
    const chartLines: MetricLine[] = out.map((s, i) => ({
      metric: s.metric,
      label: s.metric === 'hub' ? 'Control plane (hub)' : `API layer ${s.labels.api_layer_id}${s.labels.region ? ` · ${s.labels.region}` : ''}`,
      color: SERIES_COLORS[i % SERIES_COLORS.length],
      kind: 'line',
      dashed: s.metric === 'hub',
    }));
    return { result: asResult(out), lines: chartLines };
  }, [history.data]);

  const apiSummaries = summaries.filter((s) => s.source === 'api_layer');
  const nearest = node?.apiLayers[0];
  const bestP50 = apiSummaries.reduce<number | null>((m, s) => (s.p50Ms !== null && (m === null || s.p50Ms < m) ? s.p50Ms : m), null);
  const bestP95 = apiSummaries.reduce<number | null>((m, s) => (s.p95Ms !== null && (m === null || s.p95Ms < m) ? s.p95Ms : m), null);
  const hubSummary = summaries.find((s) => s.source === 'hub');
  const empty = !node && summaries.length === 0 && !live.isPending && !history.isPending;
  const rangeLabel = range.preset.label.toLowerCase();

  return (
    <div className="space-y-4">
      <RangeBar range={range.rangeId} onRangeChange={range.setRange} presets={RANGES} />
      {empty ? (
        <Card className="rounded-md shadow-premium">
          <CardContent>
            <EmptyState
              icon={Network}
              title="No round-trip measurements yet"
              description="API layers probe every connected node every few seconds and the agent hub pings every agent; values show here within a minute of the node connecting."
            />
          </CardContent>
        </Card>
      ) : (
        <>
          <section aria-label="Round trip now" className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <StatTile
              label="Nearest API layer"
              icon={Waypoints}
              value={formatRtt(nearest?.rttMs)}
              unit={nearest?.mode ? `· ${nearest.mode}` : undefined}
              hint={nearest ? `${nearest.apiLayerId}${nearest.region ? ` · ${nearest.region}` : ''} · jitter ${formatRtt(nearest.jitterMs)}` : 'not measured'}
              tone={rttTone(nearest?.rttMs) === 'bad' ? 'bad' : rttTone(nearest?.rttMs) === 'warn' ? 'warn' : 'default'}
            />
            <StatTile
              label={bestP50 !== null ? `Serving path p50 · ${rangeLabel}` : 'Serving path p50 · recent probes'}
              icon={Activity}
              value={formatRtt(bestP50 ?? nearest?.p50Ms)}
              hint={`p95 ${formatRtt(bestP95 ?? nearest?.p95Ms)} (nearest API layer)`}
            />
            <StatTile label="Control plane" icon={RadioTower} value={formatRtt(node?.hub?.rttMs)} hint={node?.hub ? `jitter ${formatRtt(node.hub.jitterMs)} · hub ping every few seconds` : 'not connected to this hub'} />
            <StatTile
              label={hubSummary ? `Control plane p50 · ${rangeLabel}` : 'Control plane p50 · recent pings'}
              icon={Activity}
              value={formatRtt(hubSummary?.p50Ms ?? node?.hub?.p50Ms)}
              hint={`p95 ${formatRtt(hubSummary?.p95Ms ?? node?.hub?.p95Ms)}`}
            />
          </section>

          <MetricsChartCard
            title="Round-trip time"
            description="Smoothed RTT per minute: each API layer over the path it serves this node by (tunnel or direct), and the agent hub (dashed)."
            result={result}
            lines={lines}
            fromMs={range.fromMs}
            toMs={range.toMs}
            unit="ms"
            height={220}
            refreshing={history.isFetching && !history.isPending}
            emptyTitle="No history in this range"
            emptyDescription="The jobs process stores one value per path and minute."
          />

          <Card className="rounded-md shadow-premium">
            <CardHeader>
              <CardTitle className="text-sm font-semibold">Paths</CardTitle>
              <CardDescription>
                Requests reach the node from an API layer; the control plane only sends commands and receives metrics. The serving RTT is what routing uses: the nearest healthy replica wins until it gets busy.
              </CardDescription>
            </CardHeader>
            <CardContent className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Path</TableHead>
                    <TableHead className="text-right">Now</TableHead>
                    <TableHead className="text-right">Jitter</TableHead>
                    <TableHead className="text-right">p50 · {rangeLabel}</TableHead>
                    <TableHead className="text-right">p95 · {rangeLabel}</TableHead>
                    <TableHead className="text-right">Min / max</TableHead>
                    <TableHead className="text-right">Probes</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {rows.map((row) => (
                    <TableRow key={row.key}>
                      <TableCell>
                        <div className="text-xs font-medium">{row.label}</div>
                        <div className="text-[11px] text-muted-foreground">{row.detail}</div>
                      </TableCell>
                      <TableCell className="text-right text-xs">
                        <RttValue ms={row.current?.rttMs} />
                      </TableCell>
                      <TableCell className="text-right font-mono text-xs tabular-nums">{formatRtt(row.current?.jitterMs)}</TableCell>
                      <TableCell className="text-right font-mono text-xs tabular-nums">{formatRtt(row.summary?.p50Ms)}</TableCell>
                      <TableCell className="text-right font-mono text-xs tabular-nums">{formatRtt(row.summary?.p95Ms)}</TableCell>
                      <TableCell className="text-right font-mono text-xs tabular-nums">
                        {formatRtt(row.summary?.minMs ?? row.current?.minMs)} / {formatRtt(row.summary?.maxMs ?? row.current?.maxMs)}
                      </TableCell>
                      <TableCell className="text-right font-mono text-[11px] tabular-nums text-muted-foreground">
                        {row.current ? `${row.current.samples}${row.current.failures ? ` · ${row.current.failures} lost` : ''}` : '—'}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        </>
      )}
    </div>
  );
}
