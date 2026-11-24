'use client';

import { useMemo } from 'react';
import { cn } from '@/lib/utils';
import { HeatRampLegend } from '@/components/charts/heat-strip';
import { labelFormatterFor, tickFormatterFor } from '@/components/charts/series-utils';
import type { MetricsResult } from '@/types/metrics';
import { formatTokens } from '@/utils/metrics-format';

const COLOR = 'rgb(var(--chart-2))';

function opacity(ratio: number | null): number {
  if (ratio === null || ratio <= 0) return 0;
  return 0.14 + Math.min(1, ratio) * 0.86;
}

interface SlotHeatmapProps {
  result: MetricsResult | undefined;
  /** Tokens a slot can hold (context size), for the fill ratio. */
  contextSize: number;
  fromMs: number;
  toMs: number;
  selectedSlot?: number | null;
}

/**
 * Slots × time: one row per slot, one cell per bucket, filled by the slot's
 * KV cache share of its context (single hue, light → dark). Each cell has a
 * hover title with the exact figure; the slot table above is the table twin
 * for the current moment.
 */
export function SlotHeatmap({ result, contextSize, fromMs, toMs, selectedSlot }: SlotHeatmapProps) {
  const rows = useMemo(
    () =>
      (result?.series ?? [])
        .filter((s) => s.metric === 'kv_cache_tokens' && s.labels.slot_id !== undefined)
        .sort((a, b) => Number(a.labels.slot_id) - Number(b.labels.slot_id)),
    [result],
  );
  const tick = tickFormatterFor(fromMs, toMs);
  const label = labelFormatterFor(fromMs, toMs);

  if (rows.length === 0) return <p className="py-6 text-center text-xs text-muted-foreground">No per-slot samples in the last 15 minutes.</p>;

  const columns = rows[0].points.length;
  const ticks = [0, Math.floor(columns / 2), columns - 1];

  return (
    <div>
      <div className="flex gap-2">
        <div className="flex shrink-0 flex-col gap-[2px] pt-px text-right text-[10px] leading-none text-muted-foreground tabular-nums">
          {rows.map((row) => (
            <span key={row.labels.slot_id} className={cn('flex h-3.5 items-center justify-end', Number(row.labels.slot_id) === selectedSlot && 'font-semibold text-foreground')}>
              {row.labels.slot_id}
            </span>
          ))}
        </div>
        <div className="min-w-0 flex-1">
          <div className="grid gap-[2px]" role="img" aria-label={`KV cache fill per slot over time, ${rows.length} slots`}>
            {rows.map((row) => (
              <div
                key={row.labels.slot_id}
                className={cn('grid h-3.5 gap-px rounded-[2px]', Number(row.labels.slot_id) === selectedSlot && 'ring-1 ring-foreground/60')}
                style={{ gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` }}
              >
                {row.points.map((p) => {
                  const ratio = p.v === null || contextSize <= 0 ? null : p.v / contextSize;
                  return (
                    <span
                      key={p.t}
                      title={`slot ${row.labels.slot_id} · ${label(p.t)} · ${p.v === null ? 'no sample' : `${formatTokens(p.v)} tokens (${Math.round((ratio ?? 0) * 100)}% of context)`}`}
                      className="relative h-full bg-muted first:rounded-l-[2px] last:rounded-r-[2px]"
                    >
                      <span aria-hidden className="absolute inset-0" style={{ backgroundColor: COLOR, opacity: opacity(ratio) }} />
                    </span>
                  );
                })}
              </div>
            ))}
          </div>
          <div className="mt-1.5 flex justify-between text-[10px] text-muted-foreground tabular-nums">
            {ticks.map((i) => (
              <span key={i}>{rows[0].points[i] ? tick(rows[0].points[i].t) : ''}</span>
            ))}
          </div>
        </div>
      </div>
      <div className="mt-3 flex items-center justify-between gap-3">
        <span className="text-[11px] text-muted-foreground">Rows are slots; shade is KV cache as a share of the slot&apos;s context.</span>
        <HeatRampLegend color={COLOR} />
      </div>
    </div>
  );
}
