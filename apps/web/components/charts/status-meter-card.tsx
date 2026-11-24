'use client';

import { AlertCircle, CheckCircle2, XCircle } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { cn } from '@/lib/utils';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import type { OverallStatus } from '@/components/shared/HeaderBand';

const STATUS_CONFIG: Record<OverallStatus, { label: string; className: string; Icon: LucideIcon }> = {
  operational: {
    label: 'Operational',
    className: 'border-success/30 bg-success/10 text-success-dark dark:text-success-light',
    Icon: CheckCircle2,
  },
  degraded: {
    label: 'Degraded',
    className: 'border-warning/30 bg-warning/10 text-warning-dark dark:text-warning-light',
    Icon: AlertCircle,
  },
  outage: {
    label: 'Outage',
    className: 'border-error/30 bg-error/10 text-error-dark dark:text-error-light',
    Icon: XCircle,
  },
};

export interface StatusMeterItem {
  label: string;
  /** Formatted value shown on the right, e.g. "18.2 GB". */
  value: string;
  /** 0–100 */
  percent: number;
  /** Bar colour class: bg-success | bg-warning | bg-error | bg-chart-2 … */
  barClass: string;
}

interface StatusMeterCardProps {
  title: string;
  description?: string;
  status?: OverallStatus;
  meters: StatusMeterItem[];
  /** Extra rows under the meters (e.g. a service dot row). */
  children?: React.ReactNode;
  /** Left / right text in the footer line. */
  footerLeft?: React.ReactNode;
  footerRight?: React.ReactNode;
}

/**
 * Card of labelled progress meters with a status chip in the header, for
 * GPU utilisation, VRAM, power and temperature.
 */
export function StatusMeterCard({
  title,
  description,
  status,
  meters,
  children,
  footerLeft,
  footerRight,
}: StatusMeterCardProps) {
  const cfg = status ? STATUS_CONFIG[status] : null;
  const StatusIcon = cfg?.Icon;

  return (
    <Card className="h-full gap-0 rounded-xl py-0">
      <CardHeader className="flex flex-row items-start justify-between gap-3 border-b border-border px-5 py-4">
        <div className="space-y-0.5">
          <CardTitle className="text-sm font-semibold">{title}</CardTitle>
          {description && <CardDescription>{description}</CardDescription>}
        </div>
        {cfg && StatusIcon && (
          <span
            className={cn(
              'inline-flex shrink-0 items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-semibold',
              cfg.className,
            )}
          >
            <StatusIcon className="size-3" />
            {cfg.label}
          </span>
        )}
      </CardHeader>
      <CardContent className="flex flex-1 flex-col px-5 py-4">
        <div className="space-y-4">
          {meters.map((meter) => (
            <StatusMeter key={meter.label} {...meter} />
          ))}
          {children}
        </div>

        {(footerLeft || footerRight) && (
          <div className="mt-auto flex items-center justify-between border-t border-border pt-4 text-[11px] text-muted-foreground">
            <span>{footerLeft}</span>
            <span>{footerRight}</span>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

export function StatusMeter({ label, value, percent, barClass }: StatusMeterItem) {
  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between text-xs">
        <span className="text-muted-foreground">{label}</span>
        <span className="font-semibold tabular-nums">{value}</span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-muted">
        <div
          className={cn('h-full rounded-full transition-[width] duration-700 ease-out', barClass)}
          style={{ width: `${Math.min(Math.max(percent, 0), 100).toFixed(1)}%` }}
        />
      </div>
    </div>
  );
}

/** A label with a coloured dot and text on the right (e.g. "Vault — Available"). */
export function StatusDotRow({
  label,
  text,
  dotClass,
  title,
}: {
  label: string;
  text: string;
  dotClass: string;
  title?: string;
}) {
  return (
    <div className="flex items-center justify-between gap-3 text-xs" title={title}>
      <span className="text-muted-foreground">{label}</span>
      <span className="flex items-center gap-1.5 text-right font-semibold">
        <span className={cn('inline-block size-2 shrink-0 rounded-full', dotClass)} />
        {text}
      </span>
    </div>
  );
}
