'use client';

import { useId } from 'react';
import {
  Area,
  Bar,
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import type { TooltipContentProps } from 'recharts';
import { cn } from '@/lib/utils';
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { ChartTooltipCard } from '@/components/charts/chart-tooltip';
import {
  ACTIVE_DOT,
  AXIS_TICK,
  GRID_STROKE,
  TOOLTIP_CURSOR,
} from '@/components/charts/chart-theme';
import { ChartEmpty } from '@/components/charts/trend-chart';
import type { ChartRow } from '@/components/charts/series-utils';
import {
  labelFormatterFor,
  tickFormatterFor,
} from '@/components/charts/series-utils';

export type SeriesKind = 'area' | 'line' | 'bar';

export interface ChartSeries {
  key: string;
  label: string;
  /** CSS colour string from the theme, e.g. 'rgb(var(--chart-2))'. */
  color: string;
  kind?: SeriesKind;
  dashed?: boolean;
  /** Bars / areas with the same stackId stack. */
  stackId?: string;
  strokeWidth?: number;
}

export interface ChartThreshold {
  value: number;
  label: string;
  /** Theme colour string; defaults to the warning colour. */
  color?: string;
}

export interface TimeSeriesChartProps {
  data: ChartRow[];
  series: ChartSeries[];
  fromMs: number;
  toMs: number;
  /** Formats a value for the tooltip / table (`compact` for axis ticks). */
  formatValue: (value: number | null, compact: boolean) => string;
  height?: number;
  thresholds?: ChartThreshold[];
  /** Fix the y range, e.g. [0, 100] for percentages. */
  yDomain?: [number | 'auto', number | 'auto'];
  /** Hide the legend even with several series (when a sibling list carries identity). */
  hideLegend?: boolean;
  className?: string;
  emptyTitle?: string;
  emptyDescription?: string;
  /** Dim while a refetch is in flight — no skeleton, no layout jump. */
  refreshing?: boolean;
}

/**
 * Time-series plot: epoch x-axis with span-aware ticks, hairline grid, 2px
 * lines / 10% area washes / thin bars, crosshair tooltip listing every
 * series (value first), optional threshold lines, sr-only table twin.
 */
export function TimeSeriesChart({
  data,
  series,
  fromMs,
  toMs,
  formatValue,
  height = 240,
  thresholds = [],
  yDomain,
  hideLegend = false,
  className,
  emptyTitle = 'No samples in this range',
  emptyDescription = 'Points chart here as the agents report.',
  refreshing = false,
}: TimeSeriesChartProps) {
  const gradientPrefix = `g-${useId().replace(/[^a-zA-Z0-9_-]/g, '')}`;
  const tick = tickFormatterFor(fromMs, toMs);
  const label = labelFormatterFor(fromMs, toMs);
  const hasData = data.some((row) =>
    series.some((s) => row[s.key] !== null && row[s.key] !== undefined),
  );

  function renderTooltip({ active, label: x }: TooltipContentProps) {
    if (!active || x === undefined || x === null) return null;
    const row = data.find((d) => d.t === Number(x));
    if (!row) return null;
    return (
      <ChartTooltipCard
        title={label(row.t)}
        rows={series.map((s) => ({
          key: s.key,
          label: s.label,
          value: formatValue(row[s.key] ?? null, false),
          color: s.color,
        }))}
      />
    );
  }

  if (!hasData)
    return (
      <ChartEmpty
        title={emptyTitle}
        description={emptyDescription}
        height={height}
      />
    );

  const legendNeeded = !hideLegend && series.length > 1;

  return (
    // `relative` keeps the sr-only table inside the card (absolute elements otherwise
    // escape <main>'s scroll box and make the whole document scroll).
    <div
      className={cn(
        'relative transition-opacity duration-300',
        refreshing && 'opacity-60',
        className,
      )}
    >
      {legendNeeded && (
        <ul className="mb-2 flex flex-wrap gap-x-4 gap-y-1 px-3 text-xs text-muted-foreground">
          {series.map((s) => (
            <li key={s.key} className="flex items-center gap-1.5">
              <span
                aria-hidden
                className={cn(
                  'h-0.5 w-3 rounded-full',
                  s.kind === 'bar' && 'h-2.5 w-2.5 rounded-sm',
                )}
                style={
                  s.dashed
                    ? {
                        backgroundImage: `repeating-linear-gradient(90deg, ${s.color} 0 3px, transparent 3px 5px)`,
                      }
                    : { backgroundColor: s.color }
                }
              />
              {s.label}
            </li>
          ))}
        </ul>
      )}
      <div style={{ height }} aria-hidden>
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart
            data={data}
            margin={{ top: 8, right: 12, bottom: 0, left: 0 }}
            barCategoryGap="20%"
          >
            <defs>
              {series.map((s) => (
                <linearGradient
                  key={s.key}
                  id={`${gradientPrefix}-${s.key}`}
                  x1="0"
                  y1="0"
                  x2="0"
                  y2="1"
                >
                  <stop offset="0%" stopColor={s.color} stopOpacity={0.18} />
                  <stop offset="100%" stopColor={s.color} stopOpacity={0.02} />
                </linearGradient>
              ))}
            </defs>
            <CartesianGrid vertical={false} stroke={GRID_STROKE} />
            <XAxis
              dataKey="t"
              type="number"
              scale="time"
              domain={[fromMs, toMs]}
              tickFormatter={tick}
              tickLine={false}
              axisLine={false}
              minTickGap={36}
              tickMargin={8}
              tick={AXIS_TICK}
            />
            <YAxis
              domain={yDomain ?? [0, 'auto']}
              tickFormatter={(v: number) => formatValue(v, true)}
              tickLine={false}
              axisLine={false}
              width={56}
              tick={AXIS_TICK}
              allowDecimals
            />
            <Tooltip
              content={renderTooltip}
              cursor={TOOLTIP_CURSOR}
              isAnimationActive={false}
            />
            {thresholds.map((th) => (
              <ReferenceLine
                key={th.label}
                y={th.value}
                stroke={th.color ?? 'rgb(var(--warning-rgb, 201 138 23))'}
                strokeDasharray="4 4"
                strokeWidth={1}
                ifOverflow="extendDomain"
                label={{
                  value: th.label,
                  position: 'insideTopRight',
                  fontSize: 10,
                  fill: 'rgb(var(--muted-foreground))',
                }}
              />
            ))}
            {series.map((s) => {
              const kind = s.kind ?? 'area';
              if (kind === 'bar') {
                return (
                  <Bar
                    key={s.key}
                    dataKey={s.key}
                    name={s.label}
                    fill={s.color}
                    stackId={s.stackId}
                    radius={s.stackId ? 0 : [3, 3, 0, 0]}
                    maxBarSize={24}
                    isAnimationActive={false}
                  />
                );
              }
              if (kind === 'line') {
                return (
                  <Line
                    key={s.key}
                    type="monotone"
                    dataKey={s.key}
                    name={s.label}
                    stroke={s.color}
                    strokeWidth={s.strokeWidth ?? 2}
                    strokeDasharray={s.dashed ? '5 4' : undefined}
                    dot={false}
                    activeDot={ACTIVE_DOT}
                    connectNulls={false}
                    isAnimationActive={false}
                  />
                );
              }
              return (
                <Area
                  key={s.key}
                  type="monotone"
                  dataKey={s.key}
                  name={s.label}
                  stroke={s.color}
                  strokeWidth={s.strokeWidth ?? 2}
                  strokeDasharray={s.dashed ? '5 4' : undefined}
                  fill={
                    s.stackId ? s.color : `url(#${gradientPrefix}-${s.key})`
                  }
                  fillOpacity={s.stackId ? 0.22 : 1}
                  stackId={s.stackId}
                  dot={false}
                  activeDot={ACTIVE_DOT}
                  connectNulls={false}
                  isAnimationActive={false}
                />
              );
            })}
          </ComposedChart>
        </ResponsiveContainer>
      </div>

      <div className="sr-only">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Time</TableHead>
              {series.map((s) => (
                <TableHead key={s.key}>{s.label}</TableHead>
              ))}
            </TableRow>
          </TableHeader>
          <TableBody>
            {data.map((row) => (
              <TableRow key={row.t}>
                <TableCell>{label(row.t)}</TableCell>
                {series.map((s) => (
                  <TableCell key={s.key}>
                    {formatValue(row[s.key] ?? null, false)}
                  </TableCell>
                ))}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </div>
  );
}

interface ChartCardProps {
  title: string;
  description?: string;
  /** Right side of the header: tabs, a current-value readout, a link. */
  headerExtra?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
  /** Tighter padding for dense grids. */
  dense?: boolean;
}

/** Card chrome shared with the "Usage over time" card. */
export function ChartCard({
  title,
  description,
  headerExtra,
  children,
  className,
  dense = false,
}: ChartCardProps) {
  return (
    <Card className={cn('h-full gap-0 rounded-xl py-0', className)}>
      <CardHeader
        className={cn(
          'flex flex-row items-start justify-between gap-3 border-b border-border',
          dense ? 'px-4 py-3' : 'px-5 py-4',
        )}
      >
        <div className="min-w-0 space-y-0.5">
          <CardTitle className="text-sm font-semibold">{title}</CardTitle>
          {description && (
            <CardDescription className="truncate">
              {description}
            </CardDescription>
          )}
        </div>
        {headerExtra && (
          <div className="flex shrink-0 items-center gap-2">{headerExtra}</div>
        )}
      </CardHeader>
      <CardContent
        className={cn('pb-4 pt-4', dense ? 'px-2 sm:px-3' : 'px-2 sm:px-4')}
      >
        {children}
      </CardContent>
    </Card>
  );
}

/** Small "current value" readout for a chart header. */
export function ChartReadout({
  label,
  value,
  color,
}: {
  label: string;
  value: string;
  color?: string;
}) {
  return (
    <span className="inline-flex items-baseline gap-1.5 text-xs text-muted-foreground">
      {color && (
        <span
          aria-hidden
          className="inline-block h-0.5 w-3 translate-y-[-3px] rounded-full"
          style={{ backgroundColor: color }}
        />
      )}
      <span className="text-sm font-semibold tabular-nums text-foreground">
        {value}
      </span>
      {label}
    </span>
  );
}
