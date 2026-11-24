'use client';

import Link from 'next/link';
import { ArrowDownRight, ArrowUpRight, Minus } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { cn } from '@/lib/utils';
import { Sparkline } from '@/components/charts/sparkline';

/** Which way is good news for this metric — decides the change pill's colour. */
export type GoodDirection = 'up' | 'down' | 'neutral';

interface MetricCardProps {
  label: string;
  value: string;
  icon: LucideIcon;
  /** Backend change vs the previous period, e.g. "+12.5%"; null when the metric keeps no history. */
  change: string | null;
  goodDirection?: GoodDirection;
  /** Shown instead of the change pill when there is no change. */
  footnote?: string;
  /** Caption after the change pill (default "vs previous period"). */
  changeLabel?: string;
  /** Secondary figure under the value, e.g. "p50 118 ms" or "3 waiting". */
  detail?: React.ReactNode;
  trend?: number[];
  /** Where the card leads; omit for a static card (no permission for the page). */
  href?: string;
}

type Tone = 'good' | 'bad' | 'neutral';

const TONE_CLASS: Record<Tone, string> = {
  good: 'bg-success/10 text-success-dark dark:text-success-light',
  bad: 'bg-error/10 text-error-dark dark:text-error-light',
  neutral: 'bg-muted text-muted-foreground',
};

function changeTone(direction: 'up' | 'down' | 'flat', good: GoodDirection): Tone {
  if (direction === 'flat' || good === 'neutral') return 'neutral';
  return direction === good ? 'good' : 'bad';
}

function ChangePill({ change, goodDirection }: { change: string; goodDirection: GoodDirection }) {
  const n = Number.parseFloat(change);
  const direction = Number.isNaN(n) || n === 0 ? 'flat' : n > 0 ? 'up' : 'down';
  const Icon = direction === 'up' ? ArrowUpRight : direction === 'down' ? ArrowDownRight : Minus;
  return (
    <span
      className={cn(
        'inline-flex items-center gap-0.5 rounded-full px-1.5 py-0.5 text-[11px] font-semibold tabular-nums',
        TONE_CLASS[changeTone(direction, goodDirection)],
      )}
    >
      <Icon className="size-3" aria-hidden />
      {change}
    </span>
  );
}

export function MetricCard({
  label,
  value,
  icon: Icon,
  change,
  goodDirection = 'neutral',
  footnote,
  changeLabel = 'vs previous period',
  detail,
  trend,
  href,
}: MetricCardProps) {
  const body = (
    <>
      {/* Hairline brand glint along the top edge on hover. */}
      <span
        aria-hidden
        className="pointer-events-none absolute inset-x-6 top-0 h-px bg-gradient-to-r from-transparent via-brand/50 to-transparent opacity-0 transition-opacity duration-300 group-hover/metric:opacity-100"
      />
      <div className="flex items-start justify-between gap-3">
        <p className="text-[13px] font-medium text-muted-foreground">{label}</p>
        <span className="flex size-8 shrink-0 items-center justify-center rounded-lg border border-border bg-muted/60 text-muted-foreground transition-colors duration-200 group-hover/metric:text-foreground">
          <Icon className="size-4" />
        </span>
      </div>
      <p className="mt-1 text-[28px] font-semibold leading-tight tracking-tight">{value}</p>
      {detail && <p className="mt-0.5 truncate text-xs text-muted-foreground tabular-nums">{detail}</p>}
      <div className="mt-2 flex min-h-5 items-center gap-2 text-xs">
        {change ? (
          <>
            <ChangePill change={change} goodDirection={goodDirection} />
            <span className="truncate text-muted-foreground">{changeLabel}</span>
          </>
        ) : (
          <span className="text-muted-foreground">{footnote}</span>
        )}
      </div>
      {trend && trend.length >= 2 && (
        <div className="-mx-1 mt-3">
          <Sparkline values={trend} />
        </div>
      )}
    </>
  );

  const base =
    'group/metric relative flex flex-col overflow-hidden rounded-xl border border-border bg-card p-5 text-card-foreground shadow-premium transition-all duration-200';

  if (!href) return <div className={base}>{body}</div>;

  return (
    <Link
      href={href}
      className={cn(
        base,
        'outline-none hover:-translate-y-0.5 hover:border-foreground/15 hover:shadow-premium-lg focus-visible:ring-2 focus-visible:ring-ring/30',
      )}
    >
      {body}
    </Link>
  );
}
