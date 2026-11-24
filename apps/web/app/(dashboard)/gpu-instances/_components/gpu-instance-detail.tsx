'use client';

import { useState } from 'react';
import Link from 'next/link';
import { useParams, useRouter } from 'next/navigation';
import { AlertTriangle, ArrowLeft, Infinity as InfinityIcon, KeyRound, Play, Plus, Square, Trash2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { PageContainer } from '@/components/shared/PageContainer';
import { ListPageSkeleton } from '@/components/shared/ListPageSkeleton';
import { ErrorState } from '@/components/shared/ErrorState';
import { StatusBadge } from '@/components/shared/StatusBadge';
import { DetailList } from '@/components/shared/detail-list';
import { LabelChips } from '@/components/shared/label-chips';
import { RelativeTime } from '@/components/shared/relative-time';
import { ConfirmationDialog } from '@/components/shared/ConfirmationDialog';
import { DeleteConfirmationDialog } from '@/components/shared/DeleteConfirmationDialog';
import { ExpiryCountdown } from '@/components/fleet/expiry-countdown';
import { SshTerminal } from '@/components/gpu-instances/ssh-terminal';
import { InstanceLifecycle } from '@/components/gpu-instances/lifecycle';
import { GPU_INSTANCE_BADGE, IDLE_OPTIONS, extendedTtl, formatSpan, gpuHours, gpuShape } from '@/components/gpu-instances/meta';
import { useDeleteGpuInstance, useGpuInstance, useStartGpuInstance, useStopGpuInstance, useUpdateGpuInstance } from '@/hooks/use-gpu-instances';
import { sshKeyTypeLabel } from '@/lib/ssh-keys';
import { formatBytes, formatDateTime } from '@/utils/format';
import { GPU_INSTANCE_STATE_LABELS, STOP_REASON_LABELS, type GpuInstance } from '@/types/gpu-instances';

function TtlCard({ instance }: { instance: GpuInstance }) {
  const update = useUpdateGpuInstance();
  const live = instance.state !== 'stopped' && instance.state !== 'error';
  const extend = (hours: number) =>
    update.mutate({ id: instance.id, body: { ttl_seconds: extendedTtl(instance.expires_at, hours) }, successText: `Stops ${hours} h later` });
  const ttlStart = instance.expires_at && instance.ttl_seconds ? new Date(Date.parse(instance.expires_at) - instance.ttl_seconds * 1000).toISOString() : null;

  return (
    <Card className="rounded-md shadow-premium">
      <CardHeader>
        <CardTitle>Time to live</CardTitle>
        <CardDescription>{instance.ttl_seconds ? `Stops ${formatSpan(instance.ttl_seconds)} after it is ready or last extended.` : 'No TTL — it runs until stopped.'}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex items-center gap-4">
          {instance.expires_at && ttlStart && live ? (
            <ExpiryCountdown createdAt={ttlStart} expiresAt={instance.expires_at} size={104} />
          ) : (
            <div className="flex size-[104px] shrink-0 items-center justify-center rounded-full border-[6px] border-muted">
              {instance.ttl_seconds && live ? <span className="text-[11px] text-muted-foreground">on ready</span> : <InfinityIcon className="size-6 text-muted-foreground" />}
            </div>
          )}
          <div className="min-w-0 space-y-1 text-[13px]">
            {instance.expires_at && live ? (
              <>
                <p className="font-medium">Stops at {new Date(instance.expires_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</p>
                <p className="text-xs text-muted-foreground">{formatDateTime(instance.expires_at)}</p>
              </>
            ) : instance.state === 'stopped' ? (
              <p className="text-muted-foreground">{instance.stop_reason ? (STOP_REASON_LABELS[instance.stop_reason] ?? instance.stop_reason) : 'Stopped'}{instance.stopped_at ? ` · ${formatDateTime(instance.stopped_at)}` : ''}</p>
            ) : (
              <p className="text-muted-foreground">{instance.ttl_seconds ? 'The countdown starts once sshd is up.' : 'Remember to stop it — it is billed while it runs.'}</p>
            )}
          </div>
        </div>
        {live && (
          <div className="flex flex-wrap gap-2">
            {[1, 4, 24].map((h) => (
              <Button key={h} variant="outline" size="xs" disabled={update.isPending} onClick={() => extend(h)}>
                <Plus className="size-3" />
                {h === 24 ? '1 day' : `${h} h`}
              </Button>
            ))}
            {instance.ttl_seconds ? (
              <Button variant="ghost" size="xs" disabled={update.isPending} onClick={() => update.mutate({ id: instance.id, body: { ttl_seconds: 0 }, successText: 'TTL removed' })}>
                <InfinityIcon className="size-3" />
                Remove TTL
              </Button>
            ) : (
              <Button variant="ghost" size="xs" disabled={update.isPending} onClick={() => update.mutate({ id: instance.id, body: { ttl_seconds: 8 * 3600 }, successText: 'Stops in 8 h' })}>
                Set 8 h TTL
              </Button>
            )}
          </div>
        )}
        <div className="space-y-1.5 border-t border-border pt-3">
          <p className="text-xs font-medium text-muted-foreground">Idle stop</p>
          <Select
            value={String(instance.idle_timeout_seconds ?? 0)}
            onValueChange={(x) => update.mutate({ id: instance.id, body: { idle_timeout_seconds: Number(x) }, successText: Number(x) ? 'Idle stop updated' : 'Idle stop turned off' })}
            disabled={update.isPending}
          >
            <SelectTrigger className="w-full">
              <SelectValue>{(x: unknown) => IDLE_OPTIONS.find((o) => String(o.value) === x)?.label ?? `${formatSpan(Number(x))} without an SSH session`}</SelectValue>
            </SelectTrigger>
            <SelectContent>
              {IDLE_OPTIONS.map((o) => (
                <SelectItem key={o.value} value={String(o.value)}>
                  {o.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </CardContent>
    </Card>
  );
}

export function GpuInstanceDetail() {
  const router = useRouter();
  const { id } = useParams<{ id: string }>();
  const { data: instance, isLoading, isError, refetch } = useGpuInstance(id);
  const stop = useStopGpuInstance();
  const start = useStartGpuInstance();
  const remove = useDeleteGpuInstance();
  const [confirmStop, setConfirmStop] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);

  if (isLoading) return <ListPageSkeleton stats={0} rows={4} />;
  if (isError || !instance) {
    return (
      <PageContainer>
        <ErrorState title="Instance not found" description="It may have been deleted, or the control plane is unreachable." onRetry={() => void refetch()} />
      </PageContainer>
    );
  }

  const i = instance;
  const stoppable = i.state !== 'stopped' && i.state !== 'error' && i.state !== 'stopping';
  const limits = i.limits ?? {};

  return (
    <PageContainer>
      <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div className="space-y-2">
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="text-2xl font-semibold tracking-tight sm:text-[28px]">{i.name}</h1>
            <StatusBadge variant={GPU_INSTANCE_BADGE[i.state]}>{GPU_INSTANCE_STATE_LABELS[i.state]}</StatusBadge>
          </div>
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-muted-foreground">
            <span>{gpuShape(i)}</span>
            {i.node_id && (
              <>
                <span>·</span>
                <Link href={`/nodes/${i.node_id}`} className="hover:underline">
                  {i.node_name ?? 'node'}
                </Link>
              </>
            )}
            <span>·</span>
            <span className="text-xs">
              requested <RelativeTime value={i.created_at} />
            </span>
          </div>
          {i.description && <p className="max-w-2xl text-sm text-muted-foreground">{i.description}</p>}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="outline" size="sm" className="rounded-sm" onClick={() => router.push('/gpu-instances')}>
            <ArrowLeft className="size-4" />
            All instances
          </Button>
          {i.state === 'stopped' || i.state === 'error' ? (
            <Button size="sm" className="rounded-sm" disabled={start.isPending} onClick={() => start.mutate(i.id)}>
              <Play className="size-4" />
              Start again
            </Button>
          ) : (
            <Button variant="destructive" size="sm" className="rounded-sm" disabled={!stoppable} onClick={() => setConfirmStop(true)}>
              <Square className="size-4" />
              Stop
            </Button>
          )}
          <Button variant="ghost" size="sm" className="rounded-sm text-muted-foreground hover:text-destructive" onClick={() => setConfirmDelete(true)}>
            <Trash2 className="size-4" />
            Delete
          </Button>
        </div>
      </div>

      <Card className="rounded-md shadow-premium">
        <CardContent className="space-y-3">
          <InstanceLifecycle instance={i} />
          {i.state === 'error' && i.state_message && (
            <p className="flex items-start gap-2 rounded-md bg-destructive/10 p-3 text-sm text-destructive">
              <AlertTriangle className="mt-0.5 size-4 shrink-0" />
              {i.state_message}
            </p>
          )}
          {i.state !== 'error' && i.state_message && <p className="text-center text-xs text-muted-foreground">{i.state_message}</p>}
          {i.warnings.map((w) => (
            <p key={w} className="flex items-start gap-2 rounded-md bg-warning/10 p-3 text-xs text-warning-dark dark:text-warning-light">
              <AlertTriangle className="mt-0.5 size-3.5 shrink-0" />
              {w}
            </p>
          ))}
        </CardContent>
      </Card>

      <div className="grid items-start gap-6 lg:grid-cols-3">
        <div className="space-y-6 lg:col-span-2">
          <Card className="rounded-md shadow-premium">
            <CardHeader>
              <CardTitle>Connect</CardTitle>
              <CardDescription>
                The agent forwards a host port on the node&apos;s private address to sshd in the container
                {i.allowed_source_cidrs.length ? `, from ${i.allowed_source_cidrs.join(', ')} only` : ''}.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <SshTerminal instance={i} />
            </CardContent>
          </Card>

          <Card className="rounded-md shadow-premium">
            <CardHeader>
              <CardTitle>Container</CardTitle>
              <CardDescription>What the agent was asked to run.</CardDescription>
            </CardHeader>
            <CardContent>
              <DetailList
                columns={2}
                items={[
                  { label: 'Image', value: i.image, mono: true, wide: true },
                  { label: 'GPU', value: `${gpuShape(i)}${i.device_vendor ? ` · ${i.device_vendor}` : ''}` },
                  {
                    label: 'Devices',
                    value: i.device_ids.length ? (
                      <span className="flex flex-wrap gap-1.5">
                        {i.device_ids.map((d) => (
                          <Link key={d} href={`/gpus/${d}`} className="font-mono text-xs hover:underline">
                            {d.slice(0, 8)}
                          </Link>
                        ))}
                      </span>
                    ) : null,
                  },
                  { label: 'GPU attached', value: i.state === 'running' ? (i.gpu_attached ? 'yes' : 'no — see the warning above') : null },
                  { label: 'SSH user', value: i.ssh_user, mono: true },
                  { label: 'sshd port (inside)', value: String(i.container_port), mono: true },
                  { label: 'Container', value: i.container_id, mono: true },
                  { label: 'CPU', value: limits.cpu_cores ? `${limits.cpu_cores} cores` : 'no limit' },
                  { label: 'Memory', value: limits.memory_bytes ? formatBytes(limits.memory_bytes) : 'no limit' },
                  { label: '/dev/shm', value: limits.shm_bytes ? formatBytes(limits.shm_bytes) : 'default' },
                  {
                    label: 'Volumes',
                    value: i.volumes.length ? i.volumes.map((vol) => `${vol.source ?? vol.type ?? 'volume'} → ${vol.target}${vol.keep ? ' (kept)' : ''}${vol.read_only ? ' ro' : ''}`).join(', ') : null,
                    mono: true,
                    wide: true,
                  },
                  { label: 'Environment', value: Object.keys(i.env).length ? Object.entries(i.env).map(([k, val]) => `${k}=${val}`).join('  ') : null, mono: true, wide: true },
                  { label: 'Labels', value: <LabelChips labels={i.labels} max={8} emptyText="none" />, wide: true },
                ]}
              />
            </CardContent>
          </Card>
        </div>

        <div className="space-y-6">
          <TtlCard instance={i} />

          <Card className="rounded-md shadow-premium">
            <CardHeader>
              <CardTitle>Usage</CardTitle>
              <CardDescription>Metered into billing as GPU minutes.</CardDescription>
            </CardHeader>
            <CardContent>
              <DetailList
                items={[
                  { label: 'GPU hours', value: <span className="font-mono tabular-nums">{gpuHours(i.gpu_seconds_total)}</span> },
                  { label: 'SSH sessions now', value: String(i.active_connections) },
                  { label: 'Sessions total', value: String(i.connections_total) },
                  { label: 'Last session', value: i.last_connection_at ? <RelativeTime value={i.last_connection_at} /> : 'never' },
                  { label: 'Running since', value: i.running_at && i.state === 'running' ? formatDateTime(i.running_at) : null },
                ]}
              />
            </CardContent>
          </Card>

          <Card className="rounded-md shadow-premium">
            <CardHeader>
              <CardTitle>Authorized keys</CardTitle>
              <CardDescription>Changeable while stopped; used on the next start.</CardDescription>
            </CardHeader>
            <CardContent>
              <ul className="space-y-2">
                {i.authorized_keys.map((k) => (
                  <li key={k.fingerprint} className="flex items-start gap-2.5 rounded-md border border-border px-3 py-2">
                    <KeyRound className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" />
                    <div className="min-w-0">
                      <p className="text-xs font-medium">
                        {sshKeyTypeLabel(k.type)}
                        {k.comment ? <span className="text-muted-foreground"> · {k.comment}</span> : null}
                      </p>
                      <p className="truncate font-mono text-[11px] text-muted-foreground" title={k.fingerprint}>
                        {k.fingerprint}
                      </p>
                    </div>
                  </li>
                ))}
              </ul>
            </CardContent>
          </Card>
        </div>
      </div>

      <ConfirmationDialog
        open={confirmStop}
        onOpenChange={setConfirmStop}
        title={`Stop ${i.name}?`}
        description={`Open SSH sessions are closed, the container is removed and its GPU is released.${i.volumes.some((vol) => vol.keep) ? ' Kept volumes survive.' : ' Anything outside a kept volume is lost.'}`}
        confirmText="Stop instance"
        variant="destructive"
        isLoading={stop.isPending}
        onConfirm={() => stop.mutate({ id: i.id }, { onSuccess: () => setConfirmStop(false) })}
      />
      <DeleteConfirmationDialog
        open={confirmDelete}
        onOpenChange={setConfirmDelete}
        itemName={i.name}
        resourceType="instance"
        warningMessage={stoppable ? 'It is still running: it will be force-stopped first.' : undefined}
        isDeleting={remove.isPending}
        onConfirm={() => remove.mutate({ id: i.id, force: stoppable || i.state === 'stopping' }, { onSuccess: () => router.push('/gpu-instances') })}
      />
    </PageContainer>
  );
}
