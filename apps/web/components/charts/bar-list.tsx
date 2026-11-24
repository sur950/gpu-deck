'use client';

import Link from 'next/link';
import { cn } from '@/lib/utils';

export interface BarListItem {
  id: string;
  label: string;
  /** Caption under the bar (left), e.g. engine or node. */
  sublabel?: string;
  value: number;
  /** Formatted primary figure shown at the right. */
  valueLabel: string;
  /** Caption under the bar (right), e.g. tokens and cost. */
  extraLabel?: string;
  href?: string;
  /** Per-item colour override (rarely needed — the list is one hue). */
  color?: string;
}

interface BarListProps {
  items: BarListItem[];
  /** Show the share of the total next to the value. */
  showShare?: boolean;
  max?: number;
  /** Theme colour for every bar (one hue, magnitude only); default chart-1. */
  color?: string;
  className?: string;
}

/** Ranked list of thin meters ("Model usage" style card): one hue, magnitude only, optional caption row. */
export function BarList({ items, showShare = true, max, color = 'rgb(var(--chart-1))', className }: BarListProps) {
  const total = items.reduce((s, i) => s + i.value, 0);
  const top = max ?? Math.max(...items.map((i) => i.value), 0);
  return (
    <ol className={cn('space-y-3.5', className)}>
      {items.map((item) => {
        const share = total > 0 ? (item.value / total) * 100 : 0;
        const width = top > 0 ? (item.value / top) * 100 : 0;
        const row = (
          <>
            <div className="mb-1.5 flex items-baseline justify-between gap-3 text-xs">
              <span className="min-w-0 truncate font-medium">{item.label}</span>
              <span className="shrink-0 tabular-nums text-muted-foreground">
                <span className="font-semibold text-foreground">{item.valueLabel}</span>
                {showShare && total > 0 && <> · {share.toFixed(share < 10 ? 1 : 0)}%</>}
              </span>
            </div>
            <div className="h-1.5 overflow-hidden rounded-full bg-muted" role="meter" aria-label={`${item.label}: ${item.valueLabel}`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(share)}>
              <div className="h-full rounded-full transition-[width] duration-700 ease-out" style={{ width: `${width.toFixed(1)}%`, backgroundColor: item.color ?? color }} />
            </div>
            {(item.sublabel || item.extraLabel) && (
              <div className="mt-1 flex items-baseline justify-between gap-3 text-[11px] text-muted-foreground tabular-nums">
                <span className="min-w-0 truncate">{item.sublabel}</span>
                <span className="shrink-0">{item.extraLabel}</span>
              </div>
            )}
          </>
        );
        return (
          <li key={item.id}>
            {item.href ? (
              <Link href={item.href} className="block rounded-md outline-none transition-colors hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/30">
                {row}
              </Link>
            ) : (
              row
            )}
          </li>
        );
      })}
    </ol>
  );
}
