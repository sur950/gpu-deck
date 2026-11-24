'use client';

import { useMemo, useState } from 'react';
import Link from 'next/link';
import { Download, Eraser, HardDrive, MoreVertical, Pin, PinOff, Trash2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Checkbox } from '@/components/ui/checkbox';
import { Input } from '@/components/ui/input';
import { Switch } from '@/components/ui/switch';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from '@/components/ui/dropdown-menu';
import { PageContainer } from '@/components/shared/PageContainer';
import { PageHeader } from '@/components/shared/PageHeader';
import { ListPageSkeleton } from '@/components/shared/ListPageSkeleton';
import { ErrorState } from '@/components/shared/ErrorState';
import { EmptyState } from '@/components/shared/EmptyState';
import { StatusBadge } from '@/components/shared/StatusBadge';
import { TablePagination } from '@/components/shared/table-pagination';
import { ConfirmationDialog } from '@/components/shared/ConfirmationDialog';
import { FormDialog, Field } from '@/components/shared/form-dialog';
import { CACHE_BADGE } from '@/components/fleet/status';
import { PrefetchDialog } from '@/components/models/prefetch-dialog';
import { useCacheItems, useCacheSummary, useDeleteCacheItem, useEvictCache, usePinCacheItem, useUnpinCacheItem } from '@/hooks/api/models-cache';
import { useUrlState } from '@/hooks/use-url-state';
import { cn } from '@/lib/utils';
import { formatBytes, formatRelativeTime } from '@/utils/format';
import { CACHE_STATE_LABELS, type CacheItem, type CacheState, type CacheSummary } from '@/types/models';

const DEFAULTS = { node: '', state: '', pinned: '', unused: 0, page: 1, pageSize: 50 };
const GiB = 1024 ** 3;

/** Disk bar: pinned share (dark), other cached files, and the remaining room up to the limit. */
function CacheBar({ s }: { s: CacheSummary }) {
  const limit = s.limit_bytes ?? s.used_bytes + (s.free_disk_bytes ?? 0);
  const pct = (b: number) => `${limit ? Math.min(100, (b / limit) * 100) : 0}%`;
  const pinned = s.pinned_bytes ?? 0;
  const used = s.used_bytes;
  const ratio = limit ? used / limit : 0;
  return (
    <div className="space-y-1.5">
      <div className="flex h-3 overflow-hidden rounded-[5px] bg-muted" role="meter" aria-label={`${formatBytes(used)} of ${formatBytes(limit)} used`} aria-valuenow={Math.round(ratio * 100)} aria-valuemin={0} aria-valuemax={100}>
        <div className="h-full bg-chart-4" style={{ width: pct(pinned) }} title={`${formatBytes(pinned)} pinned`} />
        <div className={cn('h-full', ratio > 0.9 ? 'bg-error' : ratio > 0.75 ? 'bg-warning' : 'bg-chart-2')} style={{ width: pct(Math.max(0, used - pinned)) }} title={`${formatBytes(used - pinned)} evictable`} />
      </div>
      <div className="flex items-center justify-between text-[11px] tabular-nums text-muted-foreground">
        <span>
          <span className="font-medium text-foreground">{formatBytes(used)}</span> of {s.limit_bytes ? formatBytes(s.limit_bytes) : 'no limit'}
        </span>
        <span>{s.free_disk_bytes !== null && s.free_disk_bytes !== undefined ? `${formatBytes(s.free_disk_bytes)} free on disk` : ''}</span>
      </div>
      <div className="flex gap-3 text-[10.5px] text-muted-foreground">
        <span className="inline-flex items-center gap-1"><span className="size-2 rounded-[2px] bg-chart-4" />pinned {formatBytes(pinned)}</span>
        <span className="inline-flex items-center gap-1"><span className="size-2 rounded-[2px] bg-chart-2" />evictable {formatBytes(Math.max(0, used - pinned))}</span>
      </div>
    </div>
  );
}

