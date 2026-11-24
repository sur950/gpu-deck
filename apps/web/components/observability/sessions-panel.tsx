'use client';

import { useMemo, useState } from 'react';
import { Layers } from 'lucide-react';
import { cn } from '@/lib/utils';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Skeleton } from '@/components/ui/skeleton';
import { EmptyState } from '@/components/shared/EmptyState';
import { HeatStrip, HeatRampLegend } from '@/components/charts/heat-strip';
import type { HeatCell } from '@/components/charts/heat-strip';
import { InlineMeter } from '@/components/charts/inline-meter';
import { SlotHeatmap } from '@/components/observability/slot-heatmap';
import { engineLabel } from '@/components/observability/labels';
import { EngineEndpoint } from '@/components/models/engine-endpoint';
import type { InstanceSummary, MetricsResult, SessionSlot, SessionsSnapshot } from '@/types/metrics';
import { formatGiB, formatMs, formatPct, formatTokens } from '@/utils/metrics-format';

const KV_COLOR = 'rgb(var(--chart-2))';

const STATE_STYLE: Record<SessionSlot['state'], { label: string; dot: string }> = {
  idle: { label: 'Idle', dot: 'bg-muted-foreground/40' },
  waiting: { label: 'Waiting', dot: 'bg-warning' },
  processing: { label: 'Prompt', dot: 'bg-info' },
  generating: { label: 'Generating', dot: 'bg-success' },
};

interface SessionsPanelProps {
  instance: InstanceSummary | undefined;
  snapshot: SessionsSnapshot | undefined;
  history: MetricsResult | undefined;
  historyFromMs: number;
  historyToMs: number;
  isLoading: boolean;
  /** Instance picker rendered in the header. */
  picker?: React.ReactNode;
}

function kvRatio(slot: SessionSlot): number | null {
  if (slot.kvCacheTokens === null || !slot.contextSize) return null;
  return slot.kvCacheTokens / slot.contextSize;
}

/**
 * Per-slot view of one engine instance: the KV cache strip right now, a row
 * per slot with prompt / cached / generated tokens and timings, and the last
 * 15 minutes of KV fill per slot.
 */
