'use client';

import { useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { Clock, Copy, Cpu, ExternalLink, Hourglass, MoreVertical, Play, Plus, Square, SquareTerminal, Timer, Trash2 } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader } from '@/components/ui/card';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from '@/components/ui/dropdown-menu';
import { PageContainer } from '@/components/shared/PageContainer';
import { PageHeader } from '@/components/shared/PageHeader';
import { ListPageSkeleton } from '@/components/shared/ListPageSkeleton';
import { ErrorState } from '@/components/shared/ErrorState';
import { EmptyState } from '@/components/shared/EmptyState';
import { StatusBadge } from '@/components/shared/StatusBadge';
import { StatCard } from '@/components/shared/stat-card';
import { SearchInput } from '@/components/shared/search-input';
import { TablePagination } from '@/components/shared/table-pagination';
import { RelativeTime } from '@/components/shared/relative-time';
import { DeleteConfirmationDialog } from '@/components/shared/DeleteConfirmationDialog';
import { ConfirmationDialog } from '@/components/shared/ConfirmationDialog';
import { useNow } from '@/components/fleet/expiry-countdown';
import { GPU_INSTANCE_BADGE, extendedTtl, gpuHours, gpuShape, ttlLeft } from '@/components/gpu-instances/meta';
import { useDeleteGpuInstance, useGpuInstances, useStartGpuInstance, useStopGpuInstance, useUpdateGpuInstance } from '@/hooks/use-gpu-instances';
import { useUrlState } from '@/hooks/use-url-state';
import { useDebounce } from '@/hooks/use-debounce';
import { cn } from '@/lib/utils';
import { GPU_INSTANCE_STATES, GPU_INSTANCE_STATE_LABELS, STOP_REASON_LABELS, type GpuInstance, type GpuInstanceState } from '@/types/gpu-instances';

const DEFAULTS = { search: '', state: '', page: 1, pageSize: 25 };

async function copy(text: string, what: string) {
  try {
    await navigator.clipboard.writeText(text);
    toast.success(`${what} copied`);
  } catch {
    toast.error('Clipboard is not available in this browser');
  }
}

function TtlCell({ instance, now }: { instance: GpuInstance; now: number }) {
  if (instance.state === 'stopped') {
    return <span className="text-xs text-muted-foreground">{instance.stop_reason ? (STOP_REASON_LABELS[instance.stop_reason] ?? instance.stop_reason) : 'stopped'}</span>;
  }
  const left = ttlLeft(instance.expires_at, now);
  if (!left) {
    return <span className="text-xs text-muted-foreground">{instance.ttl_seconds ? 'starts on ready' : 'no TTL'}</span>;
  }
  const low = left.ms < 3_600_000;
  return (
    <span className={cn('inline-flex items-center gap-1.5 font-mono text-xs tabular-nums', left.ms <= 0 ? 'text-error-dark dark:text-error-light' : low ? 'text-warning-dark dark:text-warning-light' : 'text-foreground')} title={`Stops at ${new Date(instance.expires_at ?? '').toLocaleString()}`}>
      <Hourglass className="size-3" aria-hidden />
      {left.label}
    </span>
  );
}

