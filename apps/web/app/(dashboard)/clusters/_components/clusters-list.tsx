'use client';

import { useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { Boxes, Cpu, Edit, ExternalLink, MemoryStick, MoreVertical, Plus, Power, Server, Trash2 } from 'lucide-react';
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
import { LabelChips } from '@/components/shared/label-chips';
import { DeleteConfirmationDialog } from '@/components/shared/DeleteConfirmationDialog';
import { ConfirmationDialog } from '@/components/shared/ConfirmationDialog';
import { ClusterTypeBadge } from '@/components/fleet/cluster-type-badge';
import { VramMeter } from '@/components/fleet/vram-meter';
import { CLUSTER_BADGE } from '@/components/fleet/status';
import { useClusters, useDeleteCluster, useUpdateCluster } from '@/hooks/api/fleet';
import { useUrlState } from '@/hooks/use-url-state';
import { useDebounce } from '@/hooks/use-debounce';
import { formatBytes, formatDate } from '@/utils/format';
import { CLUSTER_TYPE_OPTIONS, type Cluster, type ClusterStatus, type ClusterType } from '@/types/fleet';

const DEFAULTS = { search: '', type: '', status: '', sort: '-created_at', page: 1, pageSize: 25 };

const STATUS_OPTIONS: { value: string; label: string }[] = [
  { value: 'all', label: 'All statuses' },
  { value: 'active', label: 'Active' },
  { value: 'disabled', label: 'Disabled' },
];

function sortField(sort: string) {
  return sort.startsWith('-') ? sort.slice(1) : sort;
}

export function ClustersList() {
  const router = useRouter();
  const { params, setParams } = useUrlState(DEFAULTS);
  const [search, setSearch] = useState(params.search);
  const debouncedSearch = useDebounce(search, 300);
  const [deleting, setDeleting] = useState<Cluster | null>(null);
  const [toggling, setToggling] = useState<Cluster | null>(null);

  // Mirror the debounced search into the URL (and go back to page 1).
  const urlSearch = params.search;
  useEffect(() => {
    if (debouncedSearch !== urlSearch) setParams({ search: debouncedSearch, page: 1 });
  }, [debouncedSearch, urlSearch, setParams]);

  const query = useMemo(
    () => ({
      search: debouncedSearch,
      type: params.type as ClusterType | '',
      status: params.status as ClusterStatus | '',
      sort: params.sort,
      page: params.page,
      page_size: params.pageSize,
    }),
    [debouncedSearch, params.type, params.status, params.sort, params.page, params.pageSize],
  );

  const { data, isLoading, isError, refetch } = useClusters(query);
  const deleteCluster = useDeleteCluster();
  const updateCluster = useUpdateCluster();

  const clusters = useMemo(() => data?.items ?? [], [data]);
  const total = data?.meta.total ?? 0;
  const totals = useMemo(
    () =>
      clusters.reduce(
        (acc, c) => ({
          nodes: acc.nodes + (c.counts?.nodes ?? 0),
          online: acc.online + (c.counts?.nodes_online ?? 0),
          devices: acc.devices + (c.counts?.devices ?? 0),
          vramUsed: acc.vramUsed + (c.counts?.vram_used_bytes ?? 0),
          vramTotal: acc.vramTotal + (c.counts?.vram_total_bytes ?? 0),
        }),
        { nodes: 0, online: 0, devices: 0, vramUsed: 0, vramTotal: 0 },
      ),
    [clusters],
  );

  function toggleSort(field: string) {
    const current = sortField(params.sort);
    const desc = params.sort.startsWith('-');
    setParams({ sort: current === field && desc ? field : `-${field}`, page: 1 });
  }

  if (isLoading && !data) return <ListPageSkeleton />;

  const sortDir = params.sort.startsWith('-') ? 'desc' : 'asc';
  const filtered = !!debouncedSearch || !!params.type || !!params.status;

  return (
    <PageContainer>
      <PageHeader
        title="Clusters"
        description="Bare metal, VM, Kubernetes and cloud clusters managed by this control plane."
        actions={
          <>
            <Button variant="outline" size="sm" className="rounded-sm" onClick={() => router.push('/nodes/join')}>
              <Server className="size-4" />
              Join a node
            </Button>
            <Button size="sm" className="rounded-sm" onClick={() => router.push('/clusters/new')}>
              <Plus className="size-4" />
              New cluster
            </Button>
          </>
        }
      />

      <div className="grid gap-4 md:grid-cols-4">
        <StatCard label="Clusters" value={total} icon={Boxes} footnote={`${clusters.filter((c) => c.status === 'active').length} active on this page`} />
        <StatCard label="Nodes online" value={`${totals.online} / ${totals.nodes}`} icon={Server} footnote="Across the listed clusters" />
        <StatCard label="GPUs" value={totals.devices} icon={Cpu} footnote="Accelerators reporting inventory" />
        <StatCard
          label="VRAM in use"
          value={totals.vramTotal > 0 ? `${((totals.vramUsed / totals.vramTotal) * 100).toFixed(0)}%` : '—'}
          icon={MemoryStick}
          footnote={totals.vramTotal > 0 ? `${formatBytes(totals.vramUsed)} of ${formatBytes(totals.vramTotal)}` : 'No inventory yet'}
        />
      </div>

      {isError ? (
        <ErrorState title="Could not load clusters" description="The control plane did not answer. Retry in a moment." onRetry={() => void refetch()} />
      ) : (
        <Card className="rounded-md shadow-premium">
          <CardHeader>
            <div className="flex flex-wrap items-center gap-3">
              <SearchInput value={search} onChange={setSearch} placeholder="Search clusters…" />
              <Select value={params.type || 'all'} onValueChange={(v) => setParams({ type: v === 'all' ? '' : String(v), page: 1 })}>
                <SelectTrigger className="w-44">
                  <SelectValue>
                    {(val: unknown) => CLUSTER_TYPE_OPTIONS.find((o) => o.value === val)?.label ?? 'All types'}
                  </SelectValue>
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">All types</SelectItem>
                  {CLUSTER_TYPE_OPTIONS.map((o) => (
                    <SelectItem key={o.value} value={o.value}>
                      {o.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Select value={params.status || 'all'} onValueChange={(v) => setParams({ status: v === 'all' ? '' : String(v), page: 1 })}>
                <SelectTrigger className="w-40">
                  <SelectValue>{(val: unknown) => STATUS_OPTIONS.find((o) => o.value === val)?.label ?? 'All statuses'}</SelectValue>
                </SelectTrigger>
                <SelectContent>
                  {STATUS_OPTIONS.map((o) => (
                    <SelectItem key={o.value} value={o.value}>
                      {o.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </CardHeader>
          <CardContent>
            {clusters.length === 0 ? (
              <EmptyState
                icon={Boxes}
                title={filtered ? 'No clusters match' : 'No clusters yet'}
                description={filtered ? 'Try a different search or clear the filters.' : 'Create a cluster, then join GPU machines to it with a one-time token.'}
                action={
                  !filtered && (
                    <Button size="sm" className="rounded-sm" onClick={() => router.push('/clusters/new')}>
                      <Plus className="size-4" />
                      New cluster
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
                            Cluster
                            <SortIcon field="name" sortField={sortField(params.sort)} sortDirection={sortDir} />
                          </div>
                        </TableHead>
                        <TableHead>Type</TableHead>
                        <TableHead>Region</TableHead>
                        <TableHead className="text-right">Nodes</TableHead>
                        <TableHead className="text-right">GPUs</TableHead>
                        <TableHead>VRAM</TableHead>
                        <TableHead>Status</TableHead>
                        <TableHead className="cursor-pointer hover:bg-muted/50" onClick={() => toggleSort('created_at')}>
                          <div className="flex items-center gap-1">
                            Created
                            <SortIcon field="created_at" sortField={sortField(params.sort)} sortDirection={sortDir} />
                          </div>
                        </TableHead>
                        <TableHead className="sticky right-0 bg-card text-right shadow-[-4px_0_6px_-2px_rgb(var(--border))]">Actions</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {clusters.map((cluster) => {
                        const counts = cluster.counts ?? {};
                        const online = counts.nodes_online ?? 0;
                        const nodes = counts.nodes ?? 0;
                        return (
                          <TableRow key={cluster.id} className="cursor-pointer" onClick={() => router.push(`/clusters/${cluster.id}`)}>
                            <TableCell className="font-medium">
                              <div className="flex flex-col gap-1">
                                <Link href={`/clusters/${cluster.id}`} className="font-semibold hover:underline" onClick={(e) => e.stopPropagation()}>
                                  {cluster.name}
                                </Link>
                                {cluster.description && <span className="line-clamp-1 text-xs text-muted-foreground">{cluster.description}</span>}
                                <LabelChips labels={cluster.labels} max={2} />
                              </div>
                            </TableCell>
                            <TableCell>
                              <ClusterTypeBadge type={cluster.type} />
                            </TableCell>
                            <TableCell className="text-muted-foreground">{cluster.region ?? '—'}</TableCell>
                            <TableCell className="text-right tabular-nums">
                              <span className="inline-flex items-center gap-1.5">
                                <span className={online === nodes && nodes > 0 ? 'size-1.5 rounded-full bg-success' : online > 0 ? 'size-1.5 rounded-full bg-warning' : 'size-1.5 rounded-full bg-muted-foreground/40'} />
                                {online} / {nodes}
                              </span>
                            </TableCell>
                            <TableCell className="text-right tabular-nums">{counts.devices ?? 0}</TableCell>
                            <TableCell>
                              {counts.vram_total_bytes ? (
                                <VramMeter used={counts.vram_used_bytes ?? 0} total={counts.vram_total_bytes} />
                              ) : (
                                <span className="text-xs text-muted-foreground">—</span>
                              )}
                            </TableCell>
                            <TableCell>
                              <StatusBadge variant={CLUSTER_BADGE[cluster.status]}>{cluster.status === 'active' ? 'Active' : 'Disabled'}</StatusBadge>
                            </TableCell>
                            <TableCell className="text-muted-foreground">{formatDate(cluster.created_at)}</TableCell>
                            <TableCell className="sticky right-0 bg-card text-right shadow-[-4px_0_6px_-2px_rgb(var(--border))]">
                              <DropdownMenu>
                                <DropdownMenuTrigger
                                  onClick={(e) => e.stopPropagation()}
                                  className="inline-flex h-8 w-8 cursor-pointer items-center justify-center rounded-md hover:bg-accent"
                                  aria-label="Cluster actions"
                                >
                                  <MoreVertical className="h-4 w-4" />
                                </DropdownMenuTrigger>
                                <DropdownMenuContent align="end">
                                  <DropdownMenuItem onClick={(e) => { e.stopPropagation(); router.push(`/clusters/${cluster.id}`); }}>
                                    <ExternalLink className="mr-2 h-4 w-4" />
                                    View details
                                  </DropdownMenuItem>
                                  <DropdownMenuItem onClick={(e) => { e.stopPropagation(); router.push(`/clusters/${cluster.id}/edit`); }}>
                                    <Edit className="mr-2 h-4 w-4" />
                                    Edit cluster
                                  </DropdownMenuItem>
                                  <DropdownMenuItem onClick={(e) => { e.stopPropagation(); router.push(`/nodes/join?cluster_id=${cluster.id}`); }}>
                                    <Server className="mr-2 h-4 w-4" />
                                    Join a node
                                  </DropdownMenuItem>
                                  <DropdownMenuSeparator />
                                  <DropdownMenuItem onClick={(e) => { e.stopPropagation(); setToggling(cluster); }}>
                                    <Power className="mr-2 h-4 w-4" />
                                    {cluster.status === 'active' ? 'Disable' : 'Enable'}
                                  </DropdownMenuItem>
                                  <DropdownMenuItem variant="destructive" onClick={(e) => { e.stopPropagation(); setDeleting(cluster); }}>
                                    <Trash2 className="mr-2 h-4 w-4" />
                                    Delete
                                  </DropdownMenuItem>
                                </DropdownMenuContent>
                              </DropdownMenu>
                            </TableCell>
                          </TableRow>
                        );
                      })}
                    </TableBody>
                  </Table>
                </div>
                <TablePagination
                  page={params.page}
                  pageSize={params.pageSize}
                  total={total}
                  noun="clusters"
                  onPageChange={(page) => setParams({ page })}
                  onPageSizeChange={(pageSize) => setParams({ pageSize, page: 1 })}
                />
              </>
            )}
          </CardContent>
        </Card>
      )}

      <DeleteConfirmationDialog
        open={!!deleting}
        onOpenChange={(open) => !open && setDeleting(null)}
        itemName={deleting?.name ?? ''}
        resourceType="cluster"
        warningMessage={
          deleting?.counts?.nodes
            ? `${deleting.counts.nodes} node(s) and their instances will be removed from the fleet. Agents keep running but are cut off.`
            : undefined
        }
        isDeleting={deleteCluster.isPending}
        onConfirm={() => {
          if (!deleting) return;
          deleteCluster.mutate(deleting.id, { onSuccess: () => setDeleting(null) });
        }}
      />

      <ConfirmationDialog
        open={!!toggling}
        onOpenChange={(open) => !open && setToggling(null)}
        title={toggling?.status === 'active' ? `Disable ${toggling?.name}?` : `Enable ${toggling?.name}?`}
        description={
          toggling?.status === 'active'
            ? 'The scheduler stops placing new instances in this cluster. Running instances keep serving.'
            : 'The scheduler can place instances in this cluster again.'
        }
        confirmText={toggling?.status === 'active' ? 'Disable' : 'Enable'}
        variant={toggling?.status === 'active' ? 'destructive' : 'default'}
        isLoading={updateCluster.isPending}
        onConfirm={() => {
          if (!toggling) return;
          updateCluster.mutate({ id: toggling.id, body: { status: toggling.status === 'active' ? 'disabled' : 'active' } });
        }}
      />
    </PageContainer>
  );
}
