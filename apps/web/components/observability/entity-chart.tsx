'use client';

import { useMemo } from 'react';
import { ChartCard, TimeSeriesChart } from '@/components/charts/time-series-chart';
import type { ChartSeries, ChartThreshold, SeriesKind } from '@/components/charts/time-series-chart';
import { filterSeries, latestValue, mergeSeries } from '@/components/charts/series-utils';
import type { MetricsResult } from '@/types/metrics';
import { formatterForUnit } from '@/utils/metrics-format';

export interface ChartEntity {
  /** Value of the series label that identifies the entity (device_id / instance_id …). */
  id: string;
  label: string;
  color: string;
}

interface EntityMetricChartProps {
  title: string;
  description?: string;
  result: MetricsResult | undefined;
  metric: string;
  /** Series label that carries the entity id. */
  labelKey: 'device_id' | 'instance_id' | 'deployment_id';
  entities: ChartEntity[];
  fromMs: number;
  toMs: number;
  /** Override the unit the backend reports (for formatting). */
  unit?: string;
  kind?: SeriesKind;
  stacked?: boolean;
  thresholds?: ChartThreshold[];
  yDomain?: [number | 'auto', number | 'auto'];
  height?: number;
  refreshing?: boolean;
  headerExtra?: React.ReactNode;
  className?: string;
  emptyTitle?: string;
  emptyDescription?: string;
}

/**
 * One metric, one line per entity, in entity colours. The legend carries
 * each entity's latest value so identity and number never depend on colour.
 */
export function EntityMetricChart({
  title,
  description,
  result,
  metric,
  labelKey,
  entities,
  fromMs,
  toMs,
  unit,
  kind = 'line',
  stacked = false,
  thresholds,
  yDomain,
  height = 220,
  refreshing,
  headerExtra,
  className,
  emptyTitle,
  emptyDescription,
}: EntityMetricChartProps) {
  const seriesUnit = unit ?? filterSeries(result, metric)[0]?.unit ?? '';
  const format = useMemo(() => formatterForUnit(seriesUnit), [seriesUnit]);

  const { data, series } = useMemo(() => {
    const present = entities.filter((e) => filterSeries(result, metric, (l) => l[labelKey] === e.id).length > 0);
    const entries = present.map((e) => ({ key: e.id, series: filterSeries(result, metric, (l) => l[labelKey] === e.id)[0] }));
    const chartSeries: ChartSeries[] = present.map((e) => {
      const latest = latestValue(entries.find((x) => x.key === e.id)?.series);
      return {
        key: e.id,
        label: `${e.label} · ${format(latest, false)}`,
        color: e.color,
        kind,
        stackId: stacked ? 'stack' : undefined,
        strokeWidth: kind === 'line' ? 1.75 : undefined,
      };
    });
    return { data: mergeSeries(entries), series: chartSeries };
  }, [entities, result, metric, labelKey, kind, stacked, format]);

  return (
    <ChartCard title={title} description={description} headerExtra={headerExtra} className={className} dense>
      <TimeSeriesChart
        data={data}
        series={series}
        fromMs={fromMs}
        toMs={toMs}
        formatValue={(v, compact) => format(v, compact)}
        height={height}
        thresholds={thresholds}
        yDomain={yDomain}
        refreshing={refreshing}
        emptyTitle={emptyTitle}
        emptyDescription={emptyDescription}
      />
    </ChartCard>
  );
}

export interface MetricLine {
  metric: string;
  label: string;
  color: string;
  kind?: SeriesKind;
  dashed?: boolean;
  /** Pick one series when the result carries several (e.g. one device). */
  match?: (labels: Record<string, string>) => boolean;
}

interface MetricsChartCardProps {
  title: string;
  description?: string;
  result: MetricsResult | undefined;
  lines: MetricLine[];
  fromMs: number;
  toMs: number;
  unit?: string;
  thresholds?: ChartThreshold[];
  yDomain?: [number | 'auto', number | 'auto'];
  height?: number;
  refreshing?: boolean;
  className?: string;
  emptyTitle?: string;
  emptyDescription?: string;
  /** Treat an all-zero range as empty (event counts). */
  zeroIsEmpty?: boolean;
  /** Stack the lines (parts of a whole, e.g. cached + computed tokens). */
  stacked?: boolean;
}

/**
 * A few metrics of the same unit on one chart (never two y-scales). With one
 * line the title names it and the header shows its latest value; with more,
 * the legend carries each line's latest value.
 */
export function MetricsChartCard({
  title,
  description,
  result,
  lines,
  fromMs,
  toMs,
  unit,
  thresholds,
  yDomain,
  height = 200,
  refreshing,
  className,
  emptyTitle,
  emptyDescription,
  zeroIsEmpty = false,
  stacked = false,
}: MetricsChartCardProps) {
  const seriesUnit = unit ?? filterSeries(result, lines[0]?.metric ?? '')[0]?.unit ?? '';
  const format = useMemo(() => formatterForUnit(seriesUnit), [seriesUnit]);

  const { data, series, single } = useMemo(() => {
    const entries = lines.map((line) => ({ key: line.metric, series: filterSeries(result, line.metric, line.match)[0] }));
    const chartSeries: ChartSeries[] = lines
      // A metric the device does not report (all nulls) gets no legend entry.
      .filter((line) => entries.find((e) => e.key === line.metric)?.series?.points.some((p) => p.v !== null))
      .map((line) => ({
        key: line.metric,
        label: lines.length > 1 ? `${line.label} · ${format(latestValue(entries.find((e) => e.key === line.metric)?.series), false)}` : line.label,
        color: line.color,
        kind: line.kind ?? (lines.length > 1 && !stacked ? 'line' : 'area'),
        dashed: line.dashed,
        stackId: stacked ? 'stack' : undefined,
      }));
    const first = entries[0]?.series;
    const allZero = entries.every((e) => (e.series?.points ?? []).every((p) => !p.v));
    return {
      data: zeroIsEmpty && allZero ? [] : mergeSeries(entries),
      series: chartSeries,
      single: lines.length === 1 ? latestValue(first) : null,
    };
  }, [lines, result, format, zeroIsEmpty, stacked]);

  return (
    <ChartCard
      title={title}
      description={description}
      className={className}
      dense
      headerExtra={
        lines.length === 1 && single !== null ? (
          <span className="text-sm font-semibold tabular-nums">{format(single, false)}</span>
        ) : undefined
      }
    >
      <TimeSeriesChart
        data={data}
        series={series}
        fromMs={fromMs}
        toMs={toMs}
        formatValue={(v, compact) => format(v, compact)}
        height={height}
        thresholds={thresholds}
        yDomain={yDomain}
        refreshing={refreshing}
        emptyTitle={emptyTitle}
        emptyDescription={emptyDescription}
      />
    </ChartCard>
  );
}
