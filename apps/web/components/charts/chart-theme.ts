/**
 * Recharts styling from theme tokens.
 * Colours are CSS strings so they follow light / dark mode and the
 * NEXT_PUBLIC_BRAND_COLOR accent at runtime.
 */

/** Categorical series colours in assignment order; the sixth is "Other". */
export const SERIES_COLORS = [
  'rgb(var(--chart-1))',
  'rgb(var(--chart-2))',
  'rgb(var(--chart-3))',
  'rgb(var(--chart-4))',
  'rgb(var(--chart-5))',
] as const;

export const OTHER_COLOR = 'rgb(var(--muted-foreground))';

export const GRID_STROKE = 'rgb(var(--border))';

export const AXIS_TICK = { fontSize: 11, fill: 'rgb(var(--muted-foreground))' } as const;

export const TOOLTIP_CURSOR = { stroke: 'rgb(var(--foreground) / 0.25)', strokeWidth: 1 } as const;

export const ACTIVE_DOT = { r: 4, strokeWidth: 2, stroke: 'rgb(var(--card))' } as const;

/** Status colour classes for meters and dots. */
export const STATUS_BAR_CLASS = {
  good: 'bg-success',
  warn: 'bg-warning',
  bad: 'bg-error',
  info: 'bg-chart-2',
} as const;

/** Colour a percentage meter by thresholds (e.g. VRAM / temperature). */
export function thresholdBarClass(percent: number, warnAt = 70, badAt = 90): string {
  if (percent >= badAt) return STATUS_BAR_CLASS.bad;
  if (percent >= warnAt) return STATUS_BAR_CLASS.warn;
  return STATUS_BAR_CLASS.good;
}
