'use client';

import { useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { ArrowDownToLine, ArrowUpFromLine, Cpu, ExternalLink, HardDrive, KeyRound, MoreVertical, Pencil, Plus, RotateCw, Server, Trash2, X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from '@/components/ui/dropdown-menu';
import { PageContainer } from '@/components/shared/PageContainer';
import { PageHeader } from '@/components/shared/PageHeader';
import { ListPageSkeleton } from '@/components/shared/ListPageSkeleton';
import { ErrorState } from '@/components/shared/ErrorState';
import { EmptyState } from '@/components/shared/EmptyState';
import { SortIcon } from '@/components/shared/SortIcon';
import { StatCard } from '@/components/shared/stat-card';
import { SearchInput } from '@/components/shared/search-input';
import { TablePagination } from '@/components/shared/table-pagination';
import { LabelChips } from '@/components/shared/label-chips';
import { ConfirmationDialog } from '@/components/shared/ConfirmationDialog';
import { ActivityBadge, nodeActivity } from '@/components/fleet/activity-badge';
import { NodeStatusBadge, isNodeConnected, isNodeDetached, nodeStateInput } from '@/components/fleet/node-status';
import { Heartbeat } from '@/components/fleet/heartbeat';
import { VramMeter } from '@/components/fleet/vram-meter';
import { ExpiryCountdown } from '@/components/fleet/expiry-countdown';
import { DrainNodeDialog, EditNodeDialog, RemoveNodeDialog } from '@/components/fleet/node-dialogs';
import { useClusters, useDevices, useJoinTokens, useNodes, useRevokeJoinToken, useRotateNodeCertificate, useUndrainNode } from '@/hooks/api/fleet';
import { useUrlState } from '@/hooks/use-url-state';
import { useDebounce } from '@/hooks/use-debounce';
import { formatBytes } from '@/utils/format';
import { NODE_OS_LABELS, NODE_STATUS_LABELS, type Node, type NodeOs, type NodeStatus } from '@/types/fleet';

const DEFAULTS = { search: '', cluster: '', status: '', os: '', sort: 'name', page: 1, pageSize: 25 };

const STATUS_FILTERS: NodeStatus[] = ['online', 'pending', 'draining', 'drained', 'offline', 'unhealthy'];

function sortField(sort: string) {
  return sort.startsWith('-') ? sort.slice(1) : sort;
}

function ActiveTokens() {
  const tokens = useJoinTokens({ active_only: true, page_size: 10 });
  const clusters = useClusters({ page_size: 100 });
  const revoke = useRevokeJoinToken();
  const list = tokens.data?.items ?? [];
  if (list.length === 0) return null;
  const clusterName = (id: string) => clusters.data?.items.find((c) => c.id === id)?.name ?? id.slice(0, 8);
  return (
    <Card className="gap-0 rounded-md py-0 shadow-premium">
      <CardHeader className="flex flex-row items-center justify-between gap-3 border-b border-border px-4 py-3">
        <div className="flex items-center gap-2">
          <KeyRound className="size-4 text-muted-foreground" />
          <CardTitle className="text-sm font-semibold">Join tokens waiting for an agent</CardTitle>
        </div>
        <CardDescription className="text-xs">One-time, hashed at rest, shown only when issued</CardDescription>
      </CardHeader>
      <CardContent className="divide-y divide-border px-0">
        {list.map((t) => (
          <div key={t.id} className="flex flex-wrap items-center gap-x-6 gap-y-1 px-4 py-2.5 text-xs">
            <span className="font-mono font-medium">{t.token_prefix}…</span>
            <span className="text-muted-foreground">
              for <span className="font-medium text-foreground">{t.node_name_hint ?? 'any name'}</span> in {clusterName(t.cluster_id)}
            </span>
            <LabelChips labels={t.labels as Record<string, string>} max={3} />
            <span className="ml-auto flex items-center gap-3">
              <ExpiryCountdown variant="inline" createdAt={t.created_at} expiresAt={t.expires_at} />
              <Button variant="ghost" size="xs" className="text-muted-foreground hover:text-destructive" disabled={revoke.isPending} onClick={() => revoke.mutate(t.id)}>
                <X className="size-3" />
                Revoke
              </Button>
            </span>
          </div>
        ))}
      </CardContent>
    </Card>
  );
}

export function NodesList() {
  const router = useRouter();
  const { params, setParams } = useUrlState(DEFAULTS);
  const [search, setSearch] = useState(params.search);
  const debouncedSearch = useDebounce(search, 300);
  const [draining, setDraining] = useState<Node | null>(null);
  const [editing, setEditing] = useState<Node | null>(null);
  const [removing, setRemoving] = useState<Node | null>(null);
  const [rotating, setRotating] = useState<Node | null>(null);
  const undrain = useUndrainNode();
  const rotate = useRotateNodeCertificate();

  const urlSearch = params.search;
  useEffect(() => {
    if (debouncedSearch !== urlSearch) setParams({ search: debouncedSearch, page: 1 });
  }, [debouncedSearch, urlSearch, setParams]);

  const query = useMemo(
    () => ({
      search: debouncedSearch,
      cluster_id: params.cluster || undefined,
      status: params.status as NodeStatus | '',
      os: params.os as NodeOs | '',
      sort: params.sort,
      page: params.page,
      page_size: params.pageSize,
    }),
    [debouncedSearch, params.cluster, params.status, params.os, params.sort, params.page, params.pageSize],
  );

  const { data, isLoading, isError, refetch } = useNodes(query);
  const all = useNodes({ page_size: 500 });
  const fleetDevices = useDevices({ page_size: 200 });
  const clusters = useClusters({ page_size: 100 });

  const nodes = data?.items ?? [];
  const total = data?.meta.total ?? 0;
  const fleet = all.data?.items ?? [];
  const connected = fleet.filter((n) => isNodeConnected(nodeStateInput(n))).length;
  const detachedCount = fleet.filter((n) => n.status !== 'removed' && isNodeDetached(nodeStateInput(n))).length;
  const gpus = fleet.reduce((s, n) => s + (n.device_count ?? 0), 0);
  const instances = fleet.reduce((s, n) => s + (n.instance_count ?? 0), 0);
  const versions = [...new Set(fleet.map((n) => n.agent_version).filter(Boolean))] as string[];
  const cacheUsed = fleet.reduce((s, n) => s + (n.cache_used_bytes ?? 0), 0);
  const cacheLimit = fleet.reduce((s, n) => s + (n.cache_limit_bytes ?? 0), 0);
  const devicesByNode = (nodeId: string) => (fleetDevices.data?.items ?? []).filter((d) => d.node_id === nodeId);
  const clusterName = (id: string) => clusters.data?.items.find((c) => c.id === id)?.name;

  function toggleSort(field: string) {
    const current = sortField(params.sort);
    const desc = params.sort.startsWith('-');
    setParams({ sort: current === field && !desc ? `-${field}` : field, page: 1 });
  }

  if (isLoading && !data) return <ListPageSkeleton />;

  const sortDir = params.sort.startsWith('-') ? 'desc' : 'asc';
  const filtered = !!debouncedSearch || !!params.cluster || !!params.status || !!params.os;

  return (
    <PageContainer>
      <PageHeader
        title="Nodes"
        description="GPU machines running the node agent: heartbeat, inventory, drain and removal."
        actions={
          <Button size="sm" className="rounded-sm" onClick={() => router.push('/nodes/join')}>
            <Plus className="size-4" />
            Add node
          </Button>
        }
      />

      <div className="grid gap-4 md:grid-cols-4">
        <StatCard label="Nodes connected" value={`${connected} / ${fleet.length}`} icon={Server} footnote={detachedCount > 0 ? `${detachedCount} detached (agent gone)` : connected < fleet.length ? `${fleet.length - connected} not reporting yet` : 'Every agent is heartbeating'} />
        <StatCard label="GPUs" value={gpus} icon={Cpu} footnote={`${instances} engine instance${instances === 1 ? '' : 's'} placed`} />
        <StatCard label="Agent versions" value={versions.length || '—'} icon={RotateCw} footnote={versions.length === 1 ? <span className="font-mono">{versions[0]}</span> : versions.length > 1 ? 'Mixed — plan an upgrade' : 'No agents yet'} />
        <StatCard label="Model cache" value={formatBytes(cacheUsed)} icon={HardDrive} footnote={cacheLimit ? `of ${formatBytes(cacheLimit)} allowed across nodes` : 'No limits set'} />
      </div>

      <ActiveTokens />

      {isError ? (
        <ErrorState title="Could not load nodes" description="The control plane did not answer. Retry in a moment." onRetry={() => void refetch()} />
      ) : (
        <Card className="rounded-md shadow-premium">
          <CardHeader>
            <div className="flex flex-wrap items-center gap-3">
              <SearchInput value={search} onChange={setSearch} placeholder="Search name, IP or label (zone=a)…" />
              <Select value={params.cluster || 'all'} onValueChange={(v) => setParams({ cluster: v === 'all' ? '' : String(v), page: 1 })}>
                <SelectTrigger className="w-44">
                  <SelectValue>{(v: unknown) => (v === 'all' ? 'All clusters' : clusterName(String(v)) ?? 'Cluster')}</SelectValue>
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">All clusters</SelectItem>
                  {(clusters.data?.items ?? []).map((c) => (
                    <SelectItem key={c.id} value={c.id}>
                      {c.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Select value={params.status || 'all'} onValueChange={(v) => setParams({ status: v === 'all' ? '' : String(v), page: 1 })}>
                <SelectTrigger className="w-40">
                  <SelectValue>{(v: unknown) => (v === 'all' ? 'All statuses' : NODE_STATUS_LABELS[v as NodeStatus])}</SelectValue>
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">All statuses</SelectItem>
                  {STATUS_FILTERS.map((s) => (
                    <SelectItem key={s} value={s}>
                      {NODE_STATUS_LABELS[s]}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Select value={params.os || 'all'} onValueChange={(v) => setParams({ os: v === 'all' ? '' : String(v), page: 1 })}>
                <SelectTrigger className="w-32">
                  <SelectValue>{(v: unknown) => (v === 'all' ? 'Any OS' : NODE_OS_LABELS[v as NodeOs])}</SelectValue>
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">Any OS</SelectItem>
                  <SelectItem value="linux">Linux</SelectItem>
                  <SelectItem value="macos">macOS</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </CardHeader>
          <CardContent>
            {nodes.length === 0 ? (
              <EmptyState
                icon={Server}
                title={filtered ? 'No nodes match' : 'No nodes yet'}
                description={filtered ? 'Try a different search or clear the filters.' : 'Issue a one-time join token and run the agent on a GPU machine — Linux, macOS or Kubernetes.'}
                action={
                  !filtered && (
                    <Button size="sm" className="rounded-sm" onClick={() => router.push('/nodes/join')}>
                      <Plus className="size-4" />
                      Add node
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
                            Node
                            <SortIcon field="name" sortField={sortField(params.sort)} sortDirection={sortDir} />
                          </div>
                        </TableHead>
                        <TableHead>Status</TableHead>
                        <TableHead>Platform</TableHead>
                        <TableHead>Agent</TableHead>
                        <TableHead className="text-right">GPUs</TableHead>
                        <TableHead className="text-right">Instances</TableHead>
                        <TableHead>Model cache</TableHead>
                        <TableHead className="cursor-pointer hover:bg-muted/50" onClick={() => toggleSort('last_heartbeat_at')}>
                          <div className="flex items-center gap-1">
                            Heartbeat
                            <SortIcon field="last_heartbeat_at" sortField={sortField(params.sort)} sortDirection={sortDir} />
                          </div>
                        </TableHead>
                        <TableHead className="sticky right-0 bg-card text-right shadow-[-4px_0_6px_-2px_rgb(var(--border))]">Actions</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {nodes.map((node) => {
                        const canDrain = node.status === 'online' || node.status === 'unhealthy';
                        const canUndrain = node.status === 'draining' || node.status === 'drained';
                        return (
                          <TableRow key={node.id} className="cursor-pointer" onClick={() => router.push(`/nodes/${node.id}`)}>
                            <TableCell>
                              <div className="flex flex-col gap-1">
                                <Link href={`/nodes/${node.id}`} className="font-semibold hover:underline" onClick={(e) => e.stopPropagation()}>
                                  {node.name}
                                </Link>
                                <span className="font-mono text-[11px] text-muted-foreground">
                                  {node.ip_address ?? node.hostname ?? '—'}
                                  {clusterName(node.cluster_id) && <span className="font-sans"> · {clusterName(node.cluster_id)}</span>}
                                </span>
                                <LabelChips labels={node.labels} max={3} />
                              </div>
                            </TableCell>
                            <TableCell>
                              <div className="flex flex-wrap items-center gap-1">
                                <NodeStatusBadge node={nodeStateInput(node)} />
                                {!isNodeDetached(nodeStateInput(node)) && node.activity && node.activity !== 'detached' && <ActivityBadge activity={nodeActivity(node.activity, devicesByNode(node.id))} outside={node.outside_processes ?? 0} />}
                              </div>
                              {(node.status_message || node.drain_reason) && (
                                <div className="mt-1 max-w-[200px] truncate text-[11px] text-muted-foreground" title={node.status_message ?? node.drain_reason ?? ''}>
                                  {node.status_message ?? node.drain_reason}
                                </div>
                              )}
                            </TableCell>
                            <TableCell className="text-xs">
                              <div>{node.os ? NODE_OS_LABELS[node.os] : '—'}</div>
                              <div className="font-mono text-[11px] text-muted-foreground">{node.arch ?? ''}</div>
                            </TableCell>
                            <TableCell>
                              <div className="font-mono text-[11px]">{node.agent_version ?? '—'}</div>
                              <div className="text-[11px] text-muted-foreground">protocol v{node.protocol_version ?? '?'}</div>
                            </TableCell>
                            <TableCell className="text-right tabular-nums">{node.device_count ?? 0}</TableCell>
                            <TableCell className="text-right tabular-nums">{node.instance_count ?? 0}</TableCell>
                            <TableCell>
                              {node.cache_limit_bytes ? (
                                <VramMeter used={node.cache_used_bytes ?? 0} total={node.cache_limit_bytes} />
                              ) : (
                                <span className="text-xs text-muted-foreground">{node.cache_used_bytes ? formatBytes(node.cache_used_bytes) : '—'}</span>
                              )}
                            </TableCell>
                            <TableCell className="text-xs">
                              <Heartbeat at={node.last_heartbeat_at} />
                            </TableCell>
                            <TableCell className="sticky right-0 bg-card text-right shadow-[-4px_0_6px_-2px_rgb(var(--border))]">
                              <DropdownMenu>
                                <DropdownMenuTrigger
                                  onClick={(e) => e.stopPropagation()}
                                  className="inline-flex h-8 w-8 cursor-pointer items-center justify-center rounded-md hover:bg-accent"
                                  aria-label={`Actions for ${node.name}`}
                                >
                                  <MoreVertical className="h-4 w-4" />
                                </DropdownMenuTrigger>
                                <DropdownMenuContent align="end" onClick={(e) => e.stopPropagation()}>
                                  <DropdownMenuItem onClick={() => router.push(`/nodes/${node.id}`)}>
                                    <ExternalLink className="mr-2 h-4 w-4" />
                                    View details
                                  </DropdownMenuItem>
                                  <DropdownMenuItem onClick={() => setEditing(node)}>
                                    <Pencil className="mr-2 h-4 w-4" />
                                    Edit name and labels
                                  </DropdownMenuItem>
                                  {canDrain && (
                                    <DropdownMenuItem onClick={() => setDraining(node)}>
                                      <ArrowDownToLine className="mr-2 h-4 w-4" />
                                      Drain
                                    </DropdownMenuItem>
                                  )}
                                  {canUndrain && (
                                    <DropdownMenuItem onClick={() => undrain.mutate({ id: node.id })}>
                                      <ArrowUpFromLine className="mr-2 h-4 w-4" />
                                      Undrain
                                    </DropdownMenuItem>
                                  )}
                                  <DropdownMenuItem disabled={node.status === 'offline' || node.status === 'pending'} onClick={() => setRotating(node)}>
                                    <RotateCw className="mr-2 h-4 w-4" />
                                    Rotate certificate
                                  </DropdownMenuItem>
                                  <DropdownMenuSeparator />
                                  <DropdownMenuItem variant="destructive" onClick={() => setRemoving(node)}>
                                    <Trash2 className="mr-2 h-4 w-4" />
                                    Remove from fleet
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
                  noun="nodes"
                  onPageChange={(page) => setParams({ page })}
                  onPageSizeChange={(pageSize) => setParams({ pageSize, page: 1 })}
                />
              </>
            )}
          </CardContent>
        </Card>
      )}

      {draining && <DrainNodeDialog key={draining.id} node={draining} open onOpenChange={(o) => !o && setDraining(null)} />}
      {editing && <EditNodeDialog key={editing.id} node={editing} open onOpenChange={(o) => !o && setEditing(null)} />}
      {removing && <RemoveNodeDialog key={removing.id} node={removing} open onOpenChange={(o) => !o && setRemoving(null)} />}
      <ConfirmationDialog
        open={!!rotating}
        onOpenChange={(o) => !o && setRotating(null)}
        title={`Rotate ${rotating?.name ?? ''}'s certificate?`}
        description="The hub asks the agent for a new CSR, signs it and sends it back. The agent reconnects with the new certificate; the old one is revoked 10 minutes later."
        confirmText="Rotate"
        isLoading={rotate.isPending}
        onConfirm={() => rotating && rotate.mutate(rotating.id)}
      />
    </PageContainer>
  );
}
