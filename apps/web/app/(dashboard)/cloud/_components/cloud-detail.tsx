'use client';

import { useState } from 'react';
import Link from 'next/link';
import { useParams, useRouter } from 'next/navigation';
import { AlertTriangle, ArrowLeft, ArrowRight, CheckCircle2, Play, Power, Receipt, RefreshCw, Square, Trash2 } from 'lucide-react';
import { Button, buttonVariants } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { PageContainer } from '@/components/shared/PageContainer';
import { ListPageSkeleton } from '@/components/shared/ListPageSkeleton';
import { ErrorState } from '@/components/shared/ErrorState';
import { StatusBadge } from '@/components/shared/StatusBadge';
import { DetailList } from '@/components/shared/detail-list';
import { LabelChips } from '@/components/shared/label-chips';
import { RelativeTime } from '@/components/shared/relative-time';
import { CopyButton } from '@/components/shared/copy-button';
import { ProviderMark } from '@/components/cloud/provider-mark';
import { CloudLifecycle } from '@/components/cloud/cloud-lifecycle';
import { CLOUD_BADGE, formatHourly, gpuSummary } from '@/components/cloud/meta';
import { CloudActionDialog, type CloudDialogAction } from '@/app/(dashboard)/cloud/_components/cloud-action-dialog';
import { useCloudNode, useCloudNodeAction, useDeleteCloudNode, useUpdateCloudNode } from '@/hooks/use-cloud';
import { cn } from '@/lib/utils';
import { formatBytes, formatDateTime } from '@/utils/format';
import { CLOUD_STATUS_LABELS, type CloudNode } from '@/types/cloud';

const IDLE_OPTIONS = [0, 30, 60, 120, 240, 1440];

/** Where the VM is in joining the fleet — the "watch node join" panel. */
function JoinPanel({ vm }: { vm: CloudNode }) {
  if (vm.node_id && vm.joined_at) {
    return (
      <div className="flex items-center gap-4 rounded-lg border border-success/30 bg-success/10 px-4 py-3">
        <CheckCircle2 className="size-5 shrink-0 text-success" />
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold">{vm.node_name ?? vm.name} joined the fleet</p>
          <p className="text-xs text-muted-foreground">
            Agent connected <RelativeTime value={vm.joined_at} /> · node {vm.node_status ?? 'unknown'} · {gpuSummary(vm)}
          </p>
        </div>
        <Link href={`/nodes/${vm.node_id}`} className={cn(buttonVariants({ size: 'sm' }), 'rounded-sm')}>
          Open node
          <ArrowRight className="size-4" />
        </Link>
      </div>
    );
  }
  if (['pending', 'provisioning', 'booting'].includes(vm.status)) {
    return (
      <div className="flex items-center gap-3 rounded-lg border border-border bg-muted/30 px-4 py-3">
        <span className="relative flex size-2.5">
          <span className="absolute inline-flex size-full animate-ping rounded-full bg-info opacity-60" />
          <span className="relative inline-flex size-2.5 rounded-full bg-info" />
        </span>
        <div>
          <p className="text-sm font-medium">{vm.status === 'booting' ? 'Waiting for the agent to join…' : 'Creating the VM…'}</p>
          <p className="text-xs text-muted-foreground">
            {vm.status === 'booting'
              ? 'cloud-init downloads the agent from this control plane, verifies its sha256 and joins with a one-time token. This page updates when the hub signs the node certificate.'
              : 'The provider accepted the request; the VM boots from the GPU image next.'}
          </p>
        </div>
      </div>
    );
  }
  return null;
}

