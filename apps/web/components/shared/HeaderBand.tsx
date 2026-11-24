'use client';

import { cn } from '@/lib/utils';

export type OverallStatus = 'operational' | 'degraded' | 'outage';

const STATUS_CHIP: Record<OverallStatus, { label: string; dot: string }> = {
  operational: { label: 'All systems operational', dot: 'bg-success' },
  degraded: { label: 'System degraded', dot: 'bg-warning' },
  outage: { label: 'Service outage', dot: 'bg-error' },
};

/** Pill with a (pinging when operational) status dot — sits above the band title. */
export function StatusChip({ status, label }: { status: OverallStatus; label?: string }) {
  const chip = STATUS_CHIP[status];
  return (
    <span className="inline-flex items-center gap-2 rounded-full border border-border bg-background/70 px-2.5 py-1 text-[11px] font-medium text-muted-foreground backdrop-blur">
      <span className="relative flex size-2">
        {status === 'operational' && (
          <span className={cn('absolute inline-flex size-full animate-ping rounded-full opacity-60', chip.dot)} />
        )}
        <span className={cn('relative inline-flex size-2 rounded-full', chip.dot)} />
      </span>
      {label ?? chip.label}
    </span>
  );
}

interface HeaderBandProps {
  /** Status chip or any small element above the title. */
  eyebrow?: React.ReactNode;
  title: string;
  description?: string;
  /** Right-aligned controls (date range filter, rounded-full buttons). */
  actions?: React.ReactNode;
}

/**
 * Dashboard header band: rounded-2xl card with a dot grid and a soft brand
 * glow. Use for overview-style pages; list
 * pages use `PageHeader`.
 */
export function HeaderBand({ eyebrow, title, description, actions }: HeaderBandProps) {
  return (
    <section className="relative overflow-hidden rounded-2xl border border-border bg-card px-5 py-6 shadow-premium sm:px-8 sm:py-7">
      <div
        aria-hidden
        className="bg-dot-grid pointer-events-none absolute inset-0 [mask-image:linear-gradient(to_left,black,transparent_65%)]"
      />
      <div
        aria-hidden
        className="pointer-events-none absolute -right-20 -top-24 size-72 rounded-full bg-brand/10 blur-3xl"
      />

      <div className="relative flex flex-col gap-6 lg:flex-row lg:items-end lg:justify-between">
        <div className="space-y-2.5">
          {eyebrow}
          <h1 className="text-2xl font-semibold tracking-tight sm:text-[28px]">{title}</h1>
          {description && <p className="max-w-xl text-sm text-muted-foreground">{description}</p>}
        </div>

        {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
      </div>
    </section>
  );
}

/** Time-of-day greeting for the dashboard band. */
export function greeting(now: Date = new Date()): string {
  const hour = now.getHours();
  if (hour < 12) return 'Good morning';
  if (hour < 18) return 'Good afternoon';
  return 'Good evening';
}
