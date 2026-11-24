'use client';

import { useId } from 'react';
import { Area, AreaChart, ReferenceLine, ResponsiveContainer, YAxis } from 'recharts';
import { cn } from '@/lib/utils';

interface MiniChartProps {
  /** Values in time order; null = gap. */
  values: (number | null)[];
  /** Theme colour string. */
  color?: string;
  height?: number;
  /** Fixed y range (e.g. [0, 100] for percentages) so rows compare at a glance. */
  domain?: [number, number];
  /** Horizontal guide, e.g. a VRAM total or a temperature limit. */
  reference?: number;
  className?: string;
}

/**
 * Small inline area for table rows and device cards — a sparkline with a
 * fixed domain option so a 40% card and a 90% card look different.
 * Decorative: the figure next to it carries the value for assistive tech.
 */
export function MiniChart({ values, color = 'rgb(var(--chart-2))', height = 40, domain, reference, className }: MiniChartProps) {
  const gradientId = `mini-${useId().replace(/[^a-zA-Z0-9_-]/g, '')}`;
  const data = values.map((v, i) => ({ i, v }));
  const hasData = values.some((v) => v !== null);

  if (!hasData) {
    return <div aria-hidden className={cn('flex items-center', className)} style={{ height }}><span className="h-px w-full bg-border" /></div>;
  }

  return (
    <div aria-hidden className={cn('w-full', className)} style={{ height }}>
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 3, right: 0, bottom: 0, left: 0 }}>
          <defs>
            <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={color} stopOpacity={0.22} />
              <stop offset="100%" stopColor={color} stopOpacity={0} />
            </linearGradient>
          </defs>
          <YAxis hide domain={domain ?? [0, 'auto']} />
          {reference !== undefined && <ReferenceLine y={reference} stroke="rgb(var(--border))" strokeDasharray="3 3" />}
          <Area type="monotone" dataKey="v" stroke={color} strokeWidth={1.5} fill={`url(#${gradientId})`} dot={false} activeDot={false} connectNulls={false} isAnimationActive={false} />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

interface MiniStatProps {
  label: string;
  value: string;
  values: (number | null)[];
  color?: string;
  domain?: [number, number];
  reference?: number;
  /** Secondary text under the value, e.g. "of 45.0 GiB". */
  hint?: string;
  className?: string;
}

/** Label + value + mini chart, the unit cell of the device cards. */
export function MiniStat({ label, value, values, color, domain, reference, hint, className }: MiniStatProps) {
  return (
    <div className={cn('min-w-0', className)}>
      <div className="flex items-baseline justify-between gap-2">
        <span className="truncate text-[11px] font-medium text-muted-foreground">{label}</span>
        <span className="shrink-0 text-sm font-semibold tabular-nums">{value}</span>
      </div>
      {hint && <p className="-mt-0.5 text-right text-[10px] text-muted-foreground">{hint}</p>}
      <MiniChart values={values} color={color} domain={domain} reference={reference} height={34} className="-mx-0.5 mt-1" />
    </div>
  );
}
