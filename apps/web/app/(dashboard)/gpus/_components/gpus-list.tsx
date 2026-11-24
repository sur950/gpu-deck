'use client';

import { useMemo, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { Cpu, ExternalLink, Gauge, Layers, MemoryStick, MoreVertical, Rocket, Zap } from 'lucide-react';
import { Card, CardContent, CardHeader } from '@/components/ui/card';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu';
import { PageContainer } from '@/components/shared/PageContainer';
import { PageHeader } from '@/components/shared/PageHeader';
import { ListPageSkeleton } from '@/components/shared/ListPageSkeleton';
import { ErrorState } from '@/components/shared/ErrorState';
import { EmptyState } from '@/components/shared/EmptyState';
import { StatusBadge } from '@/components/shared/StatusBadge';
import { StatCard } from '@/components/shared/stat-card';
import { DEVICE_HEALTH_BADGE } from '@/components/fleet/status';
import { ActivityBadge } from '@/components/fleet/activity-badge';
import { VendorBadge } from '@/components/fleet/vendor-badge';
import { VendorStrip } from '@/components/fleet/vendor-strip';
import { SliceBar, useDeploymentColors } from '@/components/fleet/slice-bar';
import { GpuLive } from '@/components/fleet/gpu-live';
import { NewSliceDialog } from '@/components/fleet/slice-dialogs';
import { useDevices, useNodes } from '@/hooks/api/fleet';
import { useUrlState } from '@/hooks/use-url-state';
import { formatBytes } from '@/utils/format';
import { DEVICE_HEALTH_LABELS, type Device, type DeviceHealth, type DeviceVendor } from '@/types/fleet';

const DEFAULTS = { vendor: '', health: '', node: '' };
const HEALTH: DeviceHealth[] = ['healthy', 'degraded', 'unhealthy', 'unknown'];

export function GpusList() {
  const router = useRouter();
  const { params, setParams } = useUrlState(DEFAULTS);
  const [slicing, setSlicing] = useState<Device | null>(null);
  const all = useDevices({ page_size: 500 });
  const nodes = useNodes({ page_size: 500 }, { live: false });

  const devices = useMemo(
    () =>
      (all.data?.items ?? []).filter(
        (d) => (!params.vendor || d.vendor === params.vendor) && (!params.health || d.health === params.health) && (!params.node || d.node_id === params.node),
      ),
    [all.data, params.vendor, params.health, params.node],
  );
  const everything = all.data?.items ?? [];
  const colors = useDeploymentColors();

  if (all.isLoading && !all.data) return <ListPageSkeleton />;

  const vramTotal = everything.reduce((s, d) => s + d.vram_total_bytes, 0);
  const vramReserved = everything.reduce((s, d) => s + (d.vram_reserved_bytes ?? 0), 0);
  const vramUsed = everything.reduce((s, d) => s + (d.live?.memory_used_bytes ?? 0), 0);
  const reporting = everything.filter((d) => d.live?.utilization_pct !== null && d.live?.utilization_pct !== undefined);
  const avgUtil = reporting.length ? reporting.reduce((s, d) => s + (d.live?.utilization_pct ?? 0), 0) / reporting.length : null;
  const power = everything.reduce((s, d) => s + (d.live?.power_watts ?? 0), 0);
  const unhealthy = everything.filter((d) => d.health === 'unhealthy' || d.health === 'degraded').length;
  const nodeName = (id: string) => nodes.data?.items.find((n) => n.id === id)?.name;
  const filtered = !!params.vendor || !!params.health || !!params.node;

  return (
    <PageContainer>
      <PageHeader title="GPUs" description="Every accelerator the agents report — VRAM carved into slices, live load, driver and health." />

      <VendorStrip devices={everything} selected={params.vendor as DeviceVendor | ''} onSelect={(vendor) => setParams({ vendor })} />

      <div className="grid gap-4 md:grid-cols-4">
        <StatCard label="Accelerators" value={everything.length} icon={Cpu} footnote={unhealthy ? `${unhealthy} degraded or unhealthy` : 'All healthy'} />
        <StatCard label="VRAM reserved" value={vramTotal ? `${Math.round((vramReserved / vramTotal) * 100)}%` : '—'} icon={MemoryStick} footnote={`${formatBytes(vramReserved)} of ${formatBytes(vramTotal)} · ${formatBytes(vramUsed)} in use`} />
        <StatCard label="Average utilisation" value={avgUtil === null ? '—' : `${Math.round(avgUtil)}%`} icon={Gauge} footnote={`${reporting.length} of ${everything.length} reporting live`} />
        <StatCard label="Power draw" value={`${Math.round(power)} W`} icon={Zap} footnote="Sum of reported board power (Apple: best effort)" />
      </div>

      {all.isError ? (
        <ErrorState title="Could not load GPUs" description="The control plane did not answer. Retry in a moment." onRetry={() => void all.refetch()} />
      ) : (
        <Card className="rounded-md shadow-premium">
          <CardHeader>
            <div className="flex flex-wrap items-center gap-3">
              <Select value={params.node || 'all'} onValueChange={(v) => setParams({ node: v === 'all' ? '' : String(v) })}>
                <SelectTrigger className="w-48">
                  <SelectValue>{(v: unknown) => (v === 'all' ? 'All nodes' : nodeName(String(v)) ?? 'Node')}</SelectValue>
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">All nodes</SelectItem>
                  {(nodes.data?.items ?? []).map((n) => (
                    <SelectItem key={n.id} value={n.id}>
                      {n.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Select value={params.health || 'all'} onValueChange={(v) => setParams({ health: v === 'all' ? '' : String(v) })}>
                <SelectTrigger className="w-40">
                  <SelectValue>{(v: unknown) => (v === 'all' ? 'Any health' : DEVICE_HEALTH_LABELS[v as DeviceHealth])}</SelectValue>
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">Any health</SelectItem>
                  {HEALTH.map((h) => (
                    <SelectItem key={h} value={h}>
                      {DEVICE_HEALTH_LABELS[h]}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {filtered && (
                <button type="button" className="text-xs text-muted-foreground underline-offset-2 hover:text-foreground hover:underline" onClick={() => setParams({ vendor: '', health: '', node: '' })}>
                  Clear filters
                </button>
              )}
              <span className="ml-auto text-xs text-muted-foreground tabular-nums">
                {devices.length} of {everything.length} GPUs
              </span>
            </div>
          </CardHeader>
          <CardContent>
            {devices.length === 0 ? (
              <EmptyState
                icon={Cpu}
                title={filtered ? 'No GPUs match' : 'No accelerators yet'}
                description={filtered ? 'Clear the filters to see the whole fleet.' : 'Join a node — the agent reports its devices in the first inventory message.'}
              />
            ) : (
              <div className="overflow-x-auto">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>GPU</TableHead>
                      <TableHead>Node</TableHead>
                      <TableHead className="min-w-[260px]">VRAM · slices</TableHead>
                      <TableHead>Util · power · temp</TableHead>
                      <TableHead>Driver · runtime</TableHead>
                      <TableHead>Health</TableHead>
                      <TableHead className="w-10" />
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {devices.map((d) => (
                      <TableRow key={d.id} className="cursor-pointer" onClick={() => router.push(`/gpus/${d.id}`)}>
                        <TableCell>
                          <div className="flex items-start gap-2.5">
                            <VendorBadge vendor={d.vendor} withLabel={false} className="mt-0.5" />
                            <div className="min-w-0">
                              <Link href={`/gpus/${d.id}`} className="font-semibold hover:underline" onClick={(e) => e.stopPropagation()}>
                                {d.name} <span className="font-normal text-muted-foreground">#{d.index}</span>
                              </Link>
                              <div className="max-w-[220px] truncate font-mono text-[10.5px] text-muted-foreground" title={d.uuid ?? ''}>
                                {d.pci_bus_id ?? d.uuid ?? '—'}
                                {d.is_unified_memory ? ' · unified memory' : ''}
                              </div>
                            </div>
                          </div>
                        </TableCell>
                        <TableCell>
                          <Link href={`/nodes/${d.node_id}`} className="text-xs font-medium hover:underline" onClick={(e) => e.stopPropagation()}>
                            {d.node_name ?? nodeName(d.node_id) ?? d.node_id.slice(0, 8)}
                          </Link>
                          <div className="text-[11px] text-muted-foreground">{(d.instance_ids ?? []).length} instance(s)</div>
                        </TableCell>
                        <TableCell>
                          <SliceBar device={d} colors={colors} heightClass="h-2.5" />
                        </TableCell>
                        <TableCell>
                          <GpuLive device={d} inline />
                        </TableCell>
                        <TableCell className="text-xs">
                          <div className="font-mono text-[11px]">{d.driver_version ?? '—'}</div>
                          <div className="text-[11px] text-muted-foreground">
                            {d.runtime_version ?? ''}
                            {d.compute_capability ? ` · cc ${d.compute_capability}` : ''}
                          </div>
                        </TableCell>
                        <TableCell>
                          <div className="flex flex-wrap items-center gap-1">
                            <StatusBadge variant={DEVICE_HEALTH_BADGE[d.health]}>{DEVICE_HEALTH_LABELS[d.health]}</StatusBadge>
                            <ActivityBadge activity={d.activity} outside={d.outside_processes ?? 0} lastSeenAt={d.live?.ts ?? d.last_seen_at} usedBytes={d.vram_usage_stale ? null : (d.vram_used_bytes ?? d.live?.memory_used_bytes ?? null)} reservedBytes={d.vram_reserved_bytes ?? 0} unified={d.is_unified_memory} />
                          </div>
                          {d.health_reason && <div className="mt-0.5 max-w-[160px] truncate text-[11px] text-muted-foreground">{d.health_reason}</div>}
                        </TableCell>
                        <TableCell>
                          <DropdownMenu>
                            <DropdownMenuTrigger onClick={(e) => e.stopPropagation()} className="inline-flex h-8 w-8 cursor-pointer items-center justify-center rounded-md hover:bg-accent" aria-label="GPU actions">
                              <MoreVertical className="h-4 w-4" />
                            </DropdownMenuTrigger>
                            <DropdownMenuContent align="end" onClick={(e) => e.stopPropagation()}>
                              <DropdownMenuItem onClick={() => router.push(`/gpus/${d.id}`)}>
                                <ExternalLink className="mr-2 h-4 w-4" />
                                View details
                              </DropdownMenuItem>
                              <DropdownMenuItem onClick={() => setSlicing(d)}>
                                <Layers className="mr-2 h-4 w-4" />
                                New slice
                              </DropdownMenuItem>
                              <DropdownMenuItem onClick={() => router.push(`/deployments/new?node_id=${d.node_id}&device_id=${d.id}`)}>
                                <Rocket className="mr-2 h-4 w-4" />
                                Deploy on this GPU
                              </DropdownMenuItem>
                            </DropdownMenuContent>
                          </DropdownMenu>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            )}
          </CardContent>
        </Card>
      )}
      {slicing && <NewSliceDialog key={slicing.id} device={slicing} open onOpenChange={(o) => !o && setSlicing(null)} />}
    </PageContainer>
  );
}
