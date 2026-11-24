'use client';

import Link from 'next/link';
import { AlertTriangle, ArrowUpRight, Cpu } from 'lucide-react';
import { cn } from '@/lib/utils';
import { activityLabel, asActivity, effectiveActivity, outsideHeld } from '@/components/fleet/activity-badge';
import { nodeStateView } from '@/components/fleet/node-status';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { EmptyState } from '@/components/shared/EmptyState';
import { InlineMeter } from '@/components/charts/inline-meter';
import { thresholdBarClass } from '@/components/charts/chart-theme';
import { gpuLabel } from '@/components/observability/labels';
import type { LiveGpuSample } from '@/hooks/use-live-events';
import type { DeviceSummary, NodeSummary } from '@/types/topology';
import { formatGiB, formatGiBPair, formatPct, formatTemp, formatWatts } from '@/utils/metrics-format';

const HOT_C = 80;

interface FleetCardProps {
  devices: DeviceSummary[] | undefined;
  nodes: NodeSummary[] | undefined;
  gpuLive: LiveGpuSample[];
  isLoading: boolean;
  className?: string;
}

interface Row {
  device: DeviceSummary;
  util: number | null;
  used: number | null;
  total: number;
  power: number | null;
  temp: number | null;
}

/** Every GPU in the fleet with live utilisation, VRAM, power and temperature, grouped by node. */
export function FleetCard({ devices, nodes, gpuLive, isLoading, className }: FleetCardProps) {
  const liveById = new Map(gpuLive.map((g) => [g.deviceId, g]));
  const rows: Row[] = (devices ?? []).map((d) => {
    const live = liveById.get(d.id);
    return {
      device: d,
      util: live?.utilizationPct ?? d.live.utilizationPct,
      used: live?.memoryUsedBytes ?? d.live.memoryUsedBytes,
      total: live?.memoryTotalBytes ?? d.vramTotalBytes,
      power: live?.powerWatts ?? d.live.powerWatts,
      temp: live?.temperatureC ?? d.live.temperatureC,
    };
  });

  const byNode = new Map<string, Row[]>();
  for (const row of rows) byNode.set(row.device.nodeId, [...(byNode.get(row.device.nodeId) ?? []), row]);
  const nodeOrder = [...byNode.keys()].sort((a, b) => (byNode.get(a)?.[0].device.nodeName ?? '').localeCompare(byNode.get(b)?.[0].device.nodeName ?? ''));

  const usedTotal = rows.reduce((s, r) => s + (r.used ?? 0), 0);
  const capTotal = rows.reduce((s, r) => s + r.total, 0);
  const powerTotal = rows.reduce((s, r) => s + (r.power ?? 0), 0);
  const unhealthy = rows.filter((r) => r.device.health === 'unhealthy' || r.device.health === 'degraded').length;
  const hot = rows.filter((r) => (r.temp ?? 0) >= HOT_C).length;

  return (
    <Card className={cn('h-full gap-0 rounded-xl py-0', className)}>
      <CardHeader className="flex flex-row items-start justify-between gap-3 border-b border-border px-5 py-4">
        <div className="space-y-0.5">
          <CardTitle className="text-sm font-semibold">GPU fleet</CardTitle>
          <CardDescription>
            {rows.length} GPU{rows.length === 1 ? '' : 's'} on {byNode.size} node{byNode.size === 1 ? '' : 's'} · live
          </CardDescription>
        </div>
        <div className="flex items-center gap-1.5">
          {unhealthy > 0 || hot > 0 ? (
            <span className="inline-flex items-center gap-1 rounded-full border border-warning/30 bg-warning/10 px-2 py-0.5 text-[11px] font-semibold text-warning-dark dark:text-warning-light">
              <AlertTriangle className="size-3" />
              {unhealthy > 0 ? `${unhealthy} need attention` : `${hot} running hot`}
            </span>
          ) : rows.length > 0 ? (
            <span className="inline-flex items-center gap-1 rounded-full border border-success/30 bg-success/10 px-2 py-0.5 text-[11px] font-semibold text-success-dark dark:text-success-light">
              <span aria-hidden className="size-1.5 rounded-full bg-success" />
              All healthy
            </span>
          ) : null}
          <Link
            href="/observability/infra"
            aria-label="Open GPU metrics"
            className="flex size-7 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
          >
            <ArrowUpRight className="size-4" />
          </Link>
        </div>
      </CardHeader>

      <CardContent className="flex flex-1 flex-col px-0 py-0">
        {isLoading && rows.length === 0 ? (
          <div className="space-y-3 p-5">
            {Array.from({ length: 3 }).map((_, i) => (
              <Skeleton key={i} className="h-12 rounded-md" />
            ))}
          </div>
        ) : rows.length === 0 ? (
          <EmptyState icon={Cpu} title="No GPUs reported" description="A node's agent reports its GPUs as soon as it joins." />
        ) : (
          <div className="divide-y divide-border">
            {nodeOrder.map((nodeId) => {
              const group = byNode.get(nodeId) ?? [];
              const node = nodes?.find((n) => n.id === nodeId);
              const multi = group.length > 1;
              const state = node ? nodeStateView(node) : null;
              // Node activity, never "idle" while its GPUs hold memory beyond GPU Deck's reservations.
              const nodeHeld = group.reduce((sum, r) => sum + (r.device.isUnifiedMemory ? 0 : outsideHeld({ usedBytes: r.used, reservedBytes: r.device.vramReservedBytes })), 0);
              const held = group.some((r) => effectiveActivity('idle', { usedBytes: r.used, reservedBytes: r.device.vramReservedBytes, unified: r.device.isUnifiedMemory }) === 'external');
              const reported = asActivity(node?.activity);
              const nodeActivity = (reported === 'idle' || reported === null) && held ? 'external' : reported;
              return (
                <div key={nodeId} className="px-5 py-3">
                  <div className="mb-2 flex items-center justify-between gap-2 text-[11px]">
                    <span className="flex min-w-0 items-center gap-1.5 font-medium text-muted-foreground">
                      <span aria-hidden className={cn('size-1.5 shrink-0 rounded-full', state ? state.dot : 'bg-success')} />
                      <span className="truncate text-[11px] text-foreground/80 font-mono">{group[0].device.nodeName}</span>
                      {state && node && (state.detached || node.status !== 'online') && (
                        <span className={cn(state.detached && 'text-error-dark dark:text-error-light')} title={state.title}>
                          · {state.label}
                        </span>
                      )}
                      {node && !state?.detached && nodeActivity && (nodeActivity === 'external' || nodeActivity === 'mixed') && (
                        <span className="truncate text-info-dark dark:text-info-light">· {activityLabel(nodeActivity, node.outsideProcesses, null, nodeHeld)}</span>
                      )}
                    </span>
                    {node?.os && <span className="shrink-0 text-muted-foreground/80">{node.os === 'macos' ? 'macOS' : node.os === 'linux' ? 'Linux' : node.os}</span>}
                  </div>
                  <ul className="space-y-2.5">
                    {group.map((row) => {
                      const vramPct = row.total > 0 && row.used !== null ? (row.used / row.total) * 100 : null;
                      const isHot = (row.temp ?? 0) >= HOT_C;
                      return (
                        <li key={row.device.id}>
                          <Link
                            href={`/observability/infra/${row.device.id}`}
                            className="-mx-2 grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 gap-y-1 rounded-md px-2 py-1 outline-none transition-colors hover:bg-accent/50 focus-visible:ring-2 focus-visible:ring-ring/30"
                          >
                            <span className="flex min-w-0 items-baseline gap-2">
                              <span className="truncate text-[13px] font-semibold">{gpuLabel(row.device, multi)}</span>
                              <span className="truncate text-[11px] text-muted-foreground">{formatGiB(row.total, 0)}{row.device.isUnifiedMemory ? ' unified' : ''}</span>
                            </span>
                            <span className="text-right text-[13px] font-semibold tabular-nums">{formatPct(row.util, 0)}</span>
                            <InlineMeter percent={row.util} label={`Utilisation ${formatPct(row.util, 0)}`} className="col-span-2" />
                            <span className="col-span-2 flex items-center gap-3 text-[11px] text-muted-foreground tabular-nums">
                              <span className="flex min-w-0 flex-1 items-center gap-2">
                                <InlineMeter
                                  percent={vramPct}
                                  barClass={thresholdBarClass(vramPct ?? 0, 80, 95)}
                                  label={`VRAM ${formatPct(vramPct, 0)}`}
                                  heightClass="h-1"
                                  className="w-14 shrink-0"
                                />
                                <span className="truncate">{formatGiBPair(row.used, row.total)}</span>
                              </span>
                              <span>{formatWatts(row.power)}</span>
                              <span className={cn(isHot && 'font-semibold text-warning-dark dark:text-warning-light')}>
                                {isHot && <AlertTriangle aria-label="Running hot" className="mr-0.5 inline size-3 -translate-y-px" />}
                                {formatTemp(row.temp)}
                              </span>
                            </span>
                          </Link>
                        </li>
                      );
                    })}
                  </ul>
                </div>
              );
            })}
          </div>
        )}

        {rows.length > 0 && (
          <div className="mt-auto flex items-center justify-between border-t border-border px-5 py-3 text-[11px] text-muted-foreground tabular-nums">
            <span>
              VRAM <span className="font-semibold text-foreground">{formatGiBPair(usedTotal, capTotal)}</span>
            </span>
            <span>
              Power <span className="font-semibold text-foreground">{formatWatts(powerTotal)}</span>
            </span>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
