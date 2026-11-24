'use client';

import { useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { Activity, ExternalLink, Gauge, MoreVertical, Play, Plus, Rocket, RotateCw, Scale, Square, Timer, Trash2 } from 'lucide-react';
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
import { SortIcon } from '@/components/shared/SortIcon';
import { StatCard } from '@/components/shared/stat-card';
import { SearchInput } from '@/components/shared/search-input';
import { TablePagination } from '@/components/shared/table-pagination';
import { ConfirmationDialog } from '@/components/shared/ConfirmationDialog';
import { DeleteConfirmationDialog } from '@/components/shared/DeleteConfirmationDialog';
import { DEPLOYMENT_BADGE } from '@/components/fleet/status';
import { EngineBadge, ModelTypeBadge, PriorityBadge } from '@/components/models/model-badges';
import { ExternalBadge } from '@/components/models/external-badge';
import { ScaleDialog } from '@/components/models/deployment-panels';
import { useDeleteDeployment, useDeployments, useRestartDeployment, useStartDeployment, useStopDeployment } from '@/hooks/api/models-deployments';
import { useUrlState } from '@/hooks/use-url-state';
import { useDebounce } from '@/hooks/use-debounce';
import { cn } from '@/lib/utils';
import { DEPLOYMENT_STATUS_LABELS, ENGINE_LABELS, EXTERNAL_ENGINE_LABELS, MODEL_PRIORITY_LABELS, isExternal, type Deployment, type DeploymentOrigin, type DeploymentStatus, type EngineName, type ModelPriority } from '@/types/models';

const DEFAULTS = { search: '', status: '', engine: '', priority: '', origin: '', sort: '-created_at', page: 1, pageSize: 25 };
const ORIGIN_LABELS: Record<string, string> = { all: 'Managed + external', managed: 'Managed only', external: 'External only' };
const STATUSES: DeploymentStatus[] = ['running', 'degraded', 'deploying', 'scaling', 'scheduling', 'stopped', 'failed'];

function sortField(sort: string) {
  return sort.startsWith('-') ? sort.slice(1) : sort;
}

/** Replica pips: filled = ready, outlined = desired but not ready. */
function ReplicaPips({ d }: { d: Deployment }) {
  if (isExternal(d)) {
    const up = d.instances?.some((i) => i.state === 'ready' && i.health === 'healthy');
    return (
      <span className="inline-flex items-center gap-1.5 text-xs text-muted-foreground" title="One server observed by the node agent">
        <span className={cn('h-3 w-1.5 rounded-[2px]', up ? 'bg-success' : 'border border-muted-foreground/40')} aria-hidden />
        observed
      </span>
    );
  }
  const ready = d.ready_replicas ?? 0;
  const live = d.instances ?? [];
  const desired = Math.max(d.replicas, live.length);
  return (
    <div className="flex items-center gap-2">
      <div className="flex gap-0.5" aria-hidden>
        {Array.from({ length: Math.min(desired, 8) }).map((_, i) => {
          const inst = live[i];
          const state = inst?.state;
          return <span key={i} className={cn('h-3 w-1.5 rounded-[2px]', state === 'ready' ? 'bg-success' : state === 'loading' || state === 'draining' || state === 'unloading' ? 'animate-pulse bg-info' : state === 'error' ? 'bg-error' : 'border border-muted-foreground/40')} />;
        })}
      </div>
      <span className="font-mono text-xs tabular-nums">
        {ready}/{d.replicas}
      </span>
    </div>
  );
}

export function DeploymentsList() {
  const router = useRouter();
  const { params, setParams } = useUrlState(DEFAULTS);
  const [search, setSearch] = useState(params.search);
  const debouncedSearch = useDebounce(search, 300);
  const [scaling, setScaling] = useState<Deployment | null>(null);
  const [restarting, setRestarting] = useState<Deployment | null>(null);
  const [stopping, setStopping] = useState<Deployment | null>(null);
  const [deleting, setDeleting] = useState<Deployment | null>(null);
  const start = useStartDeployment();
  const stop = useStopDeployment();
  const restart = useRestartDeployment();
  const remove = useDeleteDeployment();

  const urlSearch = params.search;
  useEffect(() => {
    if (debouncedSearch !== urlSearch) setParams({ search: debouncedSearch, page: 1 });
  }, [debouncedSearch, urlSearch, setParams]);

  const query = useMemo(
    () => ({
      search: debouncedSearch,
      status: params.status as DeploymentStatus | '',
      engine: params.engine as EngineName | '',
      priority: params.priority as ModelPriority | '',
      origin: params.origin as DeploymentOrigin | '',
      sort: params.sort,
      page: params.page,
      page_size: params.pageSize,
    }),
    [debouncedSearch, params.status, params.engine, params.priority, params.origin, params.sort, params.page, params.pageSize],
  );
  const { data, isLoading, isError, refetch } = useDeployments(query);
  const all = useDeployments({ page_size: 500 });
  const list = data?.items ?? [];
  const everything = all.data?.items ?? [];
  const running = everything.filter((d) => d.status === 'running').length;
  const ready = everything.reduce((s, d) => s + (d.ready_replicas ?? 0), 0);
  const desired = everything.reduce((s, d) => s + d.replicas, 0);
  const active = everything.reduce((s, d) => s + (d.live?.active_sessions ?? 0), 0);
  const waiting = everything.reduce((s, d) => s + (d.live?.waiting_sessions ?? 0), 0);
  const rpm = everything.reduce((s, d) => s + (d.live?.requests_per_minute ?? 0), 0);
  const worstTtft = Math.max(0, ...everything.map((d) => d.live?.ttft_ms_p95 ?? 0));

  function toggleSort(field: string) {
    const current = sortField(params.sort);
    const desc = params.sort.startsWith('-');
    setParams({ sort: current === field && !desc ? `-${field}` : field, page: 1 });
  }

  if (isLoading && !data) return <ListPageSkeleton />;
  const sortDir = params.sort.startsWith('-') ? 'desc' : 'asc';
  const filtered = !!debouncedSearch || !!params.status || !!params.engine || !!params.priority || !!params.origin;

  return (
    <PageContainer>
      <PageHeader
        title="Deployments"
        description="Models being served: engine, replicas, placement, live sessions and latency."
        actions={
          <Button size="sm" className="rounded-sm" onClick={() => router.push('/deployments/new')}>
            <Plus className="size-4" />
            New deployment
          </Button>
        }
      />

      <div className="grid gap-4 md:grid-cols-4">
        <StatCard label="Running" value={`${running} / ${everything.length}`} icon={Rocket} footnote={`${ready} of ${desired} replicas ready`} />
        <StatCard label="Sessions" value={active} icon={Activity} footnote={waiting ? <span className="text-warning-dark dark:text-warning-light">{waiting} waiting in queues</span> : 'Nothing queued'} />
        <StatCard label="Requests / min" value={Math.round(rpm).toLocaleString()} icon={Gauge} footnote="All deployments, last minute" />
        <StatCard label="Worst TTFT p95" value={worstTtft ? `${Math.round(worstTtft)} ms` : '—'} icon={Timer} footnote="Across ready replicas" />
      </div>

      {isError ? (
        <ErrorState title="Could not load deployments" description="The control plane did not answer. Retry in a moment." onRetry={() => void refetch()} />
      ) : (
        <Card className="rounded-md shadow-premium">
          <CardHeader>
            <div className="flex flex-wrap items-center gap-3">
              <SearchInput value={search} onChange={setSearch} placeholder="Search deployments or models…" />
              <Select value={params.status || 'all'} onValueChange={(v) => setParams({ status: v === 'all' ? '' : String(v), page: 1 })}>
                <SelectTrigger className="w-40">
                  <SelectValue>{(v: unknown) => (v === 'all' ? 'All statuses' : DEPLOYMENT_STATUS_LABELS[v as DeploymentStatus])}</SelectValue>
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">All statuses</SelectItem>
                  {STATUSES.map((s) => (
                    <SelectItem key={s} value={s}>
                      {DEPLOYMENT_STATUS_LABELS[s]}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Select value={params.engine || 'all'} onValueChange={(v) => setParams({ engine: v === 'all' ? '' : String(v), page: 1 })}>
                <SelectTrigger className="w-40">
                  <SelectValue>{(v: unknown) => (v === 'all' ? 'All engines' : ENGINE_LABELS[v as EngineName])}</SelectValue>
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">All engines</SelectItem>
                  {(Object.keys(ENGINE_LABELS) as EngineName[]).map((e) => (
                    <SelectItem key={e} value={e}>
                      {ENGINE_LABELS[e]}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Select value={params.priority || 'all'} onValueChange={(v) => setParams({ priority: v === 'all' ? '' : String(v), page: 1 })}>
                <SelectTrigger className="w-36">
                  <SelectValue>{(v: unknown) => (v === 'all' ? 'Any priority' : MODEL_PRIORITY_LABELS[v as ModelPriority])}</SelectValue>
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">Any priority</SelectItem>
                  {(Object.keys(MODEL_PRIORITY_LABELS) as ModelPriority[]).map((p) => (
                    <SelectItem key={p} value={p}>
                      {MODEL_PRIORITY_LABELS[p]}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Select value={params.origin || 'all'} onValueChange={(v) => setParams({ origin: v === 'all' ? '' : String(v), page: 1 })}>
                <SelectTrigger className="w-44" aria-label="Origin">
                  <SelectValue>{(v: unknown) => ORIGIN_LABELS[String(v)] ?? ORIGIN_LABELS.all}</SelectValue>
                </SelectTrigger>
                <SelectContent>
                  {Object.entries(ORIGIN_LABELS).map(([value, label]) => (
                    <SelectItem key={value} value={value}>
                      {label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </CardHeader>
          <CardContent>
            {list.length === 0 ? (
              <EmptyState
                icon={Rocket}
                title={filtered ? 'No deployments match' : 'Nothing deployed yet'}
                description={filtered ? 'Try a different search or clear the filters.' : 'Pick a model from the catalog; the wizard checks engines, previews placement and starts it.'}
                action={
                  !filtered && (
                    <Button size="sm" className="rounded-sm" onClick={() => router.push('/deployments/new')}>
                      <Plus className="size-4" />
                      New deployment
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
                        <TableHead className="cursor-pointer hover:bg-muted/50" onClick={() => toggleSort('name')}>
                          <div className="flex items-center gap-1">
                            Deployment
                            <SortIcon field="name" sortField={sortField(params.sort)} sortDirection={sortDir} />
                          </div>
                        </TableHead>
                        <TableHead>Engine</TableHead>
                        <TableHead>Replicas</TableHead>
                        <TableHead>Nodes</TableHead>
                        <TableHead className="text-right">Active / waiting</TableHead>
                        <TableHead className="text-right">Req/min</TableHead>
                        <TableHead className="text-right">TTFT p95</TableHead>
                        <TableHead>Status</TableHead>
                        <TableHead className="w-10" />
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {list.map((d) => {
                        const live = d.live ?? {};
                        const external = isExternal(d);
                        const nodes = [...new Set((d.instances ?? []).map((i) => i.node_name).filter(Boolean))];
                        return (
                          <TableRow key={d.id} className="cursor-pointer" onClick={() => router.push(`/deployments/${d.id}`)}>
                            <TableCell>
                              <div className="flex items-center gap-1.5">
                                <Link href={`/deployments/${d.id}`} className="font-mono text-[13px] font-semibold hover:underline" onClick={(e) => e.stopPropagation()}>
                                  {d.name}
                                </Link>
                                {external && <ExternalBadge compact />}
                              </div>
                              <div className="mt-0.5 flex flex-wrap items-center gap-1.5 text-[11px] text-muted-foreground">
                                {d.model_name}
                                {d.model_type && <ModelTypeBadge type={d.model_type} className="h-4 text-[10px]" />}
                                {d.effective_priority && d.effective_priority !== 'normal' && <PriorityBadge priority={d.effective_priority} className="h-4 text-[10px]" />}
                              </div>
                            </TableCell>
                            <TableCell>
                              {external && d.external ? (
                                <span className="inline-flex items-center gap-1.5 text-xs">
                                  <span className="font-medium">{EXTERNAL_ENGINE_LABELS[d.external.engine]}</span>
                                  {d.external.version && <span className="font-mono text-[11px] text-muted-foreground">{d.external.version}</span>}
                                </span>
                              ) : (
                                <EngineBadge engine={d.engine_name} version={d.engine_version} />
                              )}
                              {d.preset && <div className="text-[10.5px] text-muted-foreground">{d.preset.replace('_', ' ')}</div>}
                            </TableCell>
                            <TableCell>
                              <ReplicaPips d={d} />
                            </TableCell>
                            <TableCell className="max-w-[180px] truncate text-xs text-muted-foreground">{nodes.join(', ') || '—'}</TableCell>
                            <TableCell className="text-right font-mono text-xs tabular-nums">
                              {live.active_sessions ?? '—'}
                              <span className="text-muted-foreground"> / </span>
                              <span className={cn((live.waiting_sessions ?? 0) > 0 && 'text-warning-dark dark:text-warning-light')}>{live.waiting_sessions ?? '—'}</span>
                            </TableCell>
                            <TableCell className="text-right font-mono text-xs tabular-nums">{live.requests_per_minute !== undefined && live.requests_per_minute !== null ? live.requests_per_minute.toFixed(1) : '—'}</TableCell>
                            <TableCell className="text-right font-mono text-xs tabular-nums">{live.ttft_ms_p95 ? `${Math.round(live.ttft_ms_p95)} ms` : '—'}</TableCell>
                            <TableCell>
                              <StatusBadge variant={DEPLOYMENT_BADGE[d.status]}>{DEPLOYMENT_STATUS_LABELS[d.status]}</StatusBadge>
                              {d.status_message && <div className="mt-0.5 max-w-[200px] truncate text-[10.5px] text-muted-foreground" title={d.status_message}>{d.status_message}</div>}
                            </TableCell>
                            <TableCell>
                              <DropdownMenu>
                                <DropdownMenuTrigger onClick={(e) => e.stopPropagation()} className="inline-flex h-8 w-8 cursor-pointer items-center justify-center rounded-md hover:bg-accent" aria-label={`Actions for ${d.name}`}>
                                  <MoreVertical className="h-4 w-4" />
                                </DropdownMenuTrigger>
                                <DropdownMenuContent align="end" onClick={(e) => e.stopPropagation()}>
                                  <DropdownMenuItem onClick={() => router.push(`/deployments/${d.id}`)}>
                                    <ExternalLink className="mr-2 h-4 w-4" />
                                    View details
                                  </DropdownMenuItem>
                                  {!external && (
                                    <>
                                  <DropdownMenuItem onClick={() => setScaling(d)} disabled={!d.desired_running}>
                                    <Scale className="mr-2 h-4 w-4" />
                                    Scale
                                  </DropdownMenuItem>
                                  <DropdownMenuItem onClick={() => setRestarting(d)} disabled={!d.desired_running}>
                                    <RotateCw className="mr-2 h-4 w-4" />
                                    Rolling restart
                                  </DropdownMenuItem>
                                  {d.desired_running ? (
                                    <DropdownMenuItem onClick={() => setStopping(d)}>
                                      <Square className="mr-2 h-4 w-4" />
                                      Stop
                                    </DropdownMenuItem>
                                  ) : (
                                    <DropdownMenuItem onClick={() => start.mutate({ id: d.id })}>
                                      <Play className="mr-2 h-4 w-4" />
                                      Start
                                    </DropdownMenuItem>
                                  )}
                                  <DropdownMenuSeparator />
                                  <DropdownMenuItem variant="destructive" onClick={() => setDeleting(d)}>
                                    <Trash2 className="mr-2 h-4 w-4" />
                                    Delete
                                  </DropdownMenuItem>
                                    </>
                                  )}
                                </DropdownMenuContent>
                              </DropdownMenu>
                            </TableCell>
                          </TableRow>
                        );
                      })}
                    </TableBody>
                  </Table>
                </div>
                <TablePagination page={params.page} pageSize={params.pageSize} total={data?.meta.total ?? 0} noun="deployments" onPageChange={(page) => setParams({ page })} onPageSizeChange={(pageSize) => setParams({ pageSize, page: 1 })} />
              </>
            )}
          </CardContent>
        </Card>
      )}

      {scaling && <ScaleDialog key={scaling.id} deployment={scaling} open onOpenChange={(o) => !o && setScaling(null)} />}
      <ConfirmationDialog
        open={!!restarting}
        onOpenChange={(o) => !o && setRestarting(null)}
        title={`Rolling restart of ${restarting?.name ?? ''}?`}
        description="Each replica is drained, reloaded and health-checked before the next one starts, so the model keeps serving."
        confirmText="Restart"
        isLoading={restart.isPending}
        onConfirm={() => restarting && restart.mutate({ id: restarting.id })}
      />
      <ConfirmationDialog
        open={!!stopping}
        onOpenChange={(o) => !o && setStopping(null)}
        title={`Stop ${stopping?.name ?? ''}?`}
        description="All replicas are drained and unloaded; the deployment and its settings stay. Requests get model_not_ready until it starts again (or on-demand load kicks in)."
        confirmText="Stop"
        variant="destructive"
        isLoading={stop.isPending}
        onConfirm={() => stopping && stop.mutate({ id: stopping.id })}
      />
      <DeleteConfirmationDialog
        open={!!deleting}
        onOpenChange={(o) => !o && setDeleting(null)}
        itemName={deleting?.name ?? ''}
        resourceType="deployment"
        warningMessage="Every replica is drained and unloaded first. Cached weights stay on the nodes."
        isDeleting={remove.isPending}
        onConfirm={() => {
          if (deleting) remove.mutate(deleting.id, { onSuccess: () => setDeleting(null) });
        }}
      />
    </PageContainer>
  );
}