export function CloudDetail() {
  const router = useRouter();
  const { id } = useParams<{ id: string }>();
  const { data: vm, isLoading, isError, refetch } = useCloudNode(id);
  const action = useCloudNodeAction();
  const update = useUpdateCloudNode();
  const remove = useDeleteCloudNode();
  const [dialog, setDialog] = useState<CloudDialogAction | null>(null);

  if (isLoading) return <ListPageSkeleton stats={0} rows={4} />;
  if (isError || !vm) {
    return (
      <PageContainer>
        <ErrorState title="VM not found" description="The record may have been removed, or the control plane is unreachable." onRetry={() => void refetch()} />
      </PageContainer>
    );
  }

  const err = vm.last_error as { code?: string; message?: string; provider_code?: string; stage?: string; at?: string } | null | undefined;
  const removable = vm.status === 'terminated' || (vm.status === 'error' && !vm.provider_instance_id);
  const monthly = vm.price_per_hour_usd ? vm.price_per_hour_usd * 730 : null;

  return (
    <PageContainer>
      <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div className="space-y-2">
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="text-2xl font-semibold tracking-tight sm:text-[28px]">{vm.name}</h1>
            <StatusBadge variant={CLOUD_BADGE[vm.status]}>{CLOUD_STATUS_LABELS[vm.status]}</StatusBadge>
          </div>
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-muted-foreground">
            <ProviderMark provider={vm.provider} />
            <span>·</span>
            <span className="font-mono text-xs">
              {vm.region}
              {vm.zone ? ` / ${vm.zone}` : ''}
            </span>
            <span>·</span>
            <Link href={`/clusters/${vm.cluster_id}`} className="hover:underline">
              {vm.cluster_name ?? 'cluster'}
            </Link>
            <span>·</span>
            <span className="text-xs">
              polled <RelativeTime value={vm.last_polled_at} />
            </span>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="outline" size="sm" className="rounded-sm" onClick={() => router.push('/cloud')}>
            <ArrowLeft className="size-4" />
            Cloud VMs
          </Button>
          <Button variant="outline" size="sm" className="rounded-sm" disabled={action.isPending || vm.status === 'terminated'} onClick={() => action.mutate({ id: vm.id, action: 'refresh' })} title="Poll the provider and advance the lifecycle now">
            <RefreshCw className={cn('size-4', action.isPending && action.variables?.action === 'refresh' && 'animate-spin')} />
            Poll now
          </Button>
          {vm.status === 'stopped' && (
            <Button size="sm" className="rounded-sm" onClick={() => action.mutate({ id: vm.id, action: 'start' })}>
              <Play className="size-4" />
              Start
            </Button>
          )}
          {['running', 'booting', 'error'].includes(vm.status) && (
            <Button variant="outline" size="sm" className="rounded-sm" onClick={() => setDialog('stop')}>
              <Square className="size-4" />
              Stop
            </Button>
          )}
          {removable ? (
            <Button variant="ghost" size="sm" className="rounded-sm text-muted-foreground hover:text-destructive" onClick={() => remove.mutate(vm.id, { onSuccess: () => router.push('/cloud') })}>
              <Trash2 className="size-4" />
              Remove record
            </Button>
          ) : (
            <Button variant="destructive" size="sm" className="rounded-sm" disabled={vm.status === 'terminating'} onClick={() => setDialog('terminate')}>
              <Power className="size-4" />
              Terminate
            </Button>
          )}
        </div>
      </div>

      <Card className="rounded-md shadow-premium">
        <CardContent className="space-y-4">
          <CloudLifecycle vm={vm} />
          <JoinPanel vm={vm} />
          {vm.status_message && vm.status !== 'error' && <p className="text-center text-xs text-muted-foreground">{vm.status_message}</p>}
          {vm.status === 'error' && (
            <div className="flex items-start gap-2.5 rounded-md bg-destructive/10 p-3 text-sm text-destructive">
              <AlertTriangle className="mt-0.5 size-4 shrink-0" />
              <div className="min-w-0 space-y-1">
                <p className="font-medium">
                  {err?.code ?? 'Provider error'}
                  {err?.provider_code ? <span className="ml-2 font-mono text-xs font-normal opacity-80">{err.provider_code}</span> : null}
                  {err?.stage ? <span className="ml-2 text-xs font-normal opacity-80">at {err.stage}</span> : null}
                </p>
                <p className="text-xs">{vm.status_message ?? err?.message}</p>
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      <div className="grid items-start gap-6 lg:grid-cols-3">
        <Card className="rounded-md shadow-premium lg:col-span-2">
          <CardHeader>
            <CardTitle>Instance</CardTitle>
            <CardDescription>What was requested and what the provider reports.</CardDescription>
          </CardHeader>
          <CardContent>
            <DetailList
              columns={2}
              items={[
                { label: 'Instance type', value: vm.instance_type, mono: true },
                { label: 'GPUs', value: gpuSummary(vm) },
                { label: 'vCPU · memory', value: `${vm.vcpus ?? '—'} · ${vm.memory_bytes ? formatBytes(vm.memory_bytes) : '—'}` },
                { label: 'Disk', value: `${vm.disk_gb} GB` },
                { label: 'Image', value: vm.image ?? 'provider GPU image (default)', mono: !!vm.image },
                { label: 'Provider state', value: vm.provider_state, mono: true },
                {
                  label: 'Provider id',
                  value: vm.provider_instance_id ? (
                    <span className="inline-flex max-w-full items-center gap-1">
                      <span className="truncate font-mono text-xs" title={vm.provider_instance_id}>
                        {vm.provider_instance_id}
                      </span>
                      <CopyButton value={vm.provider_instance_id} successText="Provider id copied" />
                    </span>
                  ) : null,
                  wide: true,
                },
                { label: 'Public IP', value: vm.public_ip, mono: true },
                { label: 'Private IP', value: vm.private_ip, mono: true },
                { label: 'Launched', value: vm.launched_at ? formatDateTime(vm.launched_at) : null },
                { label: 'Joined', value: vm.joined_at ? formatDateTime(vm.joined_at) : null },
                { label: 'Node labels', value: <LabelChips labels={vm.labels} max={8} emptyText="none" />, wide: true },
                { label: 'Provider tags', value: <LabelChips labels={vm.tags} max={8} emptyText="none" />, wide: true },
              ]}
            />
          </CardContent>
        </Card>

        <div className="space-y-6">
          <Card className="rounded-md shadow-premium">
            <CardHeader>
              <CardTitle>Cost</CardTitle>
              <CardDescription>{vm.price_source ? `List price from ${vm.price_source}.` : 'The provider publishes no list price for this type.'}</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="flex items-baseline justify-between">
                <span className="font-mono text-2xl font-semibold tabular-nums">{formatHourly(vm.price_per_hour_usd)}</span>
                <span className="text-xs text-muted-foreground">{monthly ? `≈ $${Math.round(monthly).toLocaleString()} / month` : ''}</span>
              </div>
              {vm.price_plan_suggestion && (
                <div className="space-y-2 rounded-md border border-border bg-muted/30 p-3 text-xs">
                  <p className="font-medium">Price plan suggestion</p>
                  <p className="text-muted-foreground">
                    <span className="text-foreground">{vm.price_plan_suggestion.name}</span> · ${vm.price_plan_suggestion.usd_per_gpu_minute?.toFixed(5) ?? '—'} per GPU-minute
                  </p>
                  <Button size="xs" variant="outline" disabled={!vm.node_id || action.isPending} onClick={() => action.mutate({ id: vm.id, action: 'apply-price-plan' })} title={vm.node_id ? undefined : 'Available once the node joined'}>
                    <Receipt className="size-3" />
                    Bill the node at this price
                  </Button>
                </div>
              )}
            </CardContent>
          </Card>

          <Card className="rounded-md shadow-premium">
            <CardHeader>
              <CardTitle>Idle auto-terminate</CardTitle>
              <CardDescription>{vm.idle_since ? <>Idle since <RelativeTime value={vm.idle_since} /> — no model instance on the node.</> : 'Terminate (after drain) when the node runs no model instance this long.'}</CardDescription>
            </CardHeader>
            <CardContent>
              <Select
                value={String(vm.auto_terminate_idle_minutes ?? 0)}
                onValueChange={(x) => update.mutate({ id: vm.id, body: { auto_terminate_idle_minutes: Number(x) || null } })}
                disabled={update.isPending || vm.status === 'terminated'}
              >
                <SelectTrigger className="w-full">
                  <SelectValue>{(x: unknown) => (Number(x) ? `After ${Number(x) >= 60 ? `${Number(x) / 60} h` : `${x} min`} idle` : 'Never')}</SelectValue>
                </SelectTrigger>
                <SelectContent>
                  {IDLE_OPTIONS.map((m) => (
                    <SelectItem key={m} value={String(m)}>
                      {m ? `After ${m >= 60 ? `${m / 60} h` : `${m} min`} idle` : 'Never'}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </CardContent>
          </Card>
        </div>
      </div>

      {dialog && <CloudActionDialog vm={vm} action={dialog} open onOpenChange={(o) => !o && setDialog(null)} />}
    </PageContainer>
  );
}
