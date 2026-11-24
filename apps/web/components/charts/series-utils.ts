/**
 * Helpers that turn contract time series into chart rows, plus time-range
 * presets shared by the dashboard and observability pages.
 */

import type { MetricSeries, MetricsResult, MetricStep } from '@/types/metrics';

/** One chart row: epoch ms + one numeric field per series key. */
export type ChartRow = { t: number } & Record<string, number | null>;

// ─── Time ranges ──────────────────────────────────────────────────────────────

export interface RangePreset {
  id: string;
  label: string;
  badge: string;
  ms: number;
  /** Default bucket for this span. */
  step: MetricStep;
  usageStep: '1h' | '1d';
}

export const RANGE_PRESETS: RangePreset[] = [
  { id: '15m', label: 'Last 15 minutes', badge: '15m', ms: 15 * 60_000, step: '2s', usageStep: '1h' },
  { id: '1h', label: 'Last hour', badge: '1h', ms: 3_600_000, step: '1m', usageStep: '1h' },
  { id: '6h', label: 'Last 6 hours', badge: '6h', ms: 6 * 3_600_000, step: '5m', usageStep: '1h' },
  { id: '24h', label: 'Last 24 hours', badge: '24h', ms: 24 * 3_600_000, step: '15m', usageStep: '1h' },
  { id: '7d', label: 'Last 7 days', badge: '7d', ms: 7 * 86_400_000, step: '1h', usageStep: '1d' },
  { id: '30d', label: 'Last 30 days', badge: '30d', ms: 30 * 86_400_000, step: '1d', usageStep: '1d' },
];

export const STEP_OPTIONS: { value: MetricStep | 'auto'; label: string }[] = [
  { value: 'auto', label: 'Auto' },
  { value: '2s', label: '2 s (raw)' },
  { value: '1m', label: '1 min' },
  { value: '5m', label: '5 min' },
  { value: '15m', label: '15 min' },
  { value: '1h', label: '1 hour' },
  { value: '1d', label: '1 day' },
];

export function findPreset(id: string | null | undefined, fallback = '1h'): RangePreset {
  return RANGE_PRESETS.find((p) => p.id === id) ?? (RANGE_PRESETS.find((p) => p.id === fallback) as RangePreset);
}

export interface ResolvedRange {
  preset: RangePreset;
  from: string;
  to: string;
  fromMs: number;
  toMs: number;
  step: MetricStep;
  usageStep: '1h' | '1d';
}

/**
 * Resolve a preset + step choice into ISO bounds. `to` is rounded down to the
 * nearest 10 s so React Query keys stay stable across quick re-renders.
 */
export function resolveRange(presetId: string | null | undefined, step: string | null | undefined, now = Date.now()): ResolvedRange {
  const preset = findPreset(presetId);
  const toMs = Math.floor(now / 10_000) * 10_000;
  const fromMs = toMs - preset.ms;
  const chosen = step && step !== 'auto' ? (step as MetricStep) : preset.step;
  return {
    preset,
    from: new Date(fromMs).toISOString(),
    to: new Date(toMs).toISOString(),
    fromMs,
    toMs,
    step: chosen,
    usageStep: preset.usageStep,
  };
}

// ─── Tick / label formatting ──────────────────────────────────────────────────

const TIME = new Intl.DateTimeFormat('en-US', { hour: '2-digit', minute: '2-digit', hour12: false });
const TIME_SECONDS = new Intl.DateTimeFormat('en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
const DAY_TIME = new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false });
const DAY = new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric' });
const FULL = new Intl.DateTimeFormat('en-US', { weekday: 'short', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });

/** Axis tick formatter appropriate for the span. */
export function tickFormatterFor(fromMs: number, toMs: number): (t: number) => string {
  const span = toMs - fromMs;
  if (span <= 20 * 60_000) return (t) => TIME_SECONDS.format(t);
  if (span <= 36 * 3_600_000) return (t) => TIME.format(t);
  if (span <= 8 * 86_400_000) return (t) => DAY_TIME.format(t);
  return (t) => DAY.format(t);
}

