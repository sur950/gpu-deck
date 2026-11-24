'use client';

import { useMemo, useState } from 'react';
import Link from 'next/link';
import { useParams, useRouter } from 'next/navigation';
import { Activity, Layers, Plus, Rocket, SlidersHorizontal, SquareTerminal, X } from 'lucide-react';
import { Button, buttonVariants } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Switch } from '@/components/ui/switch';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { PageContainer } from '@/components/shared/PageContainer';
import { ListPageSkeleton } from '@/components/shared/ListPageSkeleton';
import { ErrorState } from '@/components/shared/ErrorState';
import { EmptyState } from '@/components/shared/EmptyState';
import { StatusBadge } from '@/components/shared/StatusBadge';
import { DetailList } from '@/components/shared/detail-list';
import { DEVICE_HEALTH_BADGE, SLICE_BADGE } from '@/components/fleet/status';
import { VendorBadge, VENDOR_STACK } from '@/components/fleet/vendor-badge';
import { SliceBar, SliceLegend, outsideBytes, useDeploymentColors } from '@/components/fleet/slice-bar';
import { GpuLive } from '@/components/fleet/gpu-live';
import { Heartbeat } from '@/components/fleet/heartbeat';
import { NewSliceDialog, ResizeSliceDialog, freeVram, sliceOwner } from '@/components/fleet/slice-dialogs';
import { GpuProcessesTable } from '@/components/fleet/gpu-processes-table';
import { ActivityBadge, IDLE_BASELINE_BYTES } from '@/components/fleet/activity-badge';
import { deviceExtraRows } from '@/components/fleet/device-properties';
import { ConfirmationDialog } from '@/components/shared/ConfirmationDialog';
import { InstancesTable } from '@/components/models/instances-table';
import { useDeleteDeviceSlice, useDevice, useDeviceSlices } from '@/hooks/api/fleet';
import { useInstances } from '@/hooks/api/models-deployments';
import { cn } from '@/lib/utils';
import { formatBytes, formatRelativeTime } from '@/utils/format';
import { DEVICE_HEALTH_LABELS, DEVICE_VENDOR_LABELS, type DeviceSlice } from '@/types/fleet';

