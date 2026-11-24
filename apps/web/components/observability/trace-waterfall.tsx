'use client';

import { useMemo } from 'react';
import { AlertOctagon } from 'lucide-react';
import { cn } from '@/lib/utils';
import type { TraceSpan } from '@/types/traces';
import { formatMs } from '@/utils/metrics-format';

/** Fixed colour per tier of the request path (control plane → agent → engine). */
const SERVICE_COLORS: Record<string, string> = {
  api_layer: 'rgb(var(--chart-2))',
  node_agent: 'rgb(var(--chart-4))',
};
const ENGINE_COLOR = 'rgb(var(--chart-3))';

export const SERVICE_LABELS: Record<string, string> = {
  api_layer: 'API layer',
  node_agent: 'Node agent',
  llama_cpp: 'llama.cpp',
  mlx_lm: 'MLX',
  vllm: 'vLLM',
  sglang: 'SGLang',
};

export function serviceColor(service: string): string {
  return SERVICE_COLORS[service] ?? ENGINE_COLOR;
}

interface Row {
  span: TraceSpan;
  depth: number;
  startMs: number;
}

/** Depth-first order with each span's depth and start offset from the trace start. */
function layout(spans: TraceSpan[]): { rows: Row[]; t0: number; total: number } {
  if (spans.length === 0) return { rows: [], t0: 0, total: 0 };
  const t0 = Math.min(...spans.map((s) => new Date(s.ts).getTime()));
  const end = Math.max(...spans.map((s) => new Date(s.ts).getTime() + (s.durationMs ?? 0)));
  const children = new Map<string | null, TraceSpan[]>();
  const ids = new Set(spans.map((s) => s.spanId));
  for (const s of spans) {
    const parent = s.parentSpanId && ids.has(s.parentSpanId) ? s.parentSpanId : null;
    children.set(parent, [...(children.get(parent) ?? []), s]);
  }
  for (const list of children.values()) list.sort((a, b) => a.ts.localeCompare(b.ts));
  const rows: Row[] = [];
  const walk = (parent: string | null, depth: number) => {
    for (const s of children.get(parent) ?? []) {
      rows.push({ span: s, depth, startMs: new Date(s.ts).getTime() - t0 });
      walk(s.spanId, depth + 1);
    }
  };
  walk(null, 0);
  return { rows, t0, total: Math.max(1, end - t0) };
}

interface TraceWaterfallProps {
  spans: TraceSpan[];
  selectedId: string | null;
  onSelect: (spanId: string) => void;
}

/**
 * Span waterfall: name column indented by depth, a shared time axis, one bar
 * per span placed by start offset and sized by duration, coloured by tier.
 * Failed spans carry an icon and an error outline, not colour alone.
 */
export function TraceWaterfall({ spans, selectedId, onSelect }: TraceWaterfallProps) {
  const { rows, total } = useMemo(() => layout(spans), [spans]);
  const ticks = [0, 0.25, 0.5, 0.75, 1];
  const services = [...new Set(rows.map((r) => r.span.service))];

  return (
    <div className="text-xs">
      <div className="grid grid-cols-[minmax(220px,34%)_minmax(0,1fr)_72px] items-end gap-x-3 border-b border-border px-5 pb-2 text-[11px] text-muted-foreground">
        <span className="font-medium">Span</span>
        <div className="relative h-4">
          {ticks.map((t) => (
            <span key={t} className="absolute -translate-x-1/2 whitespace-nowrap tabular-nums first:translate-x-0 last:-translate-x-full" style={{ left: `${t * 100}%` }}>
              {t === 0 ? '0' : formatMs(total * t)}
            </span>
          ))}
        </div>
        <span className="text-right font-medium">Duration</span>
      </div>
      <ul role="listbox" aria-label="Spans" className="py-1">
        {rows.map(({ span, depth, startMs }) => {
          const left = (startMs / total) * 100;
          const width = Math.max(0.4, ((span.durationMs ?? 0) / total) * 100);
          const selected = span.spanId === selectedId;
          const color = serviceColor(span.service);
          return (
            <li key={span.spanId} role="option" aria-selected={selected}>
              <button
                type="button"
                onClick={() => onSelect(span.spanId)}
                className={cn(
                  'grid w-full grid-cols-[minmax(220px,34%)_minmax(0,1fr)_72px] items-center gap-x-3 px-5 py-1.5 text-left outline-none transition-colors hover:bg-accent/50 focus-visible:bg-accent/50',
                  selected && 'bg-accent',
                )}
              >
                <span className="flex min-w-0 items-center gap-1.5" style={{ paddingLeft: depth * 14 }}>
                  {depth > 0 && <span aria-hidden className="h-3 w-2 shrink-0 rounded-bl-sm border-b border-l border-border" />}
                  <span aria-hidden className="size-2 shrink-0 rounded-[2px]" style={{ backgroundColor: color }} />
                  <span className="truncate font-medium font-mono">{span.name}</span>
                  {span.error && <AlertOctagon className="size-3.5 shrink-0 text-error" aria-label="Failed" />}
                </span>
                <span className="relative h-5">
                  {ticks.slice(1, -1).map((t) => (
                    <span key={t} aria-hidden className="absolute inset-y-0 w-px bg-border/60" style={{ left: `${t * 100}%` }} />
                  ))}
                  <span
                    className={cn('absolute top-1/2 h-2.5 -translate-y-1/2 rounded-[3px]', span.error && 'ring-2 ring-error/70')}
                    style={{ left: `${left}%`, width: `${width}%`, backgroundColor: color, opacity: span.error ? 0.85 : 1 }}
                  />
                </span>
                <span className="text-right tabular-nums">{formatMs(span.durationMs)}</span>
              </button>
            </li>
          );
        })}
      </ul>
      <div className="flex flex-wrap gap-4 border-t border-border px-5 py-2.5 text-[11px] text-muted-foreground">
        {services.map((s) => (
          <span key={s} className="inline-flex items-center gap-1.5">
            <span aria-hidden className="size-2 rounded-[2px]" style={{ backgroundColor: serviceColor(s) }} />
            {SERVICE_LABELS[s] ?? s}
          </span>
        ))}
        <span className="inline-flex items-center gap-1.5">
          <AlertOctagon className="size-3 text-error" aria-hidden /> failed span
        </span>
      </div>
    </div>
  );
}
