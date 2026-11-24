'use client';

import { cn } from '@/lib/utils';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { StatusBadge, toBadgeVariant } from '@/components/shared/StatusBadge';
import { ExternalBadge } from '@/components/models/external-badge';
import { InlineMeter } from '@/components/charts/inline-meter';
import { thresholdBarClass } from '@/components/charts/chart-theme';
import { ENTITY_OTHER } from '@/components/charts/entity-colors';
import { INSTANCE_STATE_LABELS, engineLabel } from '@/components/observability/labels';
import type { LiveEngineSample } from '@/hooks/use-live-events';
import type { InstanceSummary } from '@/types/metrics';
import { formatCompact, formatMs, formatPct } from '@/utils/metrics-format';

interface InstanceTableProps {
  instances: InstanceSummary[];
  live: LiveEngineSample[];
  colors: Map<string, string>;
  selectedId: string | null;
  onSelect: (id: string) => void;
}

/** One row per engine instance with live sessions, throughput, latency, KV and errors; click a row for its slots. */
export function InstanceTable({ instances, live, colors, selectedId, onSelect }: InstanceTableProps) {
  const byId = new Map(live.map((s) => [s.instanceId, s]));
  const sorted = [...instances].sort((a, b) => a.deploymentName.localeCompare(b.deploymentName) || a.nodeName.localeCompare(b.nodeName));

  return (
    <div className="overflow-x-auto">
      <Table className="text-xs tabular-nums">
        <TableHeader>
          <TableRow>
            <TableHead className="pl-5">Instance</TableHead>
            <TableHead>State</TableHead>
            <TableHead className="text-right">Sessions</TableHead>
            <TableHead className="text-right">Req / min</TableHead>
            <TableHead className="text-right">Gen tok/s</TableHead>
            <TableHead className="text-right">TTFT p50 / p95</TableHead>
            <TableHead className="text-right">E2E p95</TableHead>
            <TableHead className="min-w-32">KV cache</TableHead>
            <TableHead className="text-right">Errors</TableHead>
            <TableHead className="pr-5 text-right">Weight</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {sorted.map((inst) => {
            const s = byId.get(inst.id);
            const ready = inst.state === 'ready';
            const active = s?.activeSessions ?? inst.live.activeSessions;
            const waiting = s?.waitingSessions ?? inst.live.waitingSessions;
            const slots = s?.slotsTotal ?? inst.slots;
            const kv = s?.kvCacheUsagePct ?? inst.live.kvCacheUsagePct;
            const err = inst.live.errorRatePct;
            const selected = inst.id === selectedId;
            return (
              <TableRow
                key={inst.id}
                data-state={selected ? 'selected' : undefined}
                tabIndex={0}
                aria-selected={selected}
                onClick={() => onSelect(inst.id)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    onSelect(inst.id);
                  }
                }}
                className="cursor-pointer outline-none focus-visible:bg-muted/50"
              >
                <TableCell className="py-2.5 pl-5">
                  <div className="flex items-center gap-2">
                    <span aria-hidden className="size-2.5 shrink-0 rounded-[3px]" style={{ backgroundColor: colors.get(inst.deploymentId) ?? ENTITY_OTHER }} />
                    <div className="min-w-0">
                      <p className="flex items-center gap-1.5 truncate text-[13px] font-semibold">
                        {inst.deploymentName}
                        {inst.external && <ExternalBadge compact />}
                      </p>
                      <p className="truncate text-[11px] text-muted-foreground">
                        <span className="font-mono">{inst.nodeName}</span> · {engineLabel(inst.engine)}
                      </p>
                    </div>
                  </div>
                </TableCell>
                <TableCell>
                  <StatusBadge variant={ready ? (inst.health === 'unhealthy' ? 'degraded' : 'running') : toBadgeVariant(inst.state)} className="px-1.5 py-0 text-[10px]">
                    {ready && inst.health === 'unhealthy' ? 'Unhealthy' : INSTANCE_STATE_LABELS[inst.state]}
                  </StatusBadge>
                </TableCell>
                <TableCell className="text-right">
                  {ready ? (
                    <>
                      <span className="font-semibold">{active ?? '—'}</span>
                      <span className="text-muted-foreground">/{slots ?? '—'}</span>
                      {waiting ? <span className="ml-1 font-semibold text-warning-dark dark:text-warning-light">+{waiting}</span> : null}
                    </>
                  ) : (
                    '—'
                  )}
                </TableCell>
                <TableCell className="text-right">{ready ? formatCompact(inst.live.requestsPerMinute, 1) : '—'}</TableCell>
                <TableCell className="text-right">{ready ? formatCompact(s?.generationTps ?? inst.live.generationTps, 0) : '—'}</TableCell>
                <TableCell className="text-right">
                  {ready ? (
                    <>
                      <span className="text-muted-foreground">{formatMs(s?.ttftMsP50)}</span> / <span className="font-semibold">{formatMs(s?.ttftMsP95 ?? inst.live.ttftMsP95)}</span>
                    </>
                  ) : (
                    '—'
                  )}
                </TableCell>
                <TableCell className="text-right">{ready ? formatMs(s?.e2eMsP95) : '—'}</TableCell>
                <TableCell>
                  {ready ? (
                    <div className="flex items-center gap-2">
                      <InlineMeter percent={kv} barClass={thresholdBarClass(kv ?? 0, 85, 95)} label={`KV cache ${formatPct(kv, 0)}`} className="w-16" />
                      <span>{formatPct(kv, 0)}</span>
                    </div>
                  ) : (
                    '—'
                  )}
                </TableCell>
                <TableCell className={cn('text-right', (err ?? 0) >= 5 && 'font-semibold text-error-dark dark:text-error-light')}>{ready ? formatPct(err, 1) : '—'}</TableCell>
                <TableCell className="pr-5 text-right text-muted-foreground">{inst.trafficWeight}</TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </div>
  );
}