export function GpuInstancesList() {
  const router = useRouter();
  const { params, setParams } = useUrlState(DEFAULTS);
  const [search, setSearch] = useState(params.search);
  const debouncedSearch = useDebounce(search, 300);
  const [stopping, setStopping] = useState<GpuInstance | null>(null);
  const [deleting, setDeleting] = useState<GpuInstance | null>(null);
  const now = useNow(15_000);

  const urlSearch = params.search;
  useEffect(() => {
    if (debouncedSearch !== urlSearch) setParams({ search: debouncedSearch, page: 1 });
  }, [debouncedSearch, urlSearch, setParams]);

  const query = useMemo(
    () => ({ search: debouncedSearch, state: params.state as GpuInstanceState | '', page: params.page, page_size: params.pageSize }),
    [debouncedSearch, params.state, params.page, params.pageSize],
  );
  const { data, isLoading, isError, refetch } = useGpuInstances(query);
  const stop = useStopGpuInstance();
  const start = useStartGpuInstance();
  const update = useUpdateGpuInstance();
  const remove = useDeleteGpuInstance();

  const items = useMemo(() => data?.items ?? [], [data]);
  const stats = useMemo(() => {
    const running = items.filter((i) => i.state === 'running');
    return {
      running: running.length,
      gpus: running.reduce((sum, i) => sum + (i.gpu_count === 0 ? 0 : i.slice_vram_bytes ? 0 : i.device_ids.length || i.gpu_count), 0),
      slices: running.filter((i) => !!i.slice_vram_bytes).length,
      expiring: running.filter((i) => i.expires_at && Date.parse(i.expires_at) - now < 3_600_000).length,
      gpuSeconds: items.reduce((sum, i) => sum + i.gpu_seconds_total, 0),
    };
  }, [items, now]);

  if (isLoading && !data) return <ListPageSkeleton />;
  const filtered = !!debouncedSearch || !!params.state;

  function extend(i: GpuInstance, hours: number) {
    update.mutate({ id: i.id, body: { ttl_seconds: extendedTtl(i.expires_at, hours) }, successText: `TTL extended by ${hours} h` });
  }

  return (
    <PageContainer>
      <PageHeader
        title="SSH GPU instances"
        description="Containers with sshd and a GPU (or a slice of one) attached, placed by the scheduler and stopped by their TTL."
        actions={
          <Button size="sm" className="rounded-sm" onClick={() => router.push('/gpu-instances/new')}>
            <Plus className="size-4" />
            Request instance
          </Button>
        }
      />

      <div className="grid gap-4 md:grid-cols-4">
        <StatCard label="Running" value={stats.running} icon={SquareTerminal} footnote={`${items.length} listed`} />
        <StatCard label="GPUs held" value={stats.gpus} icon={Cpu} footnote={`plus ${stats.slices} slice${stats.slices === 1 ? '' : 's'}`} />
        <StatCard label="Stopping within 1 h" value={stats.expiring} icon={Timer} footnote="TTL about to run out" />
        <StatCard label="GPU hours" value={gpuHours(stats.gpuSeconds)} icon={Clock} footnote="Metered to billing as GPU minutes" />
      </div>

      {isError ? (
        <ErrorState title="Could not load SSH instances" description="The control plane did not answer. Retry in a moment." onRetry={() => void refetch()} />
      ) : (
        <Card className="rounded-md shadow-premium">
          <CardHeader>
            <div className="flex flex-wrap items-center gap-3">
              <SearchInput value={search} onChange={setSearch} placeholder="Search name, image…" />
              <Select value={params.state || 'all'} onValueChange={(v) => setParams({ state: v === 'all' ? '' : String(v), page: 1 })}>
                <SelectTrigger className="w-44">
                  <SelectValue>{(v: unknown) => (v && v !== 'all' ? GPU_INSTANCE_STATE_LABELS[v as GpuInstanceState] : 'All states')}</SelectValue>
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">All states</SelectItem>
                  {GPU_INSTANCE_STATES.map((s) => (
                    <SelectItem key={s} value={s}>
                      {GPU_INSTANCE_STATE_LABELS[s]}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </CardHeader>
          <CardContent>
            {items.length === 0 ? (
              <EmptyState
                icon={SquareTerminal}
                title={filtered ? 'No instances match' : 'No SSH instances yet'}
                description={
                  filtered
                    ? 'Try a different search or clear the state filter.'
                    : 'Request a container with a GPU or a slice of one, paste your public key, and SSH in when it is ready.'
                }
                action={
                  !filtered && (
                    <Button size="sm" className="rounded-sm" onClick={() => router.push('/gpu-instances/new')}>
                      <Plus className="size-4" />
                      Request instance
                    </Button>
                  )
                }
              />
            ) : (
              <>
                <div className="overflow-x-auto">
                  <Table>
                    <TableHeader className="sticky top-0 z-10 bg-card">
                      <TableRow>
                        <TableHead>Instance</TableHead>
                        <TableHead>State</TableHead>
                        <TableHead>GPU</TableHead>
                        <TableHead>Connect</TableHead>
                        <TableHead>TTL</TableHead>
                        <TableHead className="text-right">GPU h</TableHead>
                        <TableHead>Requested</TableHead>
                        <TableHead className="sticky right-0 bg-card text-right shadow-[-4px_0_6px_-2px_rgb(var(--border))]">Actions</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {items.map((i) => (
                        <TableRow key={i.id} className="cursor-pointer" onClick={() => router.push(`/gpu-instances/${i.id}`)}>
                          <TableCell>
                            <div className="flex min-w-0 flex-col gap-0.5">
                              <Link href={`/gpu-instances/${i.id}`} className="font-semibold hover:underline" onClick={(e) => e.stopPropagation()}>
                                {i.name}
                              </Link>
                              <span className="max-w-64 truncate font-mono text-[11px] text-muted-foreground" title={i.image}>
                                {i.image}
                              </span>
                            </div>
                          </TableCell>
                          <TableCell>
                            <div className="flex flex-col items-start gap-1">
                              <StatusBadge variant={GPU_INSTANCE_BADGE[i.state]}>{GPU_INSTANCE_STATE_LABELS[i.state]}</StatusBadge>
                              {i.state === 'error' && i.state_message && (
                                <span className="line-clamp-1 max-w-56 text-[11px] text-error-dark dark:text-error-light" title={i.state_message}>
                                  {i.state_message}
                                </span>
                              )}
                              {i.warnings.length > 0 && i.state === 'running' && (
                                <span className="line-clamp-1 max-w-56 text-[11px] text-warning-dark dark:text-warning-light" title={i.warnings.join('\n')}>
                                  {i.warnings[0]}
                                </span>
                              )}
                            </div>
                          </TableCell>
                          <TableCell>
                            <div className="flex flex-col text-xs">
                              <span className="font-medium">{gpuShape(i)}</span>
                              <span className="text-muted-foreground">{i.node_name ?? (i.state === 'error' ? 'not placed' : 'placing…')}</span>
                            </div>
                          </TableCell>
                          <TableCell onClick={(e) => e.stopPropagation()}>
                            {i.connection.ready && i.connection.ssh_command ? (
                              <button
                                type="button"
                                onClick={() => void copy(i.connection.ssh_command ?? '', 'SSH command')}
                                className="group inline-flex max-w-60 items-center gap-1.5 rounded-md border border-border bg-muted/40 px-2 py-1 font-mono text-[11px] hover:border-foreground/20 hover:bg-muted"
                                title="Copy SSH command"
                              >
                                <span className="truncate">{i.connection.ssh_command}</span>
                                <Copy className="size-3 shrink-0 text-muted-foreground group-hover:text-foreground" aria-hidden />
                              </button>
                            ) : (
                              <span className="text-xs text-muted-foreground">—</span>
                            )}
                          </TableCell>
                          <TableCell>
                            <TtlCell instance={i} now={now} />
                          </TableCell>
                          <TableCell className="text-right font-mono text-xs tabular-nums">{gpuHours(i.gpu_seconds_total)}</TableCell>
                          <TableCell className="text-xs text-muted-foreground">
                            <RelativeTime value={i.created_at} />
                          </TableCell>
                          <TableCell className="sticky right-0 bg-card text-right shadow-[-4px_0_6px_-2px_rgb(var(--border))]" onClick={(e) => e.stopPropagation()}>
                            <DropdownMenu>
                              <DropdownMenuTrigger className="inline-flex h-8 w-8 cursor-pointer items-center justify-center rounded-md hover:bg-accent" aria-label={`Actions for ${i.name}`}>
                                <MoreVertical className="h-4 w-4" />
                              </DropdownMenuTrigger>
                              <DropdownMenuContent align="end" className="w-52">
                                <DropdownMenuItem onClick={() => router.push(`/gpu-instances/${i.id}`)}>
                                  <ExternalLink className="mr-2 h-4 w-4" />
                                  Open
                                </DropdownMenuItem>
                                {i.connection.ssh_command && (
                                  <DropdownMenuItem onClick={() => void copy(i.connection.ssh_command ?? '', 'SSH command')}>
                                    <Copy className="mr-2 h-4 w-4" />
                                    Copy SSH command
                                  </DropdownMenuItem>
                                )}
                                {(i.state === 'running' || i.state === 'starting') && (
                                  <>
                                    <DropdownMenuItem onClick={() => extend(i, 1)}>
                                      <Timer className="mr-2 h-4 w-4" />
                                      Extend TTL by 1 h
                                    </DropdownMenuItem>
                                    <DropdownMenuItem onClick={() => extend(i, 4)}>
                                      <Timer className="mr-2 h-4 w-4" />
                                      Extend TTL by 4 h
                                    </DropdownMenuItem>
                                  </>
                                )}
                                <DropdownMenuSeparator />
                                {i.state === 'stopped' || i.state === 'error' ? (
                                  <DropdownMenuItem onClick={() => start.mutate(i.id)}>
                                    <Play className="mr-2 h-4 w-4" />
                                    Start again
                                  </DropdownMenuItem>
                                ) : (
                                  <DropdownMenuItem disabled={i.state === 'stopping'} onClick={() => setStopping(i)}>
                                    <Square className="mr-2 h-4 w-4" />
                                    Stop
                                  </DropdownMenuItem>
                                )}
                                <DropdownMenuItem variant="destructive" onClick={() => setDeleting(i)}>
                                  <Trash2 className="mr-2 h-4 w-4" />
                                  Delete
                                </DropdownMenuItem>
                              </DropdownMenuContent>
                            </DropdownMenu>
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
                <TablePagination
                  page={params.page}
                  pageSize={params.pageSize}
                  total={data?.meta.total ?? 0}
                  noun="instances"
                  onPageChange={(page) => setParams({ page })}
                  onPageSizeChange={(pageSize) => setParams({ pageSize, page: 1 })}
                />
              </>
            )}
          </CardContent>
        </Card>
      )}

      <ConfirmationDialog
        open={!!stopping}
        onOpenChange={(open) => !open && setStopping(null)}
        title={`Stop ${stopping?.name ?? 'instance'}?`}
        description={`Open SSH sessions are closed, the container is removed and its GPU is released.${stopping?.volumes.some((v) => v.keep) ? ' Volumes marked "keep" survive.' : ' Data outside kept volumes is lost.'}`}
        confirmText="Stop instance"
        variant="destructive"
        isLoading={stop.isPending}
        onConfirm={() => {
          if (stopping) stop.mutate({ id: stopping.id }, { onSuccess: () => setStopping(null) });
        }}
      />

      <DeleteConfirmationDialog
        open={!!deleting}
        onOpenChange={(open) => !open && setDeleting(null)}
        itemName={deleting?.name ?? ''}
        resourceType="instance"
        warningMessage={deleting && deleting.state !== 'stopped' && deleting.state !== 'error' ? 'It is still running: it will be force-stopped first.' : undefined}
        isDeleting={remove.isPending}
        onConfirm={() => {
          if (deleting) remove.mutate({ id: deleting.id, force: deleting.state !== 'stopped' && deleting.state !== 'error' }, { onSuccess: () => setDeleting(null) });
        }}
      />
    </PageContainer>
  );
}
