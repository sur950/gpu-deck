'use client';

/**
 * Shared bits for the metering and billing screens: date-range state in the
 * URL, number formatting, dimension colours and the share bar.
 */

import { useMemo } from 'react';
import { buildDefaultDateRange, computeDateRange, USAGE_PRESETS, type DateRangeValue } from '@/components/shared/DateRangeFilter';
import { OTHER_COLOR, SERIES_COLORS } from '@/components/charts/chart-theme';
import { formatCurrency, formatNumber } from '@/utils/format';
import { cn } from '@/lib/utils';

export const UNALLOCATED = 'unallocated';

/** Resolve the range once per (preset, from, to) so query keys stay stable. */
export function useRangeFromUrl(preset: string, from: string, to: string): DateRangeValue {
  return useMemo(() => {
    if (preset === 'custom' && from && to) return { preset, from, to, label: 'Custom range' };
    const base = buildDefaultDateRange(preset || 'last_7d', USAGE_PRESETS);
    const r = computeDateRange(preset || 'last_7d');
    return { ...base, from: r.from, to: r.to };
  }, [preset, from, to]);
}

/**
 * Start of a `day` / `hour` usage bucket in ms. The admin API sends full ISO
 * timestamps (`2026-11-18T00:00:00Z`); bare dates (`2026-11-18`) are UTC days.
 * NaN when the value is missing or unparseable.
 */
export function bucketStartMs(value: string | null | undefined): number {
  if (!value) return Number.NaN;
  return Date.parse(/^\d{4}-\d{2}-\d{2}$/.test(value) ? `${value}T00:00:00Z` : value);
}

export function fmtTokens(n: number): string {
  return formatNumber(Math.round(n));
}

export function fmtUsd(n: number): string {
  if (n === 0) return '$0.00';
  if (n < 0.01) return `$${n.toFixed(4)}`;
  return formatCurrency(n);
}

export function fmtMinutes(n: number): string {
  // Prorated: a quarter-GPU slice for one minute is 0.25 GPU-minutes.
  if (n > 0 && n < 1) return `${n.toFixed(2)} min`;
  if (n < 60) return `${n.toFixed(n < 10 ? 1 : 0)} min`;
  return `${formatNumber(Math.round(n))} min`;
}

/** Colour per dimension id: top entries get chart-2.., the unallocated bucket is muted. */
export function dimensionColors(ids: string[]): Map<string, string> {
  const out = new Map<string, string>();
  let i = 1;
  for (const id of ids) {
    if (id === UNALLOCATED || id === 'other') out.set(id, OTHER_COLOR);
    else {
      out.set(id, SERIES_COLORS[i % SERIES_COLORS.length]);
      i += 1;
    }
  }
  return out;
}

export function ShareBar({ value, max, color, className }: { value: number; max: number; color?: string; className?: string }) {
  const pct = max > 0 ? Math.max(1.5, (value / max) * 100) : 0;
  return (
    <span className={cn('block h-1.5 w-full overflow-hidden rounded-full bg-muted', className)}>
      <span className="block h-full rounded-full" style={{ width: `${pct}%`, backgroundColor: color ?? 'rgb(var(--chart-1))' }} />
    </span>
  );
}
