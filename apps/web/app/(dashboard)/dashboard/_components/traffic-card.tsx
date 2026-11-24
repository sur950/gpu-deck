'use client';

import { useMemo, useState } from 'react';
import Link from 'next/link';
import { ArrowUpRight } from 'lucide-react';
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { ChartCard, TimeSeriesChart } from '@/components/charts/time-series-chart';
import type { ChartSeries } from '@/components/charts/time-series-chart';
import { combineSeries, filterSeries, mergeSeries } from '@/components/charts/series-utils';
import type { ChartRow } from '@/components/charts/series-utils';
import type { OverviewTrends } from '@/hooks/use-overview';
import type { MetricSeries } from '@/types/metrics';
import { formatCompact, formatMs } from '@/utils/metrics-format';

type Tab = 'requests' | 'tokens' | 'latency' | 'sessions';

const TABS: { value: Tab; label: string }[] = [
  { value: 'requests', label: 'Requests' },
  { value: 'tokens', label: 'Tokens' },
  { value: 'latency', label: 'Latency' },
  { value: 'sessions', label: 'Sessions' },
];

const SERIES_2 = 'rgb(var(--chart-2))';
const SERIES_4 = 'rgb(var(--chart-4))';
const ERROR = 'rgb(var(--error-rgb, 229 50 31))';

interface TrafficCardProps {
  trends: OverviewTrends;
  fromMs: number;
  toMs: number;
  stepSeconds: number;
  rangeLabel: string;
  className?: string;
}

function rate(series: MetricSeries | undefined, seconds: number): MetricSeries | undefined {
  if (!series) return undefined;
  return { ...series, points: series.points.map((p) => ({ t: p.t, v: p.v === null ? null : p.v / seconds })) };
}

/** Fleet traffic over the selected range, one measure family per tab (never two y-scales). */
export function TrafficCard({ trends, fromMs, toMs, stepSeconds, rangeLabel, className }: TrafficCardProps) {
  const [tab, setTab] = useState<Tab>('requests');

  const view = useMemo((): { data: ChartRow[]; series: ChartSeries[]; format: (v: number | null, compact: boolean) => string; description: string } => {
    const inf = trends.inference;
    const requests = filterSeries(inf, 'requests_delta');
    switch (tab) {
      case 'tokens':
        return {
          data: mergeSeries([{ key: 'tps', series: combineSeries(filterSeries(inf, 'generation_tps'), 'sum') }]),
          series: [{ key: 'tps', label: 'Generated tok/s', color: SERIES_2 }],
          format: (v, compact) => (v === null ? '—' : compact ? formatCompact(v, 0) : `${formatCompact(v, 1)} tok/s`),
          description: `Generated tokens per second, all models · ${rangeLabel}`,
        };
      case 'latency':
        return {
          data: mergeSeries([
            { key: 'p50', series: combineSeries(filterSeries(inf, 'ttft_ms_p50'), 'weighted', requests) },
            { key: 'p95', series: combineSeries(filterSeries(inf, 'ttft_ms_p95'), 'weighted', requests) },
          ]),
          series: [
            { key: 'p50', label: 'TTFT p50', color: SERIES_2, kind: 'line' },
            { key: 'p95', label: 'TTFT p95', color: SERIES_4, kind: 'line', dashed: true },
          ],
          format: (v, compact) => (v === null ? '—' : compact ? formatCompact(v, 0) : formatMs(v)),
          description: `Time to first token, request-weighted across instances · ${rangeLabel}`,
        };
      case 'sessions':
        return {
          data: mergeSeries([
            { key: 'active', series: combineSeries(filterSeries(inf, 'active_sessions'), 'sum') },
            { key: 'waiting', series: combineSeries(filterSeries(inf, 'waiting_sessions'), 'sum') },
          ]),
          series: [
            { key: 'active', label: 'Active', color: SERIES_2 },
            { key: 'waiting', label: 'Waiting', color: SERIES_4, kind: 'line' },
          ],
          format: (v, compact) => (v === null ? '—' : compact ? formatCompact(v, 0) : `${Math.round(v)} sessions`),
          description: `Sessions in engine slots and queued for one · ${rangeLabel}`,
        };
      default:
        return {
          data: mergeSeries([
            { key: 'rps', series: rate(combineSeries(requests, 'sum'), stepSeconds) },
            { key: 'eps', series: rate(combineSeries(filterSeries(inf, 'errors_delta'), 'sum'), stepSeconds) },
          ]),
          series: [
            { key: 'rps', label: 'Requests / s', color: SERIES_2 },
            { key: 'eps', label: 'Errors / s', color: ERROR, kind: 'line' },
          ],
          format: (v, compact) => (v === null ? '—' : compact ? formatCompact(v, 1) : `${v.toFixed(v < 10 ? 2 : 1)} / s`),
          description: `Requests and failed requests per second, all models · ${rangeLabel}`,
        };
    }
  }, [tab, trends.inference, stepSeconds, rangeLabel]);

  return (
    <ChartCard
      className={className}
      title="Inference traffic"
      description={view.description}
      headerExtra={
        <>
          <Tabs value={tab} onValueChange={(v) => setTab(v as Tab)}>
            <TabsList className="bg-muted">
              {TABS.map((t) => (
                <TabsTrigger key={t.value} value={t.value} className="px-2.5 text-xs">
                  {t.label}
                </TabsTrigger>
              ))}
            </TabsList>
          </Tabs>
          <Link
            href="/observability/inference"
            aria-label="Open inference metrics"
            className="hidden size-8 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-foreground sm:flex"
          >
            <ArrowUpRight className="size-4" />
          </Link>
        </>
      }
    >
      <TimeSeriesChart
        data={view.data}
        series={view.series}
        fromMs={fromMs}
        toMs={toMs}
        formatValue={view.format}
        height={268}
        refreshing={trends.isFetching}
        emptyTitle="No traffic in this range"
        emptyDescription="Requests through the API layer chart here."
      />
    </ChartCard>
  );
}
