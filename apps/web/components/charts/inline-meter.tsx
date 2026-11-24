import { cn } from '@/lib/utils';

interface InlineMeterProps {
  /** 0–100 */
  percent: number | null;
  /** Bar colour class (bg-chart-2, bg-success …) — or pass `color`. */
  barClass?: string;
  /** Theme colour string, e.g. 'rgb(var(--chart-3))'. */
  color?: string;
  /** Accessible name, e.g. "VRAM 71%". */
  label: string;
  className?: string;
  /** Track height class (default h-1.5). */
  heightClass?: string;
}

/** Thin magnitude bar for table rows and tiles (role="meter", value in the label). */
export function InlineMeter({ percent, barClass = 'bg-chart-2', color, label, className, heightClass = 'h-1.5' }: InlineMeterProps) {
  const pct = percent === null || !Number.isFinite(percent) ? 0 : Math.min(100, Math.max(0, percent));
  return (
    <div
      role="meter"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(pct)}
      className={cn('overflow-hidden rounded-full bg-muted', heightClass, className)}
    >
      <div
        className={cn('h-full rounded-full transition-[width] duration-700 ease-out', !color && barClass)}
        style={{ width: `${pct.toFixed(1)}%`, ...(color ? { backgroundColor: color } : {}) }}
      />
    </div>
  );
}

/**
 * Stacked meter — one segment per part (e.g. VRAM slices per deployment),
 * separated by a 2px surface gap. Remaining capacity is the empty track.
 */
export function SegmentMeter({
  segments,
  total,
  label,
  className,
  heightClass = 'h-2',
}: {
  segments: { key: string; value: number; color: string; label: string }[];
  total: number;
  label: string;
  className?: string;
  heightClass?: string;
}) {
  const used = segments.reduce((s, x) => s + x.value, 0);
  return (
    <div
      role="meter"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={total > 0 ? Math.round((used / total) * 100) : 0}
      className={cn('flex gap-[2px] overflow-hidden rounded-full bg-muted', heightClass, className)}
    >
      {segments.map((seg) => (
        <div
          key={seg.key}
          title={seg.label}
          className="h-full first:rounded-l-full last:rounded-r-full"
          style={{ width: `${total > 0 ? Math.max(0.8, (seg.value / total) * 100) : 0}%`, backgroundColor: seg.color }}
        />
      ))}
    </div>
  );
}
