'use client';

import { useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { Cloud, CloudUpload, Coins, Cpu, ExternalLink, Loader2, MoreVertical, Play, Power, Square, Trash2 } from 'lucide-react';
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
import { ProviderMark } from '@/components/cloud/provider-mark';
import { CLOUD_BADGE, formatHourly, gpuSummary } from '@/components/cloud/meta';
import { CloudActionDialog, type CloudDialogAction } from '@/app/(dashboard)/cloud/_components/cloud-action-dialog';
import { useCloudNodeAction, useCloudNodes, useCloudProviders, useDeleteCloudNode } from '@/hooks/use-cloud';
import { useUrlState } from '@/hooks/use-url-state';
import { useDebounce } from '@/hooks/use-debounce';
import { BILLED_STATES, CLOUD_PROVIDER_LABELS, CLOUD_STATUS_LABELS, type CloudNode, type CloudNodeStatus, type CloudProviderId } from '@/types/cloud';

const DEFAULTS = { search: '', provider: '', status: '', cluster_id: '', page: 1, pageSize: 25 };

export function CloudList() {
  const router = useRouter();
  const { params, setParams } = useUrlState(DEFAULTS);
  const [search, setSearch] = useState(params.search);
  const debouncedSearch = useDebounce(search, 300);
  const [dialog, setDialog] = useState<{ vm: CloudNode; action: CloudDialogAction } | null>(null);
  const providers = useCloudProviders();
  const action = useCloudNodeAction();
  const remove = useDeleteCloudNode();

  const urlSearch = params.search;
  useEffect(() => {
    if (debouncedSearch !== urlSearch) setParams({ search: debouncedSearch, page: 1 });
  }, [debouncedSearch, urlSearch, setParams]);

  const query = useMemo(
    () => ({ search: debouncedSearch, provider: params.provider as CloudProviderId | '', status: params.status as CloudNodeStatus | '', cluster_id: params.cluster_id || undefined, page: params.page, page_size: params.pageSize }),
    [debouncedSearch, params.provider, params.status, params.cluster_id, params.page, params.pageSize],
  );
  const { data, isLoading, isError, refetch } = useCloudNodes(query);
  const all = useCloudNodes({ page_size: 500 });
  const vms = data?.items ?? [];
  const stats = useMemo(() => {
    const list = all.data?.items ?? [];
    const running = list.filter((v) => v.status === 'running');
    return {
      running: running.length,
      moving: list.filter((v) => ['pending', 'provisioning', 'booting', 'starting'].includes(v.status)).length,
      gpus: running.reduce((sum, v) => sum + v.gpu_count, 0),
      rate: list.filter((v) => BILLED_STATES.includes(v.status)).reduce((sum, v) => sum + (v.price_per_hour_usd ?? 0), 0),
      byProvider: list.reduce<Record<string, number>>((acc, v) => ({ ...acc, [v.provider]: (acc[v.provider] ?? 0) + (v.status === 'terminated' ? 0 : 1) }), {}),
    };
  }, [all.data]);

  if (isLoading && !data) return <ListPageSkeleton />;
  const filtered = !!debouncedSearch || !!params.provider || !!params.status || !!params.cluster_id;

  return (
    <PageContainer>
      <PageHeader
        title="Cloud VMs"
        description="GPU VMs on AWS, Azure, GCP and DigitalOcean. Each installs the agent from cloud-init and joins its cloud cluster; terminating drains the node first."
        actions={
          <Button size="sm" className="rounded-sm" onClick={() => router.push(`/cloud/new${params.cluster_id ? `?cluster_id=${params.cluster_id}` : ''}`)}>
            <CloudUpload className="size-4" />
            Provision VM
          </Button>
        }
      />

      <div className="grid gap-4 md:grid-cols-4">
        <StatCard label="Joined and running" value={stats.running} icon={Cloud} footnote={`${stats.moving} provisioning or booting`} />
        <StatCard label="Cloud GPUs" value={stats.gpus} icon={Cpu} footnote="On running VMs" />
        <StatCard label="Run rate" value={formatHourly(stats.rate)} icon={Coins} footnote="List price of VMs the provider bills" />
        <StatCard label="Providers" value={(providers.data ?? []).filter((p) => p.enabled).length} icon={Power} footnote={(providers.data ?? []).filter((p) => p.enabled).map((p) => CLOUD_PROVIDER_LABELS[p.id]).join(' · ') || '—'} />
      </div>

      {providers.data && (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          {providers.data.map((p) => (
            <button
              key={p.id}
              type="button"
              onClick={() => setParams({ provider: params.provider === p.id ? '' : p.id, page: 1 })}
              className={`group flex flex-col gap-1.5 rounded-md border bg-card p-3 text-left shadow-premium transition-colors hover:border-foreground/20 ${params.provider === p.id ? 'border-foreground/40 ring-1 ring-foreground/10' : 'border-border'}`}
              aria-pressed={params.provider === p.id}
            >
              <span className="flex items-center justify-between gap-2">
                <ProviderMark provider={p.id} />
                <span className="font-mono text-[11px] tabular-nums text-muted-foreground">{stats.byProvider[p.id] ?? 0} VM{(stats.byProvider[p.id] ?? 0) === 1 ? '' : 's'}</span>
              </span>
              <span className="line-clamp-1 text-[11px] text-muted-foreground" title={p.pricing}>
                {p.enabled ? p.pricing : 'Disabled (CLOUD_PROVIDERS_ENABLED)'}
              </span>
              {!p.verified_live && <span className="text-[10.5px] text-muted-foreground/80">Verified against recorded API responses</span>}
            </button>
          ))}
        </div>
      )}

      {isError ? (
        <ErrorState title="Could not load cloud VMs" description="The control plane did not answer. Retry in a moment." onRetry={() => void refetch()} />
      ) : (
        <Card className="rounded-md shadow-premium">
          <CardHeader>
            <div className="flex flex-wrap items-center gap-3">
              <SearchInput value={search} onChange={setSearch} placeholder="Search name, type, region…" />
              <Select value={params.status || 'all'} onValueChange={(v) => setParams({ status: v === 'all' ? '' : String(v), page: 1 })}>
                <SelectTrigger className="w-44">
                  <SelectValue>{(v: unknown) => (v && v !== 'all' ? CLOUD_STATUS_LABELS[v as CloudNodeStatus] : 'All statuses')}</SelectValue>
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">All statuses</SelectItem>
                  {(Object.keys(CLOUD_STATUS_LABELS) as CloudNodeStatus[]).map((s) => (
                    <SelectItem key={s} value={s}>
                      {CLOUD_STATUS_LABELS[s]}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {params.cluster_id && (
                <Button variant="ghost" size="sm" onClick={() => setParams({ cluster_id: '', page: 1 })}>
                  Clear cluster filter
                </Button>
              )}
            </div>
          </CardHeader>
          <CardContent>
            {vms.length === 0 ? (
              <EmptyState
                icon={Cloud}
                title={filtered ? 'No VMs match' : 'No cloud VMs yet'}
                description={filtered ? 'Try a different search or clear the filters.' : 'Provision a GPU VM: pick a cloud cluster, a region and an instance type — it joins the fleet by itself.'}
                action={
                  !filtered && (
                    <Button size="sm" className="rounded-sm" onClick={() => router.push('/cloud/new')}>
                      <CloudUpload className="size-4" />
                      Provision VM
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
                        <TableHead>VM</TableHead>
                        <TableHead>Where</TableHead>
                        <TableHead>Status</TableHead>
                        <TableHead>Node</TableHead>
                        <TableHead>GPUs</TableHead>
                        <TableHead className="text-right">List price</TableHead>
                        <TableHead>Created</TableHead>
                        <TableHead className="sticky right-0 bg-card text-right shadow-[-4px_0_6px_-2px_rgb(var(--border))]">Actions</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {vms.map((vm) => (
                        <TableRow key={vm.id} className={`cursor-pointer ${vm.status === 'terminated' ? 'opacity-60' : ''}`} onClick={() => router.push(`/cloud/${vm.id}`)}>
                          <TableCell>
                            <Link href={`/cloud/${vm.id}`} className="font-semibold hover:underline" onClick={(e) => e.stopPropagation()}>
                              {vm.name}
                            </Link>
                            <span className="block font-mono text-[11px] text-muted-foreground">{vm.instance_type}</span>
                          </TableCell>
                          <TableCell>
                            <ProviderMark provider={vm.provider} withLabel={false} />
                            <span className="ml-1.5 font-mono text-xs">{vm.region}</span>
                            <span className="block text-[11px] text-muted-foreground">{vm.cluster_name ?? ''}</span>
                          </TableCell>
                          <TableCell>
                            <div className="flex flex-col items-start gap-1">
                              <StatusBadge variant={CLOUD_BADGE[vm.status]}>
                                {['pending', 'provisioning', 'booting', 'draining', 'stopping', 'starting', 'terminating'].includes(vm.status) && <Loader2 className="mr-1 size-3 animate-spin" />}
                                {CLOUD_STATUS_LABELS[vm.status]}
                              </StatusBadge>
                              {vm.status === 'error' && (
                                <span className="line-clamp-1 max-w-56 font-mono text-[10.5px] text-error-dark dark:text-error-light" title={vm.status_message ?? undefined}>
                                  {String(vm.last_error?.code ?? 'error')}
                                </span>
                              )}
                            </div>
                          </TableCell>
                          <TableCell>
                            {vm.node_id ? (
                              <Link href={`/nodes/${vm.node_id}`} className="text-xs font-medium hover:underline" onClick={(e) => e.stopPropagation()}>
                                {vm.node_name ?? 'node'}
                                <span className="ml-1 font-normal text-muted-foreground">· {vm.node_status ?? 'unknown'}</span>
                              </Link>
                            ) : (
                              <span className="text-xs text-muted-foreground">{vm.status === 'booting' ? 'waiting for join…' : '—'}</span>
                            )}
                          </TableCell>
                          <TableCell className="text-xs">{gpuSummary(vm)}</TableCell>
                          <TableCell className="text-right font-mono text-xs tabular-nums">{formatHourly(vm.price_per_hour_usd)}</TableCell>
                          <TableCell className="text-xs text-muted-foreground">
                            <RelativeTime value={vm.created_at} />
                          </TableCell>
                          <TableCell className="sticky right-0 bg-card text-right shadow-[-4px_0_6px_-2px_rgb(var(--border))]" onClick={(e) => e.stopPropagation()}>
                            <DropdownMenu>
                              <DropdownMenuTrigger className="inline-flex h-8 w-8 cursor-pointer items-center justify-center rounded-md hover:bg-accent" aria-label={`Actions for ${vm.name}`}>
                                <MoreVertical className="h-4 w-4" />
                              </DropdownMenuTrigger>
                              <DropdownMenuContent align="end" className="w-48">
                                <DropdownMenuItem onClick={() => router.push(`/cloud/${vm.id}`)}>
                                  <ExternalLink className="mr-2 h-4 w-4" />
                                  Open
                                </DropdownMenuItem>
                                {['running', 'booting', 'error'].includes(vm.status) && (
                                  <DropdownMenuItem onClick={() => setDialog({ vm, action: 'stop' })}>
                                    <Square className="mr-2 h-4 w-4" />
                                    Stop
                                  </DropdownMenuItem>
                                )}
                                {vm.status === 'stopped' && (
                                  <DropdownMenuItem onClick={() => action.mutate({ id: vm.id, action: 'start' })}>
                                    <Play className="mr-2 h-4 w-4" />
                                    Start
                                  </DropdownMenuItem>
                                )}
                                <DropdownMenuSeparator />
                                {vm.status === 'terminated' || (vm.status === 'error' && !vm.provider_instance_id) ? (
                                  <DropdownMenuItem variant="destructive" onClick={() => remove.mutate(vm.id)}>
                                    <Trash2 className="mr-2 h-4 w-4" />
                                    Remove record
                                  </DropdownMenuItem>
                                ) : (
                                  <DropdownMenuItem variant="destructive" disabled={vm.status === 'terminating'} onClick={() => setDialog({ vm, action: 'terminate' })}>
                                    <Power className="mr-2 h-4 w-4" />
                                    Terminate
                                  </DropdownMenuItem>
                                )}
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
                  noun="VMs"
                  onPageChange={(page) => setParams({ page })}
                  onPageSizeChange={(pageSize) => setParams({ pageSize, page: 1 })}
                />
              </>
            )}
          </CardContent>
        </Card>
      )}

      {dialog && <CloudActionDialog vm={dialog.vm} action={dialog.action} open onOpenChange={(o) => !o && setDialog(null)} />}
    </PageContainer>
  );
}
