import { thresholdBarClass } from '@/components/charts/chart-theme';
import { cn } from '@/lib/utils';
import { formatBytes } from '@/utils/format';

interface VramMeterProps {
  used: number;
  total: number;
  /** Text to the left of the bar; defaults to "used / total". */
  label?: string;
  className?: string;
  /** Hide the text, keep the bar (dense tables). */
  compact?: boolean;
  barClass?: string;
}

/** Inline VRAM / disk usage bar coloured by the usage thresholds (70 % amber, 90 % red). */
export function VramMeter({ used, total, label, className, compact = false, barClass }: VramMeterProps) {
  const percent = total > 0 ? Math.min(100, (used / total) * 100) : 0;
  return (
    <div className={cn('min-w-[120px] space-y-1', className)}>
      {!compact && (
        <div className="flex items-center justify-between gap-2 text-[11px] tabular-nums">
          <span className="text-muted-foreground">{label ?? `${formatBytes(used)} / ${formatBytes(total)}`}</span>
          <span className="font-medium">{percent.toFixed(0)}%</span>
        </div>
      )}
      <div className="h-1.5 overflow-hidden rounded-full bg-muted" role="progressbar" aria-valuenow={Math.round(percent)} aria-valuemin={0} aria-valuemax={100}>
        <div
          className={cn('h-full rounded-full transition-[width] duration-500 ease-out', barClass ?? thresholdBarClass(percent))}
          style={{ width: `${percent.toFixed(1)}%` }}
        />
      </div>
    </div>
  );
}