export function GpuDetail() {
  const router = useRouter();
  const { id } = useParams<{ id: string }>();
  const { data: device, isLoading, isError, refetch } = useDevice(id);
  const [history, setHistory] = useState(false);
  const slices = useDeviceSlices(id, history);
  const instances = useInstances({ node_id: device?.node_id, page_size: 100 }, { enabled: !!device });
  const [resizing, setResizing] = useState<DeviceSlice | null>(null);
  const [creating, setCreating] = useState(false);
  const [releasing, setReleasing] = useState<DeviceSlice | null>(null);
  const release = useDeleteDeviceSlice();

  const active = useMemo(() => (device?.slices ?? []).filter((s) => s.status !== 'released'), [device]);
  const colors = useDeploymentColors();

  if (isLoading) return <ListPageSkeleton stats={0} rows={4} />;
  if (isError || !device) {
    return (
      <PageContainer>
        <ErrorState title="GPU not found" description="The node may have been removed, or the control plane is unreachable." onRetry={() => void refetch()} />
      </PageContainer>
    );
  }

  const reserved = active.reduce((s, x) => s + x.vram_bytes, 0);
  const free = freeVram(device);
  const outside = outsideBytes(device, reserved);
  const onDevice = (instances.data?.items ?? []).filter((i) => i.device_ids.includes(device.id));
  const extra = deviceExtraRows(device.extra);
  const usedNow = device.vram_usage_stale ? null : (device.vram_used_bytes ?? device.live?.memory_used_bytes ?? null);
  const heldOutside = outside.bytes >= IDLE_BASELINE_BYTES && !device.is_unified_memory;
  const labelFor = new Map([[device.id, `${device.name.replace(/^NVIDIA |^AMD /, '')} #${device.index}`]]);

  return (
    <PageContainer>
      <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div className="space-y-2">
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="text-2xl font-semibold tracking-tight sm:text-[28px]">
              {device.name} <span className="text-muted-foreground">#{device.index}</span>
            </h1>
            <StatusBadge variant={DEVICE_HEALTH_BADGE[device.health]}>{DEVICE_HEALTH_LABELS[device.health]}</StatusBadge>
            <ActivityBadge activity={device.activity} outside={device.outside_processes ?? 0} lastSeenAt={device.live?.ts ?? device.last_seen_at} usedBytes={usedNow} reservedBytes={reserved} unified={device.is_unified_memory} />
          </div>
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-muted-foreground">
            <VendorBadge vendor={device.vendor} />
            <span>·</span>
            <Link href={`/nodes/${device.node_id}`} className="hover:underline">
              {device.node_name ?? 'node'}
            </Link>
            <span>·</span>
            <span className="text-xs">
              last sample <Heartbeat at={device.live?.ts ?? device.last_seen_at} />
            </span>
          </div>
          {device.health_reason && <p className="text-sm text-warning-dark dark:text-warning-light">{device.health_reason}</p>}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Link href={`/observability/infra?device_id=${device.id}`} className={cn(buttonVariants({ variant: 'outline', size: 'sm' }), 'rounded-sm')}>
            <Activity className="size-4" />
            Metrics history
          </Link>
          <Button variant="outline" size="sm" className="rounded-sm" onClick={() => router.push(`/deployments/new?node_id=${device.node_id}&device_id=${device.id}`)}>
            <Rocket className="size-4" />
            Deploy here
          </Button>
          <Button size="sm" className="rounded-sm" onClick={() => setCreating(true)}>
            <Plus className="size-4" />
            New slice
          </Button>
        </div>
      </div>

      <div className="grid items-start gap-6 lg:grid-cols-3">
        <div className="space-y-6 lg:col-span-2">
          <Card className="rounded-md shadow-premium">
            <CardHeader>
              <CardTitle>Memory map</CardTitle>
              <CardDescription>
                {formatBytes(device.vram_total_bytes)} {device.is_unified_memory ? 'unified memory (75 % usable for models, the hatched part stays with macOS)' : 'VRAM'}
                {typeof device.vram_used_bytes === 'number' ? ` · ${formatBytes(device.vram_used_bytes)} in use` : ''} · {formatBytes(free)} free for new slices after headroom
                {outside.bytes > 0 ? ' and memory used outside GPU Deck' : ''}
                {device.vram_usage_stale ? ' (no fresh sample: only GPU Deck reservations are counted)' : ''}
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-5">
              <SliceBar device={device} colors={colors} heightClass="h-6" />
              <SliceLegend slices={active} colors={colors} outside={outside.bytes} />
              <GpuLive device={device} />
            </CardContent>
          </Card>

          <Card className="rounded-md shadow-premium">
            <CardHeader className="flex flex-row items-start justify-between gap-4">
              <div>
                <CardTitle>Slices</CardTitle>
                <CardDescription>Reservations, deployment replicas and SSH instances. Resizing a deployment&apos;s slice swaps its replicas one at a time.</CardDescription>
              </div>
              <label className="flex items-center gap-2 text-xs text-muted-foreground">
                <Switch size="sm" checked={history} onCheckedChange={setHistory} aria-label="Include released slices" />
                Show released
              </label>
            </CardHeader>
            <CardContent>
              {(slices.data?.items.length ?? 0) === 0 ? (
                <EmptyState
                  icon={Layers}
                  title="No slices"
                  description={
                    heldOutside
                      ? `Nothing is reserved by GPU Deck, but ${formatBytes(outside.bytes)} is held by processes outside GPU Deck; ${formatBytes(free)} is free for new slices.`
                      : `Nothing is reserved here; ${formatBytes(free)} is free for new slices. Carve a slice for a model, or let the scheduler place a deployment here.`
                  }
                  action={
                    <Button size="sm" className="rounded-sm" onClick={() => setCreating(true)}>
                      <Plus className="size-4" />
                      New slice
                    </Button>
                  }
                />
              ) : (
                <div className="overflow-x-auto">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>Slice</TableHead>
                        <TableHead className="text-right">VRAM</TableHead>
                        <TableHead className="min-w-[160px]">Share of GPU</TableHead>
                        <TableHead className="text-right">Compute</TableHead>
                        <TableHead>Status</TableHead>
                        <TableHead>Since</TableHead>
                        <TableHead className="w-24" />
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {slices.data?.items.map((s) => (
                        <TableRow key={s.id} className={cn(s.status === 'released' && 'opacity-60')}>
                          <TableCell>
                            <div className="flex items-center gap-2">
                              <span className="size-2.5 rounded-[3px]" style={{ backgroundColor: colors.get(s.deployment_id ?? s.id) }} aria-hidden />
                              {s.deployment_id ? (
                                <Link href={`/deployments/${s.deployment_id}`} className="font-medium hover:underline">
                                  {s.name ?? 'slice'}
                                </Link>
                              ) : s.gpu_instance_id ? (
                                <Link href={`/gpu-instances/${s.gpu_instance_id}`} className="font-medium hover:underline">
                                  {s.name ?? 'SSH instance'}
                                </Link>
                              ) : (
                                <span className="font-medium">{s.name ?? 'reservation'}</span>
                              )}
                            </div>
                            <div className="font-mono text-[10.5px] text-muted-foreground">
                              {s.instance_id ? `replica ${s.instance_id.slice(0, 8)}` : s.gpu_instance_id ? 'SSH GPU instance' : s.deployment_id ? 'replica pending' : 'standalone reservation'}
                            </div>
                          </TableCell>
                          <TableCell className="text-right font-mono text-xs tabular-nums">{formatBytes(s.vram_bytes)}</TableCell>
                          <TableCell>
                            <div className="h-1.5 overflow-hidden rounded-full bg-muted">
                              <div className="h-full rounded-full" style={{ width: `${Math.min(100, (s.vram_bytes / device.vram_total_bytes) * 100)}%`, backgroundColor: colors.get(s.deployment_id ?? s.id) }} />
                            </div>
                            <div className="mt-0.5 text-[10.5px] text-muted-foreground tabular-nums">{((s.vram_bytes / device.vram_total_bytes) * 100).toFixed(1)}%</div>
                          </TableCell>
                          <TableCell className="text-right font-mono text-xs tabular-nums">{s.compute_pct}%</TableCell>
                          <TableCell>
                            <StatusBadge variant={SLICE_BADGE[s.status]}>
                              {s.status === 'released' ? 'Released' : s.status === 'active' ? 'Active' : sliceOwner(s) === 'reservation' ? 'Reserved' : 'Loading'}
                            </StatusBadge>
                          </TableCell>
                          <TableCell className="text-xs text-muted-foreground">{s.released_at ? `released ${formatRelativeTime(s.released_at)}` : formatRelativeTime(s.created_at)}</TableCell>
                          <TableCell className="text-right">
                            {s.status !== 'released' && (
                              <div className="flex justify-end gap-1">
                                {sliceOwner(s) === 'reservation' && (
                                  <Link
                                    href={`/gpu-instances/new?slice_id=${s.id}&node_id=${device.node_id}&device_id=${device.id}`}
                                    className="inline-flex h-7 items-center gap-1 rounded-md px-2 text-xs font-medium hover:bg-muted"
                                    title="Request an SSH GPU instance on this slice"
                                  >
                                    <SquareTerminal className="size-3" />
                                    SSH
                                  </Link>
                                )}
                                {sliceOwner(s) !== 'gpu_instance' && (
                                  <Button variant="ghost" size="xs" onClick={() => setResizing(s)}>
                                    <SlidersHorizontal className="size-3" />
                                    Resize
                                  </Button>
                                )}
                                <Button variant="ghost" size="icon-xs" className="text-muted-foreground hover:text-destructive" onClick={() => setReleasing(s)} aria-label={`Release ${s.name ?? 'slice'}`}>
                                  <X className="size-3.5" />
                                </Button>
                              </div>
                            )}
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
              )}
            </CardContent>
          </Card>
        </div>
        <Card className="rounded-md shadow-premium">
          <CardHeader>
            <CardTitle>Properties</CardTitle>
            <CardDescription>{VENDOR_STACK[device.vendor]}</CardDescription>
          </CardHeader>
          <CardContent>
            <DetailList
              items={[
                { label: 'Vendor', value: DEVICE_VENDOR_LABELS[device.vendor] },
                { label: 'UUID', value: device.uuid, mono: true },
                { label: 'PCI bus', value: device.pci_bus_id, mono: true },
                { label: 'NUMA node', value: device.numa_node },
                { label: 'Driver', value: device.driver_version, mono: true },
                { label: 'Runtime', value: device.runtime_version },
                { label: 'Compute capability', value: device.compute_capability, mono: true },
                { label: device.vendor === 'apple' ? 'GPU cores' : 'Cores', value: device.core_count?.toLocaleString() },
                { label: 'Max power', value: device.max_power_watts ? `${device.max_power_watts} W` : 'not reported' },
                ...extra,
              ]}
            />
          </CardContent>
        </Card>
      </div>

      <Card className="rounded-md shadow-premium">
        <CardHeader>
          <CardTitle>Instances on this GPU</CardTitle>
          <CardDescription>Engine processes whose slice lives here (tensor-parallel instances span several GPUs), and external servers GPU Deck observes whose processes hold memory here.</CardDescription>
        </CardHeader>
        <CardContent>
          {onDevice.length === 0 ? (
            <p className="py-6 text-center text-sm text-muted-foreground">No instances on this GPU.</p>
          ) : (
            <InstancesTable instances={onDevice} columns={['deployment', 'engine']} deviceLabels={labelFor} />
          )}
        </CardContent>
      </Card>

      <Card className="rounded-md shadow-premium">
        <CardHeader>
          <CardTitle>Processes on this GPU</CardTitle>
          <CardDescription>
            What the driver reports holding memory here (NVIDIA, NVML), with the deployment or external server each one belongs to.
            {device.processes_ts ? ` Sampled ${formatRelativeTime(device.processes_ts)}.` : ''}
          </CardDescription>
        </CardHeader>
        <CardContent>
          <GpuProcessesTable processes={device.processes ?? []} total={device.vram_total_bytes} vendor={device.vendor} stale={device.activity === 'detached' || device.activity === 'stale'} />
        </CardContent>
      </Card>

      {resizing && <ResizeSliceDialog key={resizing.id} slice={resizing} device={device} open onOpenChange={(o) => !o && setResizing(null)} />}
      {creating && <NewSliceDialog device={device} open onOpenChange={setCreating} />}
      <ConfirmationDialog
        open={!!releasing}
        onOpenChange={(o) => !o && setReleasing(null)}
        title={`Release ${releasing?.name ?? 'this slice'}?`}
        description={
          !releasing
            ? ''
            : sliceOwner(releasing) === 'reservation'
              ? 'The VRAM and compute go back to the scheduler.'
              : sliceOwner(releasing) === 'deployment'
                ? 'The replica on this slice is drained and removed (the deployment loses one replica), then the slice is released.'
                : 'The SSH GPU instance using it is stopped (sessions close), then the slice is released.'
        }
        confirmText="Release slice"
        variant="destructive"
        isLoading={release.isPending}
        onConfirm={() => {
          if (releasing) release.mutate({ deviceId: device.id, sliceId: releasing.id, force: sliceOwner(releasing) !== 'reservation' }, { onSuccess: () => setReleasing(null) });
        }}
      />
    </PageContainer>
  );
}
