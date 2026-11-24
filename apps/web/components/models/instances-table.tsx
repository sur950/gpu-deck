'use client';

import { useState } from 'react';
import Link from 'next/link';
import { ArrowDownToLine, MoreVertical, RotateCw, Square } from 'lucide-react';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from '@/components/ui/dropdown-menu';
import { StatusBadge } from '@/components/shared/StatusBadge';
import { ConfirmationDialog } from '@/components/shared/ConfirmationDialog';
import { INSTANCE_BADGE, INSTANCE_HEALTH_BADGE } from '@/components/fleet/status';
import { EngineBadge } from '@/components/models/model-badges';
import { ExternalBadge } from '@/components/models/external-badge';
import { EngineEndpoint } from '@/components/models/engine-endpoint';
import { useDrainInstance, useRestartInstance, useStopInstance } from '@/hooks/api/models-deployments';
import { cn } from '@/lib/utils';
import { formatRelativeTime } from '@/utils/format';
import { INSTANCE_STATE_LABELS, isExternal, type Instance } from '@/types/models';

type Column = 'deployment' | 'node' | 'engine';

interface InstancesTableProps {
  instances: Instance[];
  /** Optional columns; the deployment page hides "deployment", the node page hides "node". */
  columns?: Column[];
  /** Device id → short label (e.g. "L40S #0"). */
  deviceLabels?: Map<string, string>;
  actions?: boolean;
  className?: string;
}

function num(value: number | null | undefined, digits = 0): string {
  return value === null || value === undefined ? '—' : value.toLocaleString(undefined, { maximumFractionDigits: digits, minimumFractionDigits: digits });
}

