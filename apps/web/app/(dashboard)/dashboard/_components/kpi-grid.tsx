'use client';

import { useMemo } from 'react';
import { Activity, Cpu, Gauge, Timer, Users } from 'lucide-react';
import { MetricCard } from '@/components/charts/metric-card';
import { combineSeries, filterSeries, latestValue, toSpark, trendChange } from '@/components/charts/series-utils';
import type { LiveEngineSample, LiveGpuSample } from '@/hooks/use-live-events';
import type { OverviewTrends } from '@/hooks/use-overview';
import type { Overview } from '@/types/overview';
import type { MetricSeries } from '@/types/metrics';
import { formatCompact, formatMs, formatPct, formatWatts } from '@/utils/metrics-format';

/** A GPU counts as busy above this utilisation. */
export const BUSY_UTIL_PCT = 10;

interface KpiGridProps {
  overview: Overview;
  trends: OverviewTrends;
  gpuLive: LiveGpuSample[];
  engineLive: LiveEngineSample[];
  /** Range bucket in seconds (to turn per-bucket counts into rates). */
  stepSeconds: number;
}

function perSecond(series: MetricSeries | undefined, stepSeconds: number): MetricSeries | undefined {
  if (!series) return undefined;
  return { ...series, points: series.points.map((p) => ({ t: p.t, v: p.v === null ? null : p.v / stepSeconds })) };
}

/**
 * The five headline numbers: traffic, throughput, latency, sessions, GPU load.
 * Three cards on the first row, two wider ones on the second (xl: 6-column grid).
 */
export function KpiGrid({ overview, trends, gpuLive, engineLive, stepSeconds }: KpiGridProps) {
  const live = overview.live;

  const derived = useMemo(() => {
    const inf = trends.inference;
    const requests = filterSeries(inf, 'requests_delta');
    const fleetRequests = combineSeries(requests, 'sum');
    const ttftP95 = combineSeries(filterSeries(inf, 'ttft_ms_p95'), 'weighted', requests);
    const ttftP50 = combineSeries(filterSeries(inf, 'ttft_ms_p50'), 'weighted', requests);
    const tps = combineSeries(filterSeries(inf, 'generation_tps'), 'sum');
    const active = combineSeries(filterSeries(inf, 'active_sessions'), 'sum');
    const util = combineSeries(filterSeries(trends.gpu, 'gpu_utilization_pct'), 'avg');
    const inputTokens = filterSeries(trends.usage, 'input_tokens').reduce((s, x) => s + x.points.reduce((a, p) => a + (p.v ?? 0), 0), 0);
    const cachedTokens = filterSeries(trends.usage, 'cached_tokens').reduce((s, x) => s + x.points.reduce((a, p) => a + (p.v ?? 0), 0), 0);
    return {
      requestRate: perSecond(fleetRequests, stepSeconds),
      ttftP95,
      ttftP50,
      tps,
      active,
      util,
      cachedShare: inputTokens > 0 ? (cachedTokens / inputTokens) * 100 : null,
    };
  }, [trends.inference, trends.gpu, trends.usage, stepSeconds]);

  const requestsPerSecond = live.requestsPerMinute !== null ? live.requestsPerMinute / 60 : latestValue(derived.requestRate);
  const slotsTotal = engineLive.reduce((s, e) => s + (e.slotsTotal ?? 0), 0);
  const busy = gpuLive.filter((g) => (g.utilizationPct ?? 0) >= BUSY_UTIL_PCT).length;
  const gpuCount = gpuLive.length || overview.counts.devices;
  const ttftP50Now = engineLive.length
    ? weightedLatest(engineLive.map((e) => [e.ttftMsP50, e.activeSessions]))
    : latestValue(derived.ttftP50);

  return (
    <section
      aria-label="Key metrics"
      className="grid gap-4 sm:grid-cols-2 sm:max-xl:[&>*:last-child]:col-span-2 xl:grid-cols-6 xl:[&>*]:col-span-2 xl:[&>*:nth-child(n+4)]:col-span-3"
    >
      <MetricCard
        label="Requests / s"
        value={formatCompact(requestsPerSecond, 1)}
        icon={Activity}
        detail={live.errorRatePct !== null ? `${formatPct(live.errorRatePct, 2)} errors · ${formatCompact(live.requestsPerMinute, 0)} / min` : undefined}
        change={trendChange(derived.requestRate)}
        changeLabel="across range"
        goodDirection="neutral"
        trend={toSpark(derived.requestRate)}
        href="/observability/inference"
      />
      <MetricCard
        label="Generated tokens / s"
        value={formatCompact(live.generationTps, 0)}
        icon={Gauge}
        detail={derived.cachedShare !== null ? `${formatPct(derived.cachedShare, 0)} of prompt tokens served from cache` : undefined}
        change={trendChange(derived.tps)}
        changeLabel="across range"
        goodDirection="up"
        trend={toSpark(derived.tps)}
        href="/observability/inference"
      />
      <MetricCard
        label="Time to first token · p95"
        value={formatMs(live.ttftMsP95)}
        icon={Timer}
        detail={ttftP50Now !== null ? `p50 ${formatMs(ttftP50Now)}` : undefined}
        change={trendChange(derived.ttftP95)}
        changeLabel="across range"
        goodDirection="down"
        trend={toSpark(derived.ttftP95)}
        href="/observability/inference"
      />
      <MetricCard
        label="Active sessions"
        value={String(live.activeSessions)}
        icon={Users}
        detail={`${live.waitingSessions} waiting${slotsTotal > 0 ? ` · ${slotsTotal} slots` : ''}`}
        change={trendChange(derived.active)}
        changeLabel="across range"
        goodDirection="neutral"
        trend={toSpark(derived.active)}
        href="/observability/inference#sessions"
      />
      <MetricCard
        label="GPU utilisation"
        value={formatPct(live.gpuUtilizationPctAvg, 0)}
        icon={Cpu}
        detail={`${busy} of ${gpuCount} GPUs busy · ${formatWatts(live.powerWatts)}`}
        change={trendChange(derived.util)}
        changeLabel="across range"
        goodDirection="neutral"
        trend={toSpark(derived.util)}
        href="/observability/infra"
      />
    </section>
  );
}

/** Mean of [value, weight] pairs weighted by the weight (falls back to a plain mean). */
function weightedLatest(pairs: [number | null, number | null][]): number | null {
  const valid = pairs.filter((p): p is [number, number | null] => p[0] !== null);
  if (valid.length === 0) return null;
  const w = valid.reduce((s, [, wt]) => s + (wt ?? 0), 0);
  if (w === 0) return valid.reduce((s, [v]) => s + v, 0) / valid.length;
  return valid.reduce((s, [v, wt]) => s + v * (wt ?? 0), 0) / w;
}
