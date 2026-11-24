'use client';

import Link from 'next/link';
import type { LucideIcon } from 'lucide-react';
import { cn } from '@/lib/utils';
import { MiniChart } from '@/components/charts/mini-chart';

export type StatTone = 'default' | 'good' | 'warn' | 'bad';

interface StatTileProps {
  label: string;
  value: string;
  /** Smaller text after the value, e.g. "/ 45 GiB" or "p95". */
  unit?: string;
  /** Second line: context, e.g. "p50 118 ms". */
  hint?: string;
  icon?: LucideIcon;
  tone?: StatTone;
  trend?: (number | null)[];
  trendColor?: string;
  trendDomain?: [number, number];
  href?: string;
  className?: string;
}

const TONE_VALUE: Record<StatTone, string> = {
  default: 'text-foreground',
  good: 'text-success-dark dark:text-success-light',
  warn: 'text-warning-dark dark:text-warning-light',
  bad: 'text-error-dark dark:text-error-light',
};

/**
 * Dense KPI tile for the observability pages — smaller than MetricCard,
 * proportional figures (no tabular-nums on the big number), optional
 * trend strip. Tone colours the figure only when the number *is* a state.
 */
export function StatTile({ label, value, unit, hint, icon: Icon, tone = 'default', trend, trendColor, trendDomain, href, className }: StatTileProps) {
  const body = (
    <>
      <div className="flex items-center justify-between gap-2">
        <p className="truncate text-[12px] font-medium text-muted-foreground">{label}</p>
        {Icon && <Icon className="size-3.5 shrink-0 text-muted-foreground/70" aria-hidden />}
      </div>
      <p className={cn('mt-1 flex items-baseline gap-1 text-[22px] font-semibold leading-none tracking-tight', TONE_VALUE[tone])}>
        {value}
        {unit && <span className="text-xs font-medium text-muted-foreground">{unit}</span>}
      </p>
      {hint && <p className="mt-1.5 truncate text-[11px] text-muted-foreground">{hint}</p>}
      {trend && trend.length > 1 && <MiniChart values={trend} color={trendColor ?? 'rgb(var(--chart-2))'} domain={trendDomain} height={30} className="-mx-1 mt-2" />}
    </>
  );
  const base = 'group/stat relative flex flex-col rounded-xl border border-border bg-card p-4 text-card-foreground shadow-premium transition-all duration-200';
  if (!href) return <div className={cn(base, className)}>{body}</div>;
  return (
    <Link href={href} className={cn(base, 'outline-none hover:-translate-y-0.5 hover:border-foreground/15 hover:shadow-premium-lg focus-visible:ring-2 focus-visible:ring-ring/30', className)}>
      {body}
    </Link>
  );
}