/** Tooltip title formatter for the span. */
export function labelFormatterFor(fromMs: number, toMs: number): (t: number) => string {
  const span = toMs - fromMs;
  if (span <= 20 * 60_000) return (t) => TIME_SECONDS.format(t);
  if (span <= 36 * 3_600_000) return (t) => `${DAY.format(t)} · ${TIME.format(t)}`;
  return (t) => FULL.format(t);
}

// ─── Series access ────────────────────────────────────────────────────────────

export function findSeries(result: MetricsResult | undefined, metric: string, match?: (labels: Record<string, string>) => boolean): MetricSeries | undefined {
  return result?.series.find((s) => s.metric === metric && (!match || match(s.labels)));
}

export function filterSeries(result: MetricsResult | undefined, metric: string, match?: (labels: Record<string, string>) => boolean): MetricSeries[] {
  return (result?.series ?? []).filter((s) => s.metric === metric && (!match || match(s.labels)));
}

/** Last non-null value of a series. */
export function latestValue(series: MetricSeries | undefined): number | null {
  if (!series) return null;
  for (let i = series.points.length - 1; i >= 0; i -= 1) {
    const v = series.points[i].v;
    if (v !== null) return v;
  }
  return null;
}

export function sumValues(series: MetricSeries | undefined): number {
  return series?.points.reduce((s, p) => s + (p.v ?? 0), 0) ?? 0;
}

export function avgValue(series: MetricSeries | undefined): number | null {
  const vals = series?.points.map((p) => p.v).filter((v): v is number => v !== null) ?? [];
  return vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : null;
}

export function maxValue(series: MetricSeries | undefined): number | null {
  const vals = series?.points.map((p) => p.v).filter((v): v is number => v !== null) ?? [];
  return vals.length ? Math.max(...vals) : null;
}

/**
 * Plain numeric array for sparklines (nulls become 0), averaged down to at
 * most `maxPoints` so a 40 px strip shows the trend, not the jitter.
 */
export function toSpark(series: MetricSeries | undefined, maxPoints = 40): number[] {
  const values = series?.points.map((p) => p.v ?? 0) ?? [];
  if (values.length <= maxPoints) return values;
  const size = values.length / maxPoints;
  const out: number[] = [];
  for (let i = 0; i < maxPoints; i += 1) {
    const chunk = values.slice(Math.floor(i * size), Math.floor((i + 1) * size));
    out.push(chunk.reduce((a, b) => a + b, 0) / Math.max(1, chunk.length));
  }
  return out;
}

/** Merge several series into chart rows keyed by timestamp. */
export function mergeSeries(entries: { key: string; series: MetricSeries | undefined }[]): ChartRow[] {
  const rows = new Map<number, ChartRow>();
  for (const { key, series } of entries) {
    if (!series) continue;
    for (const p of series.points) {
      const row = rows.get(p.t) ?? ({ t: p.t } as ChartRow);
      row[key] = p.v;
      rows.set(p.t, row);
    }
  }
  return [...rows.values()].sort((a, b) => a.t - b.t);
}

/** Percent change between the first and last thirds of a series (for change pills). */
export function trendChange(series: MetricSeries | undefined): string | null {
  const vals = series?.points.map((p) => p.v).filter((v): v is number => v !== null) ?? [];
  if (vals.length < 6) return null;
  const third = Math.floor(vals.length / 3);
  const head = vals.slice(0, third).reduce((a, b) => a + b, 0) / third;
  const tail = vals.slice(-third).reduce((a, b) => a + b, 0) / third;
  if (head === 0) return null;
  const pct = ((tail - head) / head) * 100;
  if (!Number.isFinite(pct)) return null;
  return `${pct >= 0 ? '+' : ''}${pct.toFixed(1)}%`;
}

// ─── Client-side aggregation ──────────────────────────────────────────────────

/** Identity of an entity series (instance / device) from its labels. */
export function seriesEntity(series: MetricSeries): string {
  return series.labels.instance_id ?? series.labels.device_id ?? series.labels.deployment_id ?? JSON.stringify(series.labels);
}

