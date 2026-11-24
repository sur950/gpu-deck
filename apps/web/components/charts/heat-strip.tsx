'use client';

import { cn } from '@/lib/utils';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';

export interface HeatCell {
  id: string;
  label: string;
  /** 0–1 fill of the cell; null = no data. */
  value: number | null;
  /** Lines for the hover readout. */
  detail?: string[];
  /** Outline the cell (e.g. selected slot). */
  active?: boolean;
  /** Striped overlay for cells in a transitional state (processing). */
  striped?: boolean;
}

interface HeatStripProps {
  cells: HeatCell[];
  /** Theme colour string for the ramp; one hue, light → dark by value. */
  color?: string;
  /** Cell height in px. */
  size?: number;
  onSelect?: (id: string) => void;
  className?: string;
  'aria-label'?: string;
}

/** Opacity steps for the sequential ramp (keeps the lowest step visible on both surfaces). */
function rampOpacity(value: number | null): number {
  if (value === null) return 0;
  const v = Math.min(1, Math.max(0, value));
  return 0.14 + v * 0.86;
}

/**
 * One cell per slot / session, filled by a single-hue ramp (KV cache share).
 * Each cell carries its own tooltip and is keyboard focusable, so the value
 * is never colour-only; the slot table next to it is the table twin.
 */
export function HeatStrip({ cells, color = 'rgb(var(--chart-2))', size = 28, onSelect, className, ...rest }: HeatStripProps) {
  return (
    <TooltipProvider>
      <div role="list" aria-label={rest['aria-label'] ?? 'Slots'} className={cn('grid gap-[2px]', className)} style={{ gridTemplateColumns: `repeat(${Math.max(1, cells.length)}, minmax(0, 1fr))` }}>
        {cells.map((cell) => {
          const empty = cell.value === null;
          const body = (
            <button
              type="button"
              role="listitem"
              onClick={onSelect ? () => onSelect(cell.id) : undefined}
              aria-label={`${cell.label}: ${cell.value === null ? 'no data' : `${Math.round(cell.value * 100)}%`}`}
              className={cn(
                'relative w-full overflow-hidden rounded-[3px] bg-muted outline-none transition-[transform,box-shadow] duration-150',
                onSelect && 'cursor-pointer hover:-translate-y-px focus-visible:ring-2 focus-visible:ring-ring/40',
                cell.active && 'ring-2 ring-foreground/70',
              )}
              style={{ height: size }}
            >
              {!empty && <span aria-hidden className="absolute inset-0" style={{ backgroundColor: color, opacity: rampOpacity(cell.value) }} />}
              {cell.striped && (
                <span
                  aria-hidden
                  className="absolute inset-0"
                  style={{ backgroundImage: 'repeating-linear-gradient(135deg, rgb(var(--card) / 0.55) 0 3px, transparent 3px 7px)' }}
                />
              )}
            </button>
          );
          return (
            <Tooltip key={cell.id}>
              <TooltipTrigger render={body} />
              <TooltipContent side="top" className="text-xs">
                <p className="font-semibold">{cell.label}</p>
                {cell.detail?.map((line) => (
                  <p key={line} className="text-muted-foreground">{line}</p>
                ))}
              </TooltipContent>
            </Tooltip>
          );
        })}
      </div>
    </TooltipProvider>
  );
}

/** Legend for the strip's ramp: low → high. */
export function HeatRampLegend({ color = 'rgb(var(--chart-2))', low = '0%', high = '100%', className }: { color?: string; low?: string; high?: string; className?: string }) {
  return (
    <div className={cn('flex items-center gap-2 text-[11px] text-muted-foreground', className)}>
      <span>{low}</span>
      <span aria-hidden className="flex h-2 w-24 gap-px overflow-hidden rounded-sm bg-muted">
        {Array.from({ length: 8 }).map((_, i) => (
          <span key={i} className="flex-1" style={{ backgroundColor: color, opacity: rampOpacity(i / 7) }} />
        ))}
      </span>
      <span>{high}</span>
    </div>
  );
}