function EvictDialog({ nodes, open, onOpenChange, presetNode }: { nodes: CacheSummary[]; open: boolean; onOpenChange: (o: boolean) => void; presetNode?: string }) {
  const evict = useEvictCache();
  const [nodeIds, setNodeIds] = useState<string[]>(presetNode ? [presetNode] : nodes.map((n) => n.node_id));
  const [mode, setMode] = useState<'older' | 'free'>('older');
  const [hours, setHours] = useState('72');
  const [freeGiB, setFreeGiB] = useState('50');
  const [force, setForce] = useState(false);
  return (
    <FormDialog
      open={open}
      onOpenChange={onOpenChange}
      icon={<Eraser className="size-4 text-muted-foreground" />}
      title="Clean up the cache"
      description="Evict files nobody used recently. Pinned files and files a running instance uses are skipped unless you force it. Ollama / LM Studio files referenced in place are never deleted."
      submitLabel="Evict"
      variant="destructive"
      isPending={evict.isPending}
      submitDisabled={nodeIds.length === 0}
      onSubmit={() =>
        evict.mutate(
          {
            node_ids: nodeIds,
            unpinned_older_than_seconds: mode === 'older' ? Math.round(Number(hours) * 3600) : null,
            target_free_bytes: mode === 'free' ? Math.round(Number(freeGiB) * GiB) : null,
            force,
            unpin_only: false,
          },
          { onSuccess: () => onOpenChange(false) },
        )
      }
    >
      <Field label="Nodes">
        <div className="flex flex-wrap gap-1.5">
          {nodes.map((n) => {
            const on = nodeIds.includes(n.node_id);
            return (
              <button key={n.node_id} type="button" aria-pressed={on} onClick={() => setNodeIds((ids) => (on ? ids.filter((x) => x !== n.node_id) : [...ids, n.node_id]))} className={cn('rounded-md border px-2 py-1 text-xs', on ? 'border-foreground/60 bg-muted font-medium' : 'border-border text-muted-foreground')}>
                {n.node_name}
              </button>
            );
          })}
        </div>
      </Field>
      <div className="grid gap-2 sm:grid-cols-2">
        {(['older', 'free'] as const).map((m) => (
          <button key={m} type="button" onClick={() => setMode(m)} className={cn('rounded-lg border p-3 text-left', mode === m ? 'border-foreground/60 bg-muted/40 ring-1 ring-foreground/50' : 'border-border')}>
            <span className="block text-xs font-medium">{m === 'older' ? 'Unused for a while' : 'Free up space'}</span>
            <span className="block text-[11px] text-muted-foreground">{m === 'older' ? 'Unpinned files not used for N hours.' : 'Oldest unpinned files first until enough is free.'}</span>
          </button>
        ))}
      </div>
      {mode === 'older' ? (
        <Field label="Not used for (hours)" htmlFor="ev-hours">
          <Input id="ev-hours" type="number" min={1} value={hours} onChange={(e) => setHours(e.target.value)} className="w-28 font-mono text-sm" />
        </Field>
      ) : (
        <Field label="Free at least (GiB per node)" htmlFor="ev-free">
          <Input id="ev-free" type="number" min={1} value={freeGiB} onChange={(e) => setFreeGiB(e.target.value)} className="w-28 font-mono text-sm" />
        </Field>
      )}
      <label className="flex items-center justify-between gap-3 rounded-lg border border-border bg-muted/30 p-3 text-xs">
        <span>
          <span className="block font-medium">Force</span>
          <span className="text-muted-foreground">Also evict pinned files. Files in use by a running instance are never removed.</span>
        </span>
        <Switch checked={force} onCheckedChange={setForce} aria-label="Force" />
      </label>
    </FormDialog>
  );
}