/** Live instance table: state, health, placement, sessions and latency from the latest engine sample. */
export function InstancesTable({ instances, columns = ['deployment', 'node'], deviceLabels, actions = true, className }: InstancesTableProps) {
  const drain = useDrainInstance();
  const restart = useRestartInstance();
  const stop = useStopInstance();
  const [stopping, setStopping] = useState<Instance | null>(null);
  const show = (c: Column) => columns.includes(c);

  return (
    <div className={cn('overflow-x-auto', className)}>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Instance</TableHead>
            {show('deployment') && <TableHead>Deployment</TableHead>}
            {show('node') && <TableHead>Node · GPUs</TableHead>}
            {!show('node') && <TableHead>GPUs</TableHead>}
            {show('engine') && <TableHead>Engine</TableHead>}
            <TableHead>State</TableHead>
            <TableHead className="text-right">Weight</TableHead>
            <TableHead className="text-right">Active / waiting</TableHead>
            <TableHead className="text-right">Req/min</TableHead>
            <TableHead className="text-right">TTFT p95</TableHead>
            <TableHead className="text-right">KV</TableHead>
            <TableHead>Up</TableHead>
            {actions && <TableHead className="w-10" />}
          </TableRow>
        </TableHeader>
        <TableBody>
          {instances.map((inst) => {
            const live = inst.live ?? {};
            const devices = inst.device_ids.map((id) => deviceLabels?.get(id) ?? `GPU ${id.slice(0, 4)}`);
            const ready = inst.state === 'ready';
            return (
              <TableRow key={inst.id} className={cn(!ready && 'bg-muted/20')}>
                <TableCell>
                  <div className="flex items-center gap-1.5 font-mono text-[11px] font-medium">
                    {inst.served_model_name ?? inst.deployment_name}
                    {isExternal(inst) && <ExternalBadge compact />}
                  </div>
                  <div className="font-mono text-[10.5px] text-muted-foreground">
                    {inst.id.slice(0, 8)} · <EngineEndpoint port={inst.port} nodeName={inst.node_name} compact />
                    {inst.pd_role ? ` · ${inst.pd_role}` : ''}
                  </div>
                </TableCell>
                {show('deployment') && (
                  <TableCell>
                    <Link href={`/deployments/${inst.deployment_id}`} className="font-medium hover:underline">
                      {inst.deployment_name ?? inst.deployment_id.slice(0, 8)}
                    </Link>
                    {inst.model_name && <div className="text-[11px] text-muted-foreground">{inst.model_name}</div>}
                  </TableCell>
                )}
                <TableCell>
                  {show('node') && (
                    <Link href={`/nodes/${inst.node_id}`} className="font-medium hover:underline">
                      {inst.node_name ?? inst.node_id.slice(0, 8)}
                    </Link>
                  )}
                  <div className={cn('flex flex-wrap gap-1', show('node') && 'mt-0.5')}>
                    {inst.device_ids.map((id, i) => (
                      <Link key={id} href={`/gpus/${id}`} className="rounded-sm border border-border px-1 font-mono text-[10px] text-muted-foreground hover:border-foreground/30 hover:text-foreground">
                        {devices[i]}
                      </Link>
                    ))}
                  </div>
                </TableCell>
                {show('engine') && (
                  <TableCell>
                    <EngineBadge engine={inst.engine_name} />
                  </TableCell>
                )}
                <TableCell>
                  <div className="flex flex-wrap items-center gap-1">
                    <StatusBadge variant={INSTANCE_BADGE[inst.state]}>{INSTANCE_STATE_LABELS[inst.state]}</StatusBadge>
                    {ready && inst.health !== 'healthy' && <StatusBadge variant={INSTANCE_HEALTH_BADGE[inst.health]}>{inst.health}</StatusBadge>}
                  </div>
                  {inst.state_message && <div className="mt-0.5 max-w-[220px] truncate text-[10.5px] text-muted-foreground" title={inst.state_message}>{inst.state_message}</div>}
                </TableCell>
                <TableCell className="text-right font-mono text-xs tabular-nums">{inst.traffic_weight}</TableCell>
                <TableCell className="text-right font-mono text-xs tabular-nums">
                  {ready ? (
                    <>
                      {num(live.active_sessions)}
                      <span className="text-muted-foreground"> / </span>
                      <span className={cn((live.waiting_sessions ?? 0) > 0 && 'text-warning-dark dark:text-warning-light')}>{num(live.waiting_sessions)}</span>
                    </>
                  ) : (
                    '—'
                  )}
                </TableCell>
                <TableCell className="text-right font-mono text-xs tabular-nums">{ready ? num(live.requests_per_minute, 1) : '—'}</TableCell>
                <TableCell className="text-right font-mono text-xs tabular-nums">{ready && live.ttft_ms_p95 ? `${num(live.ttft_ms_p95)} ms` : '—'}</TableCell>
                <TableCell className="text-right font-mono text-xs tabular-nums">{ready && live.kv_cache_usage_pct !== null && live.kv_cache_usage_pct !== undefined ? `${num(live.kv_cache_usage_pct)}%` : '—'}</TableCell>
                <TableCell className="text-xs text-muted-foreground whitespace-nowrap">{inst.ready_at && ready ? formatRelativeTime(inst.ready_at).replace(' ago', '') : '—'}</TableCell>
                {actions && isExternal(inst) && (
                  <TableCell>
                    <span className="sr-only">Observed only: no actions</span>
                  </TableCell>
                )}
                {actions && !isExternal(inst) && (
                  <TableCell>
                    <DropdownMenu>
                      <DropdownMenuTrigger className="inline-flex h-8 w-8 cursor-pointer items-center justify-center rounded-md hover:bg-accent" aria-label="Instance actions">
                        <MoreVertical className="h-4 w-4" />
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end">
                        <DropdownMenuItem
                          disabled={!ready}
                          onClick={() => drain.mutate({ id: inst.id, deploymentId: inst.deployment_id, body: { timeout_seconds: 120, then: 'none' } })}
                        >
                          <ArrowDownToLine className="mr-2 h-4 w-4" />
                          Drain (stop new traffic)
                        </DropdownMenuItem>
                        <DropdownMenuItem
                          disabled={inst.state !== 'ready' && inst.state !== 'error'}
                          onClick={() => restart.mutate({ id: inst.id, deploymentId: inst.deployment_id })}
                        >
                          <RotateCw className="mr-2 h-4 w-4" />
                          Restart in place
                        </DropdownMenuItem>
                        <DropdownMenuSeparator />
                        <DropdownMenuItem variant="destructive" disabled={inst.state === 'stopped' || inst.state === 'unloading'} onClick={() => setStopping(inst)}>
                          <Square className="mr-2 h-4 w-4" />
                          Stop and remove replica
                        </DropdownMenuItem>
                      </DropdownMenuContent>
                    </DropdownMenu>
                  </TableCell>
                )}
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
      <ConfirmationDialog
        open={!!stopping}
        onOpenChange={(open) => !open && setStopping(null)}
        title="Stop this instance?"
        description={`The replica on ${stopping?.node_name ?? 'its node'} is drained and unloaded, and the deployment's replica count drops by one. Other replicas keep serving.`}
        confirmText="Stop instance"
        variant="destructive"
        isLoading={stop.isPending}
        onConfirm={() => stopping && stop.mutate({ id: stopping.id, deploymentId: stopping.deployment_id })}
      />
    </div>
  );
}
