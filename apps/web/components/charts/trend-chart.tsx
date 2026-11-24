'use client';

import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import type { TooltipContentProps } from 'recharts';
import { LineChart as LineChartIcon } from 'lucide-react';
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs';
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

/** One line/area on the chart. `key` is a numeric field on every datum. */
export interface TrendSeries {
  key: string;
  label: string;
  /** CSS colour string, e.g. 'rgb(var(--chart-2))'. */
  color: string;
}

/** A point: `x` is the unique x key (ISO date/time); series keys are numbers. */
export type TrendDatum = { x: string } & Record<string, string | number>;

export interface TrendTab {
  value: string;
  label: string;
}

interface TrendChartProps {
  title: string;
  description?: string;
  data: TrendDatum[];
  series: TrendSeries[];
  /** Axis tick for an x value, e.g. "Nov 3". */
  formatTick: (x: string) => string;
  /** Tooltip title for an x value, e.g. "Mon, Nov 3, 2026". */
  formatLabel?: (x: string) => string;
  /** Formats a series value; `compact` for axis ticks. */
  formatValue: (value: number, compact: boolean) => string;
  /** Optional metric switcher in the header. */
  tabs?: TrendTab[];
  activeTab?: string;
  onTabChange?: (value: string) => void;
  emptyTitle?: string;
  emptyDescription?: string;
  /** Chart height in px (260 on the dashboard). */
  height?: number;
  /** Extra header controls (date range etc.). */
  headerExtra?: React.ReactNode;
}

/**
 * Area chart card ("Usage over time"): header with
 * title/description and optional tabs, legend when there are several series,
 * tokens for grid/axes/tooltip, and an sr-only table with the same numbers.
 */
export function TrendChart({
  title,
  description,
  data,
  series,
  formatTick,
  formatLabel = (x) => x,
  formatValue,
  tabs,
  activeTab,
  onTabChange,
  emptyTitle = 'No data in this period',
  emptyDescription = 'Samples will chart here as they arrive.',
  height = 260,
  headerExtra,
}: TrendChartProps) {
  const hasData = data.some((d) => series.some((s) => Number(d[s.key]) > 0));

  function renderTooltip({ active, label }: TooltipContentProps) {
    if (!active || label === undefined) return null;
    const datum = data.find((d) => d.x === String(label));
    if (!datum) return null;
    return (
      <ChartTooltipCard
        title={formatLabel(datum.x)}
        rows={series.map((s) => ({
          key: s.key,
          label: s.label,
          value: formatValue(Number(datum[s.key]), false),
          color: s.color,
        }))}
      />
    );
  }

  return (
    <Card className="h-full gap-0 rounded-xl py-0">
      <CardHeader className="flex flex-col gap-3 border-b border-border px-5 py-4 sm:flex-row sm:items-center sm:justify-between">
        <div className="space-y-0.5">
          <CardTitle className="text-sm font-semibold">{title}</CardTitle>
          {description && <CardDescription>{description}</CardDescription>}
        </div>
        <div className="flex items-center gap-2">
          {headerExtra}
          {tabs && tabs.length > 0 && (
            <Tabs
              value={activeTab}
              onValueChange={(v) => onTabChange?.(String(v))}
            >
              <TabsList className="bg-muted">
                {tabs.map((tab) => (
                  <TabsTrigger
                    key={tab.value}
                    value={tab.value}
                    className="px-3 text-xs"
                  >
                    {tab.label}
                  </TabsTrigger>
                ))}
              </TabsList>
            </Tabs>
          )}
        </div>
      </CardHeader>

      <CardContent className="relative px-2 pb-4 pt-4 sm:px-4">
        {!hasData ? (
          <ChartEmpty
            title={emptyTitle}
            description={emptyDescription}
            height={height}
          />
        ) : (
          <>
            {series.length > 1 && (
              <ul className="mb-2 flex flex-wrap gap-4 px-3 text-xs text-muted-foreground">
                {series.map((s) => (
                  <li key={s.key} className="flex items-center gap-1.5">
                    <span
                      aria-hidden
                      className="h-0.5 w-3 rounded-full"
                      style={{ backgroundColor: s.color }}
                    />
                    {s.label}
                  </li>
                ))}
              </ul>
            )}
            <div style={{ height }} aria-hidden>
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart
                  data={data}
                  margin={{ top: 8, right: 12, bottom: 0, left: 0 }}
                >
                  <CartesianGrid vertical={false} stroke={GRID_STROKE} />
                  <XAxis
                    dataKey="x"
                    tickFormatter={formatTick}
                    tickLine={false}
                    axisLine={false}
                    minTickGap={28}
                    tickMargin={8}
                    tick={AXIS_TICK}
                  />
                  <YAxis
                    tickFormatter={(v: number) => formatValue(v, true)}
                    tickLine={false}
                    axisLine={false}
                    width={56}
                    tick={AXIS_TICK}
                  />
                  <Tooltip content={renderTooltip} cursor={TOOLTIP_CURSOR} />
                  {series.map((s) => (
                    <Area
                      key={s.key}
                      type="monotone"
                      dataKey={s.key}
                      name={s.label}
                      stroke={s.color}
                      strokeWidth={2}
                      fill={s.color}
                      fillOpacity={0.1}
                      dot={false}
                      activeDot={ACTIVE_DOT}
                      animationDuration={600}
                    />
                  ))}
                </AreaChart>
              </ResponsiveContainer>
            </div>

            {/* Same numbers as a table for screen readers (the chart is aria-hidden). */}
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
                  {data.map((d) => (
                    <TableRow key={d.x}>
                      <TableCell>{formatLabel(d.x)}</TableCell>
                      {series.map((s) => (
                        <TableCell key={s.key}>
                          {formatValue(Number(d[s.key]), false)}
                        </TableCell>
                      ))}
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          </>
        )}
      </CardContent>
    </Card>
  );
}

/** Chart-level empty state (icon, bold line, muted line). */
export function ChartEmpty({
  title,
  description,
  height = 260,
}: {
  title: string;
  description?: string;
  height?: number;
}) {
  return (
    <div
      className="flex flex-col items-center justify-center gap-2 text-center"
      style={{ height }}
    >
      <LineChartIcon className="size-6 text-muted-foreground/60" />
      <p className="text-sm font-medium">{title}</p>
      {description && (
        <p className="text-xs text-muted-foreground">{description}</p>
      )}
    </div>
  );
}