export function SessionsPanel({ instance, snapshot, history, historyFromMs, historyToMs, isLoading, picker }: SessionsPanelProps) {
  const [selected, setSelected] = useState<number | null>(null);
  const slots = useMemo(() => snapshot?.sessions ?? [], [snapshot]);

  const totals = useMemo(() => {
    const busy = slots.filter((s) => s.state !== 'idle');
    const prompt = busy.reduce((s, x) => s + (x.promptTokens ?? 0), 0);
    const cached = busy.reduce((s, x) => s + (x.cachedTokens ?? 0), 0);
    const kv = slots.reduce((s, x) => s + (x.kvCacheTokens ?? 0), 0);
    const kvBytes = slots.reduce((s, x) => s + (x.kvCacheBytes ?? 0), 0);
    const ctx = slots.reduce((s, x) => s + (x.contextSize ?? 0), 0);
    return { busy: busy.length, prompt, cached, kv, kvBytes, ctx };
  }, [slots]);

  const cells: HeatCell[] = slots.map((slot) => {
    const ratio = kvRatio(slot);
    const id = String(slot.slotId ?? slot.sessionKey);
    return {
      id,
      label: `Slot ${slot.slotId ?? '?'} · ${STATE_STYLE[slot.state].label}`,
      value: ratio,
      active: selected !== null && slot.slotId === selected,
      striped: slot.state === 'processing',
      detail: [
        `KV ${formatTokens(slot.kvCacheTokens)} / ${formatTokens(slot.contextSize)} tokens (${formatPct((ratio ?? 0) * 100, 0)})`,
        slot.state !== 'idle' ? `prompt ${formatTokens(slot.promptTokens)} · cached ${formatTokens(slot.cachedTokens)} · out ${formatTokens(slot.generatedTokens)}` : 'no request in this slot',
      ],
    };
  });

  const contextSize = slots[0]?.contextSize ?? instance?.contextSize ?? 0;

  return (
    <Card id="sessions" className="scroll-mt-20 gap-0 rounded-xl py-0">
      <CardHeader className="flex flex-col gap-3 border-b border-border px-5 py-4 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0 space-y-0.5">
          <CardTitle className="text-sm font-semibold">Sessions &amp; KV cache</CardTitle>
          <CardDescription>
            {instance
              ? `${instance.deploymentName} on ${instance.nodeName} · ${engineLabel(instance.engine)} · ${snapshot?.slotsTotal ?? instance.slots ?? '—'} slots`
              : 'Pick an instance to see its slots'}

          </CardDescription>
          {instance && (
            <p className="flex flex-wrap items-center gap-1 pt-0.5 text-[11px] text-muted-foreground">
              Engine listening at <EngineEndpoint port={instance.port} nodeName={instance.nodeName} />
            </p>
          )}
        </div>
        {picker}
      </CardHeader>

      <CardContent className="space-y-5 px-5 py-4">
        {isLoading && !snapshot ? (
          <div className="space-y-3">
            <Skeleton className="h-8 rounded-md" />
            <Skeleton className="h-40 rounded-md" />
          </div>
        ) : !instance || !snapshot || slots.length === 0 ? (
          <EmptyState icon={Layers} title="No live sessions" description="Slots and their KV cache appear while the instance is ready and serving." />
        ) : (
          <>
            <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-end">
              <div className="space-y-2">
                <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1 text-xs text-muted-foreground tabular-nums">
                  <span>
                    <span className="text-base font-semibold text-foreground">{snapshot.active}</span> active
                  </span>
                  <span>
                    <span className={cn('text-base font-semibold', snapshot.waiting > 0 ? 'text-warning-dark dark:text-warning-light' : 'text-foreground')}>{snapshot.waiting}</span> waiting
                  </span>
                  <span>
                    KV <span className="font-semibold text-foreground">{formatTokens(totals.kv)}</span> / {formatTokens(totals.ctx)} tokens · {formatGiB(totals.kvBytes)}
                  </span>
                  <span>
                    cached <span className="font-semibold text-foreground">{formatPct(totals.prompt ? (totals.cached / totals.prompt) * 100 : null, 0)}</span> of live prompts
                  </span>
                </div>
                <HeatStrip
                  cells={cells}
                  color={KV_COLOR}
                  size={30}
                  aria-label="KV cache per slot"
                  onSelect={(id) => setSelected((cur) => (cur === Number(id) ? null : Number(id)))}
                />
              </div>
              <div className="flex flex-col items-start gap-1.5 lg:items-end">
                <HeatRampLegend color={KV_COLOR} low="empty" high="context full" />
                <div className="flex flex-wrap gap-3 text-[11px] text-muted-foreground">
                  {(['generating', 'processing', 'idle'] as const).map((st) => (
                    <span key={st} className="inline-flex items-center gap-1.5">
                      <span aria-hidden className={cn('size-2 rounded-full', STATE_STYLE[st].dot)} />
                      {STATE_STYLE[st].label}
                      {st === 'processing' && ' (striped)'}
                    </span>
                  ))}
                </div>
              </div>
            </div>

            <div className="overflow-x-auto rounded-lg border border-border">
              <Table className="text-xs tabular-nums">
                <TableHeader className="bg-muted/30">
                  <TableRow>
                    <TableHead className="w-14">Slot</TableHead>
                    <TableHead>State</TableHead>
                    <TableHead>Request</TableHead>
                    <TableHead className="text-right">Prompt</TableHead>
                    <TableHead className="text-right">Cached</TableHead>
                    <TableHead className="text-right">Generated</TableHead>
                    <TableHead className="min-w-40">KV cache</TableHead>
                    <TableHead className="text-right">TTFT</TableHead>
                    <TableHead className="text-right">Elapsed</TableHead>
                    <TableHead className="text-right">tok/s</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {slots.map((slot) => {
                    const ratio = kvRatio(slot);
                    const isSel = selected !== null && slot.slotId === selected;
                    const idle = slot.state === 'idle';
                    return (
                      <TableRow
                        key={slot.sessionKey}
                        data-state={isSel ? 'selected' : undefined}
                        className={cn('cursor-pointer', idle && 'text-muted-foreground')}
                        onClick={() => setSelected((cur) => (cur === slot.slotId ? null : slot.slotId))}
                      >
                        <TableCell className="font-medium font-mono">{slot.slotId ?? '—'}</TableCell>
                        <TableCell>
                          <span className="inline-flex items-center gap-1.5">
                            <span aria-hidden className={cn('size-1.5 rounded-full', STATE_STYLE[slot.state].dot, slot.state === 'generating' && 'animate-pulse-subtle')} />
                            {STATE_STYLE[slot.state].label}
                          </span>
                        </TableCell>
                        <TableCell className="max-w-36 truncate text-[11px] font-mono" title={slot.requestId ?? undefined}>
                          {slot.requestId ? slot.requestId.slice(0, 8) : '—'}
                        </TableCell>
                        <TableCell className="text-right">{idle ? '—' : formatTokens(slot.promptTokens)}</TableCell>
                        <TableCell className="text-right">
                          {idle ? (
                            '—'
                          ) : (
                            <>
                              {formatTokens(slot.cachedTokens)}
                              {slot.promptTokens && slot.cachedTokens ? <span className="ml-1 text-muted-foreground">{formatPct((slot.cachedTokens / slot.promptTokens) * 100, 0)}</span> : null}
                            </>
                          )}
                        </TableCell>
                        <TableCell className="text-right">{idle ? '—' : formatTokens(slot.generatedTokens)}</TableCell>
                        <TableCell>
                          <div className="flex items-center gap-2">
                            <InlineMeter percent={(ratio ?? 0) * 100} color={KV_COLOR} label={`KV ${formatPct((ratio ?? 0) * 100, 0)}`} className="w-20" />
                            <span className="whitespace-nowrap">
                              {formatTokens(slot.kvCacheTokens)}
                              <span className="text-muted-foreground"> / {formatTokens(slot.contextSize)}</span>
                            </span>
                          </div>
                        </TableCell>
                        <TableCell className="text-right">{formatMs(slot.ttftMs)}</TableCell>
                        <TableCell className="text-right">{formatMs(slot.elapsedMs)}</TableCell>
                        <TableCell className="text-right">{slot.tokensPerSecond ? slot.tokensPerSecond.toFixed(1) : '—'}</TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </div>

            <div>
              <p className="mb-2 text-xs font-semibold">KV cache per slot · last 15 minutes</p>
              <SlotHeatmap result={history} contextSize={contextSize} fromMs={historyFromMs} toMs={historyToMs} selectedSlot={selected} />
            </div>
          </>
        )}
      </CardContent>
    </Card>
  );
}
