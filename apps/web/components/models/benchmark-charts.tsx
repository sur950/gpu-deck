'use client';

import { CartesianGrid, ComposedChart, Area, Line, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { ACTIVE_DOT, AXIS_TICK, GRID_STROKE, TOOLTIP_CURSOR } from '@/components/charts/chart-theme';
import { ChartTooltipCard } from '@/components/charts/chart-tooltip';
import type { BenchmarkResult } from '@/types/models';

const C = {
  ttft50: 'rgb(var(--chart-2))',
  ttft95: 'rgb(var(--chart-4))',
  e2e95: 'rgb(var(--chart-3))',
  tuned: 'rgb(var(--chart-5))',
  tps: 'rgb(var(--chart-2))',
  rps: 'rgb(var(--chart-3))',
  slo: 'rgb(var(--error-rgb, 229 50 31))',
};

function q(r: BenchmarkResult, key: 'ttft_ms' | 'e2e_ms' | 'tpot_ms', pct: string): number | null {
  const v = (r[key] as Record<string, unknown> | undefined)?.[pct];
  return typeof v === 'number' ? v : null;
}

interface Row {
  c: number;
  ttft50: number | null;
  ttft95: number | null;
  e2e95: number | null;
  tunedTtft95: number | null;
  tps: number | null;
  rps: number | null;
  tunedTps: number | null;
  slo: boolean | undefined;
}

export function toRows(results: BenchmarkResult[]): Row[] {
  const base = results.filter((r) => !r.launch_flags);
  const tuned = results.filter((r) => r.launch_flags);
  const levels = [...new Set(results.map((r) => r.concurrency ?? 0))].sort((a, b) => a - b);
  return levels.map((c) => {
    const b = base.find((r) => r.concurrency === c);
    const t = tuned.find((r) => r.concurrency === c);
    return {
      c,
      ttft50: b ? q(b, 'ttft_ms', 'p50') : null,
      ttft95: b ? q(b, 'ttft_ms', 'p95') : null,
      e2e95: b ? q(b, 'e2e_ms', 'p95') : null,
      tunedTtft95: t ? q(t, 'ttft_ms', 'p95') : null,
      tps: b?.generation_tps ?? null,
      rps: b?.rps ?? null,
      tunedTps: t?.generation_tps ?? null,
      slo: b?.slo_met,
    };
  });
}

const fmtMs = (v: number) => (v >= 1000 ? `${(v / 1000).toFixed(1)}s` : `${Math.round(v)}`);

function Legend({ items }: { items: { label: string; color: string; dashed?: boolean }[] }) {
  return (
    <ul className="mb-2 flex flex-wrap gap-4 px-2 text-xs text-muted-foreground">
      {items.map((i) => (
        <li key={i.label} className="flex items-center gap-1.5">
          <span className="h-0.5 w-3 rounded-full" style={i.dashed ? { backgroundImage: `linear-gradient(90deg, ${i.color} 50%, transparent 50%)`, backgroundSize: '4px 2px' } : { backgroundColor: i.color }} />
          {i.label}
        </li>
      ))}
    </ul>
  );
}

/** p50 / p95 time to first token (and p95 end-to-end on the right axis) per concurrency level, with the SLO and the chosen level marked. */
export function LatencyChart({ results, sloTtft, best }: { results: BenchmarkResult[]; sloTtft?: number | null; best?: number | null }) {
  const rows = toRows(results);
  const hasTuned = rows.some((r) => r.tunedTtft95 !== null);
  return (
    <div>
      <Legend
        items={[
          { label: 'TTFT p50', color: C.ttft50 },
          { label: 'TTFT p95', color: C.ttft95 },
          { label: 'E2E p95 (right axis)', color: C.e2e95 },
          ...(hasTuned ? [{ label: 'TTFT p95 tuned flags', color: C.tuned, dashed: true }] : []),
          ...(sloTtft ? [{ label: `SLO ${sloTtft} ms`, color: C.slo, dashed: true }] : []),
        ]}
      />
      <div className="h-[260px]" aria-hidden>
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={rows} margin={{ top: 20, right: 8, bottom: 0, left: 0 }}>
            <CartesianGrid vertical={false} stroke={GRID_STROKE} />
            <XAxis dataKey="c" tickLine={false} axisLine={false} tick={AXIS_TICK} tickMargin={8} />
            <YAxis yAxisId="ms" tickLine={false} axisLine={false} width={44} tick={AXIS_TICK} tickFormatter={fmtMs} />
            <YAxis yAxisId="e2e" orientation="right" tickLine={false} axisLine={false} width={44} tick={AXIS_TICK} tickFormatter={fmtMs} />
            <Tooltip
              cursor={TOOLTIP_CURSOR}
              content={({ active, payload, label }) =>
                active && payload?.length ? (
                  <ChartTooltipCard
                    title={`Concurrency ${String(label)}`}
                    rows={payload.filter((p) => p.value !== null && p.value !== undefined).map((p) => ({ key: String(p.dataKey), label: String(p.name), value: `${Math.round(Number(p.value))} ms`, color: String(p.color) }))}
                  />
                ) : null
              }
            />
            {sloTtft ? <ReferenceLine yAxisId="ms" y={sloTtft} stroke={C.slo} strokeDasharray="4 4" /> : null}
            {best ? <ReferenceLine yAxisId="ms" x={best} stroke="rgb(var(--foreground) / 0.35)" strokeDasharray="2 3" label={{ value: `best ${best}`, position: 'top', style: { ...AXIS_TICK, fontSize: 10 } }} /> : null}
            <Area yAxisId="ms" type="monotone" dataKey="ttft95" name="TTFT p95" stroke={C.ttft95} fill={C.ttft95} fillOpacity={0.08} strokeWidth={2} dot={{ r: 2.5 }} activeDot={ACTIVE_DOT} connectNulls />
            <Line yAxisId="ms" type="monotone" dataKey="ttft50" name="TTFT p50" stroke={C.ttft50} strokeWidth={2} dot={{ r: 2.5 }} activeDot={ACTIVE_DOT} connectNulls />
            <Line yAxisId="e2e" type="monotone" dataKey="e2e95" name="E2E p95" stroke={C.e2e95} strokeWidth={1.5} strokeOpacity={0.8} dot={false} connectNulls />
            {hasTuned && <Line yAxisId="ms" type="monotone" dataKey="tunedTtft95" name="TTFT p95 tuned" stroke={C.tuned} strokeDasharray="5 4" strokeWidth={2} dot={{ r: 2.5 }} connectNulls />}
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

/** Generated tokens/s and requests/s per concurrency level — where throughput flattens is where batching stops helping. */
export function ThroughputChart({ results, best }: { results: BenchmarkResult[]; best?: number | null }) {
  const rows = toRows(results);
  const hasTuned = rows.some((r) => r.tunedTps !== null);
  const embeddings = rows.every((r) => !r.tps);
  return (
    <div>
      <Legend
        items={[
          ...(embeddings ? [] : [{ label: 'Generated tok/s', color: C.tps }]),
          { label: embeddings ? 'Requests/s' : 'Requests/s (right axis)', color: C.rps },
          ...(hasTuned ? [{ label: 'tok/s tuned flags', color: C.tuned, dashed: true }] : []),
        ]}
      />
      <div className="h-[260px]" aria-hidden>
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={rows} margin={{ top: 20, right: 8, bottom: 0, left: 0 }}>
            <CartesianGrid vertical={false} stroke={GRID_STROKE} />
            <XAxis dataKey="c" tickLine={false} axisLine={false} tick={AXIS_TICK} tickMargin={8} />
            <YAxis yAxisId="tps" tickLine={false} axisLine={false} width={48} tick={AXIS_TICK} tickFormatter={(v: number) => (v >= 1000 ? `${(v / 1000).toFixed(1)}k` : String(Math.round(v)))} hide={embeddings} />
            <YAxis yAxisId="rps" orientation={embeddings ? 'left' : 'right'} tickLine={false} axisLine={false} width={40} tick={AXIS_TICK} />
            <Tooltip
              cursor={TOOLTIP_CURSOR}
              content={({ active, payload, label }) =>
                active && payload?.length ? (
                  <ChartTooltipCard
                    title={`Concurrency ${String(label)}`}
                    rows={payload.filter((p) => p.value !== null && p.value !== undefined).map((p) => ({ key: String(p.dataKey), label: String(p.name), value: Number(p.value).toFixed(p.dataKey === 'rps' ? 2 : 0), color: String(p.color) }))}
                  />
                ) : null
              }
            />
            {best ? <ReferenceLine yAxisId="rps" x={best} stroke="rgb(var(--foreground) / 0.35)" strokeDasharray="2 3" /> : null}
            {!embeddings && <Area yAxisId="tps" type="monotone" dataKey="tps" name="tok/s" stroke={C.tps} fill={C.tps} fillOpacity={0.12} strokeWidth={2} dot={{ r: 2.5 }} activeDot={ACTIVE_DOT} connectNulls />}
            <Line yAxisId="rps" type="monotone" dataKey="rps" name="req/s" stroke={C.rps} strokeWidth={2} dot={{ r: 2.5 }} activeDot={ACTIVE_DOT} connectNulls />
            {hasTuned && <Line yAxisId="tps" type="monotone" dataKey="tunedTps" name="tok/s tuned" stroke={C.tuned} strokeDasharray="5 4" strokeWidth={2} dot={{ r: 2.5 }} connectNulls />}
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
