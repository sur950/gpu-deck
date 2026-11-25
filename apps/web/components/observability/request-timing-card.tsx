'use client';

/**
 * "Where the time went": the time to first byte of serving requests split into API layer,
 * network, node proxy, engine and first-token parts (they add up per request), over time and
 * on average, plus the probed round trip and a per-region breakdown when several API-layer
 * regions served traffic. Data: GET /metrics/request-timing.
 */

import { useMemo } from 'react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { TimeSeriesChart } from '@/components/charts/time-series-chart';
import type { ChartSeries } from '@/components/charts/time-series-chart';
import { mergeSeries } from '@/components/charts/series-utils';
import { TIMING_PART_COLORS, formatRtt } from '@/components/observability/latency';
import { useRequestTiming } from '@/hooks/use-latency';
import { TIMING_PARTS, type RequestTiming } from '@/types/latency';
import type { MetricStep } from '@/types/metrics';

interface RequestTimingCardProps {
  from: string;
  to: string;
  fromMs: number;
  toMs: number;
  step: MetricStep;
  deploymentId?: string;
  nodeId?: string;
}

/** Average parts that are present in the range, in stacking order. */
export function averageParts(summary: RequestTiming['summary']): { key: string; label: string; hint: string; ms: number; color: string }[] {
  return TIMING_PARTS.map((p) => ({ key: p.key, label: p.label, hint: p.hint, ms: Number(summary[p.key] ?? 0), color: TIMING_PART_COLORS[p.key] })).filter((p) => p.ms > 0.0005);
}

export function RequestTimingCard({ from, to, fromMs, toMs, step, deploymentId, nodeId }: RequestTimingCardProps) {
  const timingStep: MetricStep = step === '2s' || step === '15s' ? '1m' : step;
  const timing = useRequestTiming({ from, to, step: timingStep, deploymentId, nodeId });
  const data = timing.data;
  const requests = Number(data?.summary.requests ?? 0);

  const chart = useMemo(() => {
    // Up to the response headers: the first-token wait (generation) would dwarf the rest.
    const present = TIMING_PARTS.filter((p) => p.key !== 'part_first_token_ms' && (data?.series.find((s) => s.metric === p.key)?.points ?? []).some((pt) => (pt.v ?? 0) > 0));
    const series: ChartSeries[] = present.map((p) => ({ key: p.key, label: p.label, color: TIMING_PART_COLORS[p.key], kind: 'area', stackId: 'ttfb' }));
    const rows = mergeSeries(present.map((p) => ({ key: p.key, series: data?.series.find((s) => s.metric === p.key) })));
    return { series, rows };
  }, [data]);

  const parts = data ? averageParts(data.summary) : [];
  const total = parts.reduce((s, p) => s + p.ms, 0);
  const regions = (data?.regions ?? []).filter((r) => Number(r.requests ?? 0) > 0);

  return (
    <Card className="gap-0 rounded-xl py-0 shadow-premium lg:col-span-2">
      <CardHeader className="border-b border-border px-5 py-4">
        <CardTitle className="text-sm font-semibold">Where the time went</CardTitle>
        <CardDescription className="text-xs">
          Time to first byte of serving requests, split as the API layer measured it ({requests.toLocaleString()} request{requests === 1 ? '' : 's'} with timing in range). The same phases go back to clients in the <code className="font-mono">Server-Timing</code> header.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4 px-5 py-4">
        {requests > 0 && total > 0 && (
          <div className="space-y-2">
            <div className="flex h-3 overflow-hidden rounded-full bg-muted" role="img" aria-label={parts.map((p) => `${p.label} ${formatRtt(p.ms)}`).join(', ')}>
              {parts.map((p) => (
                <span key={p.key} title={`${p.label}: ${formatRtt(p.ms)} (${p.hint})`} style={{ width: `${(p.ms / total) * 100}%`, backgroundColor: p.color }} />
              ))}
            </div>
            <ul className="flex flex-wrap gap-x-4 gap-y-1 text-xs">
              {parts.map((p) => (
                <li key={p.key} className="flex items-center gap-1.5" title={p.hint}>
                  <span className="size-2.5 rounded-[3px]" style={{ backgroundColor: p.color }} aria-hidden />
                  <span className="text-muted-foreground">{p.label}</span>
                  <span className="font-mono tabular-nums">{formatRtt(p.ms)}</span>
                </li>
              ))}
              <li className="flex items-center gap-1.5 text-muted-foreground">
                · TTFB p50 <span className="font-mono tabular-nums text-foreground">{formatRtt(Number(data?.summary.ttft_p50_ms ?? NaN))}</span> p95{' '}
                <span className="font-mono tabular-nums text-foreground">{formatRtt(Number(data?.summary.ttft_p95_ms ?? NaN))}</span> · probed RTT avg{' '}
                <span className="font-mono tabular-nums text-foreground">{formatRtt(Number(data?.summary.rtt_ms ?? NaN))}</span>
              </li>
            </ul>
          </div>
        )}
        <div>
          <p className="mb-1 text-xs text-muted-foreground">Until the response headers (first-token wait left out), average per bucket, stacked</p>
          <TimeSeriesChart
            data={chart.rows}
            series={chart.series}
            fromMs={fromMs}
            toMs={toMs}
            formatValue={(v) => formatRtt(v)}
            height={200}
            refreshing={timing.isFetching && !timing.isPending}
            emptyTitle="No request timing in this range"
            emptyDescription="Serving requests record route, queue, connect, network and engine time once they pass through this version of the API layer."
          />
        </div>
        {regions.length > 1 && (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>API-layer region</TableHead>
                <TableHead className="text-right">Requests</TableHead>
                <TableHead className="text-right">Network</TableHead>
                <TableHead className="text-right">Probed RTT</TableHead>
                <TableHead className="text-right">TTFB</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {regions.map((r) => (
                <TableRow key={String(r.region)}>
                  <TableCell className="text-xs">{String(r.region || 'default')}</TableCell>
                  <TableCell className="text-right font-mono text-xs tabular-nums">{Number(r.requests ?? 0).toLocaleString()}</TableCell>
                  <TableCell className="text-right font-mono text-xs tabular-nums">{formatRtt(Number(r.part_network_ms ?? NaN))}</TableCell>
                  <TableCell className="text-right font-mono text-xs tabular-nums">{formatRtt(Number(r.rtt_ms ?? NaN))}</TableCell>
                  <TableCell className="text-right font-mono text-xs tabular-nums">{formatRtt(Number(r.ttft_ms ?? NaN))}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}