export function ModelCachePage() {
  const { params, setParams } = useUrlState(DEFAULTS);
  const summary = useCacheSummary();
  const query = useMemo(
    () => ({
      node_id: params.node || undefined,
      state: params.state as CacheState | '',
      pinned: params.pinned === '' ? ('' as const) : params.pinned === 'true',
      unused_for_hours: params.unused || undefined,
      sort: '-size_bytes',
      page: params.page,
      page_size: params.pageSize,
    }),
    [params.node, params.state, params.pinned, params.unused, params.page, params.pageSize],
  );
  const items = useCacheItems(query);
  const pin = usePinCacheItem();
  const unpin = useUnpinCacheItem();
  const remove = useDeleteCacheItem();
  const [prefetchOpen, setPrefetchOpen] = useState(false);
  const [evictOpen, setEvictOpen] = useState(false);
  const [evicting, setEvicting] = useState<CacheItem | null>(null);
  const [forceEvict, setForceEvict] = useState(false);

  if (summary.isLoading && !summary.data) return <ListPageSkeleton stats={2} />;
  const nodes = summary.data ?? [];
  const list = items.data?.items ?? [];
  const filtered = !!params.node || !!params.state || !!params.pinned || !!params.unused;

  return (
    <PageContainer>
      <PageHeader
        title="Model cache"
        description="Weights kept on each node's disk so a swap only costs load time, not download time."
        actions={
          <>
            <Button variant="outline" size="sm" className="rounded-sm" onClick={() => setEvictOpen(true)} disabled={nodes.length === 0}>
              <Eraser className="size-4" />
              Clean up
            </Button>
            <Button size="sm" className="rounded-sm" onClick={() => setPrefetchOpen(true)}>
              <Download className="size-4" />
              Prefetch
            </Button>
          </>
        }
      />

      {summary.isError ? (
        <ErrorState title="Could not load the cache summary" description="The control plane did not answer." onRetry={() => void summary.refetch()} />
      ) : (
        <div className="grid gap-4 lg:grid-cols-2">
          {nodes.map((s) => {
            const active = params.node === s.node_id;
            return (
              <Card key={s.node_id} className={cn('rounded-md shadow-premium transition-colors', active && 'ring-1 ring-foreground/50')}>
                <CardHeader className="flex flex-row items-start justify-between gap-3">
                  <div>
                    <CardTitle className="flex items-center gap-2">
                      <HardDrive className="size-4 text-muted-foreground" />
                      <Link href={`/nodes/${s.node_id}?tab=cache`} className="hover:underline">
                        {s.node_name}
                      </Link>
                    </CardTitle>
                    <CardDescription className="font-mono text-[11px]">{s.cache_dir ?? '—'}</CardDescription>
                  </div>
                  <Button variant={active ? 'secondary' : 'ghost'} size="xs" onClick={() => setParams({ node: active ? '' : s.node_id, page: 1 })}>
                    {active ? 'Showing files' : `${s.item_count} files`}
                  </Button>
                </CardHeader>
                <CardContent className="space-y-4">
                  <CacheBar s={s} />
                  <ul className="divide-y divide-border rounded-lg border border-border">
                    {[...s.by_model].sort((a, b) => (b.size_bytes ?? 0) - (a.size_bytes ?? 0)).slice(0, 5).map((m) => (
                      <li key={m.model_version_id} className="flex items-center gap-3 px-3 py-1.5 text-xs">
                        <span className="min-w-0 flex-1 truncate">
                          {m.model_name} <span className="font-mono text-[10.5px] text-muted-foreground">{m.version}</span>
                        </span>
                        {m.in_use && <span className="text-[10.5px] text-success-dark dark:text-success-light">in use</span>}
                        {m.pinned && <Pin className="size-3 text-chart-4" aria-label="pinned" />}
                        {m.state === 'downloading' && <span className="text-[10.5px] text-info">downloading</span>}
                        <span className="w-28 text-right text-[10.5px] text-muted-foreground">{m.last_used_at ? formatRelativeTime(m.last_used_at) : 'never used'}</span>
                        <span className="w-16 text-right font-mono text-[11px] tabular-nums">{formatBytes(m.size_bytes ?? 0)}</span>
                      </li>
                    ))}
                    {s.by_model.length === 0 && <li className="px-3 py-3 text-center text-xs text-muted-foreground">Empty</li>}
                  </ul>
                </CardContent>
              </Card>
            );
          })}
        </div>
      )}

      <Card className="rounded-md shadow-premium">
        <CardHeader>
          <div className="flex flex-wrap items-center gap-3">
            <Select value={params.node || 'all'} onValueChange={(v) => setParams({ node: v === 'all' ? '' : String(v), page: 1 })}>
              <SelectTrigger className="w-48">
                <SelectValue>{(v: unknown) => (v === 'all' ? 'All nodes' : nodes.find((n) => n.node_id === v)?.node_name ?? 'Node')}</SelectValue>
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All nodes</SelectItem>
                {nodes.map((n) => (
                  <SelectItem key={n.node_id} value={n.node_id}>
                    {n.node_name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Select value={params.state || 'all'} onValueChange={(v) => setParams({ state: v === 'all' ? '' : String(v), page: 1 })}>
              <SelectTrigger className="w-40">
                <SelectValue>{(v: unknown) => (v === 'all' ? 'Any state' : CACHE_STATE_LABELS[v as CacheState])}</SelectValue>
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">Any state</SelectItem>
                {(Object.keys(CACHE_STATE_LABELS) as CacheState[]).map((s) => (
                  <SelectItem key={s} value={s}>
                    {CACHE_STATE_LABELS[s]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Select value={params.pinned || 'all'} onValueChange={(v) => setParams({ pinned: v === 'all' ? '' : String(v), page: 1 })}>
              <SelectTrigger className="w-36">
                <SelectValue>{(v: unknown) => (v === 'all' ? 'Pinned or not' : v === 'true' ? 'Pinned' : 'Not pinned')}</SelectValue>
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">Pinned or not</SelectItem>
                <SelectItem value="true">Pinned</SelectItem>
                <SelectItem value="false">Not pinned</SelectItem>
              </SelectContent>
            </Select>
            <Select value={String(params.unused || 0)} onValueChange={(v) => setParams({ unused: Number(v), page: 1 })}>
              <SelectTrigger className="w-44">
                <SelectValue>{(v: unknown) => (v === '0' ? 'Any last use' : `Unused for ${Number(v) >= 24 ? `${Number(v) / 24} d` : `${String(v)} h`}`)}</SelectValue>
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="0">Any last use</SelectItem>
                <SelectItem value="1">Unused for 1 h</SelectItem>
                <SelectItem value="24">Unused for 1 d</SelectItem>
                <SelectItem value="72">Unused for 3 d</SelectItem>
                <SelectItem value="168">Unused for 7 d</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </CardHeader>
        <CardContent>
          {items.isError ? (
            <ErrorState title="Could not load cache items" onRetry={() => void items.refetch()} />
          ) : list.length === 0 ? (
            <EmptyState icon={HardDrive} title={filtered ? 'No files match' : 'Nothing cached yet'} description={filtered ? 'Clear the filters to see every file.' : 'Prefetch a model version to the nodes that will serve it.'} />
          ) : (
            <>
              <div className="overflow-x-auto">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Model · file</TableHead>
                      <TableHead>Node</TableHead>
                      <TableHead className="text-right">Size</TableHead>
                      <TableHead className="min-w-[140px]">State</TableHead>
                      <TableHead>Last used</TableHead>
                      <TableHead className="w-10" />
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {list.map((item) => (
                      <TableRow key={item.id}>
                        <TableCell>
                          <Link href={`/catalog/${item.model_id}`} className="font-medium hover:underline">
                            {item.model_name}
                          </Link>{' '}
                          <span className="font-mono text-[10.5px] text-muted-foreground">{item.version}</span>
                          <div className="max-w-[420px] truncate font-mono text-[10.5px] text-muted-foreground" title={item.local_path}>
                            {item.path ?? item.local_path}
                            <span className="opacity-60"> · {item.local_path.slice(0, item.local_path.length - (item.path?.length ?? 0)).replace(/\/$/, '')}</span>
                          </div>
                        </TableCell>
                        <TableCell className="text-xs">{item.node_name}</TableCell>
                        <TableCell className="text-right font-mono text-xs tabular-nums">{formatBytes(item.size_bytes)}</TableCell>
                        <TableCell>
                          {item.state === 'downloading' ? (
                            <div className="space-y-1">
                              <div className="flex justify-between text-[11px]">
                                <span className="text-info">Downloading</span>
                                <span className="font-mono tabular-nums">{Math.round(item.progress_pct ?? 0)}%</span>
                              </div>
                              <div className="h-1 overflow-hidden rounded-full bg-muted">
                                <div className="h-full rounded-full bg-info transition-[width] duration-700" style={{ width: `${item.progress_pct ?? 0}%` }} />
                              </div>
                            </div>
                          ) : (
                            <div className="flex flex-wrap items-center gap-1">
                              <StatusBadge variant={CACHE_BADGE[item.state]} className="py-0">{CACHE_STATE_LABELS[item.state]}</StatusBadge>
                              {item.in_use && <StatusBadge variant="running" className="py-0">in use</StatusBadge>}
                              {item.pinned && (
                                <span className="inline-flex items-center gap-0.5 text-[10.5px] text-muted-foreground" title={item.pinned_reason ?? 'pinned'}>
                                  <Pin className="size-3 text-chart-4" />
                                  {item.pinned_reason ?? 'pinned'}
                                </span>
                              )}
                            </div>
                          )}
                        </TableCell>
                        <TableCell className="text-xs text-muted-foreground">{item.last_used_at ? formatRelativeTime(item.last_used_at) : 'never'}</TableCell>
                        <TableCell>
                          <DropdownMenu>
                            <DropdownMenuTrigger className="inline-flex h-8 w-8 cursor-pointer items-center justify-center rounded-md hover:bg-accent" aria-label="Cache item actions">
                              <MoreVertical className="h-4 w-4" />
                            </DropdownMenuTrigger>
                            <DropdownMenuContent align="end">
                              {item.pinned ? (
                                <DropdownMenuItem onClick={() => unpin.mutate({ id: item.id })}>
                                  <PinOff className="mr-2 h-4 w-4" />
                                  Unpin
                                </DropdownMenuItem>
                              ) : (
                                <DropdownMenuItem onClick={() => pin.mutate({ id: item.id, reason: 'Pinned by admin' })} disabled={item.state !== 'ready'}>
                                  <Pin className="mr-2 h-4 w-4" />
                                  Pin (never auto-evict)
                                </DropdownMenuItem>
                              )}
                              <DropdownMenuSeparator />
                              <DropdownMenuItem variant="destructive" disabled={item.state === 'evicting' || item.state === 'evicted'} onClick={() => { setForceEvict(false); setEvicting(item); }}>
                                <Trash2 className="mr-2 h-4 w-4" />
                                Evict
                              </DropdownMenuItem>
                            </DropdownMenuContent>
                          </DropdownMenu>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
              <TablePagination page={params.page} pageSize={params.pageSize} total={items.data?.meta.total ?? 0} noun="files" onPageChange={(page) => setParams({ page })} onPageSizeChange={(pageSize) => setParams({ pageSize, page: 1 })} />
            </>
          )}
        </CardContent>
      </Card>

      {prefetchOpen && <PrefetchDialog open onOpenChange={setPrefetchOpen} preset={{ nodeIds: params.node ? [params.node] : [] }} />}
      {evictOpen && <EvictDialog nodes={nodes} open onOpenChange={setEvictOpen} presetNode={params.node || undefined} />}
      {evicting?.in_use ? (
        <FormDialog
          open
          onOpenChange={(o) => !o && setEvicting(null)}
          title="This file is in use"
          description={`A running instance on ${evicting.node_name} maps ${evicting.path ?? 'this file'}. Evicting it now breaks that instance's next reload.`}
          submitLabel="Force evict"
          variant="destructive"
          isPending={remove.isPending}
          submitDisabled={!forceEvict}
          onSubmit={() => remove.mutate({ id: evicting.id, force: true }, { onSuccess: () => setEvicting(null) })}
        >
          <label className="flex items-center gap-2 text-xs">
            <Checkbox checked={forceEvict} onCheckedChange={(v) => setForceEvict(v === true)} />I understand — evict anyway
          </label>
        </FormDialog>
      ) : (
        <ConfirmationDialog
          open={!!evicting}
          onOpenChange={(o) => !o && setEvicting(null)}
          title="Evict this file?"
          description={`${evicting?.model_name ?? ''} ${evicting?.version ?? ''} (${formatBytes(evicting?.size_bytes ?? 0)}) is removed from ${evicting?.node_name ?? 'the node'}. It is downloaded again the next time it is needed.`}
          confirmText="Evict"
          variant="destructive"
          isLoading={remove.isPending}
          onConfirm={() => evicting && remove.mutate({ id: evicting.id })}
        />
      )}
    </PageContainer>
  );
}