/**
 * Collapse per-entity series into one. `sum` for counters, sessions and
 * throughput; `avg` / `max` for gauges; `weighted` for latencies — the mean
 * weighted by a per-entity weight series (e.g. requests in the bucket), so a
 * busy instance counts more than an idle one.
 */
/** Copy of a series with negative values raised to 0 (derived counters that must not go below zero). */
export function clampSeries(series: MetricSeries | undefined, min = 0): MetricSeries | undefined {
  if (!series) return series;
  return { ...series, points: series.points.map((p) => (p.v !== null && p.v < min ? { ...p, v: min } : p)) };
}

export function combineSeries(
  list: MetricSeries[],
  mode: 'sum' | 'avg' | 'max' | 'weighted',
  weights: MetricSeries[] = [],
): MetricSeries | undefined {
  if (list.length === 0) return undefined;
  const weightBy = new Map(weights.map((w) => [seriesEntity(w), new Map(w.points.map((p) => [p.t, p.v ?? 0]))]));
  const buckets = new Map<number, { sum: number; n: number; max: number; wsum: number; w: number }>();
  for (const series of list) {
    const wmap = weightBy.get(seriesEntity(series));
    for (const p of series.points) {
      if (p.v === null) continue;
      const b = buckets.get(p.t) ?? { sum: 0, n: 0, max: -Infinity, wsum: 0, w: 0 };
      b.sum += p.v;
      b.n += 1;
      b.max = Math.max(b.max, p.v);
      const w = wmap?.get(p.t) ?? 0;
      b.wsum += p.v * w;
      b.w += w;
      buckets.set(p.t, b);
    }
  }
  const allTimes = new Set<number>();
  for (const s of list) for (const p of s.points) allTimes.add(p.t);
  const points = [...allTimes]
    .sort((a, b) => a - b)
    .map((t) => {
      const b = buckets.get(t);
      if (!b || b.n === 0) return { t, v: null };
      if (mode === 'sum') return { t, v: b.sum };
      if (mode === 'max') return { t, v: b.max };
      if (mode === 'weighted' && b.w > 0) return { t, v: b.wsum / b.w };
      return { t, v: b.sum / b.n };
    });
  return { metric: list[0].metric, unit: list[0].unit, labels: { aggregate: mode }, points };
}

/** Total of the last non-null values across series (e.g. current VRAM used across GPUs). */
export function sumLatest(list: MetricSeries[]): number | null {
  const values = list.map(latestValue).filter((v): v is number => v !== null);
  return values.length ? values.reduce((a, b) => a + b, 0) : null;
}

/** Seconds per step id. */
export const STEP_SECONDS_BY_ID: Record<string, number> = {
  '2s': 2,
  '15s': 15,
  '1m': 60,
  '5m': 300,
  '15m': 900,
  '1h': 3600,
  '1d': 86400,
};

/**
 * Collapse per-instance series of one metric into one series per value of a
 * label (e.g. per `deployment_id`), keeping that label on the result.
 */
export function groupSeriesBy(
  result: MetricsResult | undefined,
  metric: string,
  labelKey: string,
  mode: 'sum' | 'avg' | 'max' | 'weighted',
  weightMetric?: string,
): MetricSeries[] {
  const list = filterSeries(result, metric);
  const weights = weightMetric ? filterSeries(result, weightMetric) : [];
  const groups = new Map<string, MetricSeries[]>();
  for (const s of list) {
    const key = s.labels[labelKey];
    if (!key) continue;
    groups.set(key, [...(groups.get(key) ?? []), s]);
  }
  const out: MetricSeries[] = [];
  for (const [key, members] of groups) {
    const combined = combineSeries(members, mode, weights);
    if (combined) out.push({ ...combined, labels: { [labelKey]: key } });
  }
  return out;
}

/** Wrap derived series as a result so chart components can take it. */
export function asResult(series: MetricSeries[], from = '', to = '', step = ''): MetricsResult {
  return { from, to, step, series };
}
