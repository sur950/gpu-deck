'use client';

import { useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { Cog, Download, ExternalLink, HardDrive, Layers, Library, MoreVertical, Plus, Rocket, ShieldCheck, Trash2 } from 'lucide-react';
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
import { VERSION_BADGE } from '@/components/fleet/status';
import { FormatBadge, ModelTypeBadge, PriorityBadge, SecurityFlags, SourceBadge } from '@/components/models/model-badges';
import { DeleteModelDialog } from '@/components/models/delete-model-dialog';
import { PrefetchDialog } from '@/components/models/prefetch-dialog';
import { useModels } from '@/hooks/api/models';
import { useUrlState } from '@/hooks/use-url-state';
import { useDebounce } from '@/hooks/use-debounce';
import { formatBytes } from '@/utils/format';
import {
  MODEL_PRIORITY_LABELS,
  MODEL_PRIORITY_OPTIONS,
  MODEL_SOURCE_LABELS,
  MODEL_TYPE_OPTIONS,
  MODEL_VERSION_STATUS_LABELS,
  type Model,
  type ModelPriority,
  type ModelSource,
  type ModelType,
} from '@/types/models';

const DEFAULTS = { search: '', type: '', source: '', priority: '', sort: '-created_at', page: 1, pageSize: 25 };

function sortField(sort: string) {
  return sort.startsWith('-') ? sort.slice(1) : sort;
}

function formatParams(n: number | null | undefined): string | null {
  if (!n) return null;
  if (n >= 1e9) return `${(n / 1e9).toFixed(n >= 1e10 ? 0 : 1)}B`;
  return `${Math.round(n / 1e6)}M`;
}

export function CatalogList() {
  const router = useRouter();
  const { params, setParams } = useUrlState(DEFAULTS);
  const [search, setSearch] = useState(params.search);
  const debouncedSearch = useDebounce(search, 300);
  const [deleting, setDeleting] = useState<Model | null>(null);
  const [prefetching, setPrefetching] = useState<Model | null>(null);

  const urlSearch = params.search;
  useEffect(() => {
    if (debouncedSearch !== urlSearch) setParams({ search: debouncedSearch, page: 1 });
  }, [debouncedSearch, urlSearch, setParams]);

  const query = useMemo(
    () => ({
      search: debouncedSearch,
      type: params.type as ModelType | '',
      source: params.source as ModelSource | '',
      priority: params.priority as ModelPriority | '',
      sort: params.sort,
      page: params.page,
      page_size: params.pageSize,
    }),
    [debouncedSearch, params.type, params.source, params.priority, params.sort, params.page, params.pageSize],
  );
  const { data, isLoading, isError, refetch } = useModels(query);
  const all = useModels({ page_size: 500 });

  const models = data?.items ?? [];
  const total = data?.meta.total ?? 0;
  const everything = all.data?.items ?? [];
  const versions = everything.reduce((s, m) => s + (m.version_count ?? 0), 0);
  const weights = everything.reduce((s, m) => s + (m.default_version?.total_size_bytes ?? 0), 0);
  const alwaysOn = everything.filter((m) => m.priority === 'always_on').length;
  const flagged = everything.filter((m) => m.trust_remote_code || m.allow_pickle || m.default_version?.status === 'blocked').length;

  function toggleSort(field: string) {
    const current = sortField(params.sort);
    const desc = params.sort.startsWith('-');
    setParams({ sort: current === field && !desc ? `-${field}` : field, page: 1 });
  }

  if (isLoading && !data) return <ListPageSkeleton />;

  const sortDir = params.sort.startsWith('-') ? 'desc' : 'asc';
  const filtered = !!debouncedSearch || !!params.type || !!params.source || !!params.priority;

  return (
    <PageContainer>
      <PageHeader
        title="Catalog"
        description="Models from Hugging Face, ModelScope and local paths — versions, files, hashes, priority and security defaults."
        actions={
          <>
            <Button variant="outline" size="sm" className="rounded-sm" onClick={() => router.push('/catalog/engines')}>
              <Cog className="size-4" />
              Engines
            </Button>
            <Button size="sm" className="rounded-sm" onClick={() => router.push('/catalog/new')}>
              <Plus className="size-4" />
              Add model
            </Button>
          </>
        }
      />

      <div className="grid gap-4 md:grid-cols-4">
        <StatCard label="Models" value={everything.length} icon={Library} footnote={`${versions} version${versions === 1 ? '' : 's'} registered`} />
        <StatCard label="Weights (default versions)" value={formatBytes(weights)} icon={HardDrive} footnote="Downloaded once per node into the cache" />
        <StatCard label="Always on" value={alwaysOn} icon={Layers} footnote="Never unloaded by rules or make-room" />
        <StatCard label="Security flags" value={flagged} icon={ShieldCheck} footnote={flagged ? 'Remote code, pickle or blocked weights' : 'All models on safe defaults'} />
      </div>

      {isError ? (
        <ErrorState title="Could not load the catalog" description="The control plane did not answer. Retry in a moment." onRetry={() => void refetch()} />
      ) : (
        <Card className="rounded-md shadow-premium">
          <CardHeader>
            <div className="flex flex-wrap items-center gap-3">
              <SearchInput value={search} onChange={setSearch} placeholder="Search name, repo, family or alias…" />
              <Select value={params.type || 'all'} onValueChange={(v) => setParams({ type: v === 'all' ? '' : String(v), page: 1 })}>
                <SelectTrigger className="w-40">
                  <SelectValue>{(v: unknown) => MODEL_TYPE_OPTIONS.find((o) => o.value === v)?.label ?? 'All types'}</SelectValue>
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">All types</SelectItem>
                  {MODEL_TYPE_OPTIONS.map((o) => (
                    <SelectItem key={o.value} value={o.value}>
                      {o.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Select value={params.source || 'all'} onValueChange={(v) => setParams({ source: v === 'all' ? '' : String(v), page: 1 })}>
                <SelectTrigger className="w-40">
                  <SelectValue>{(v: unknown) => (v === 'all' ? 'All sources' : MODEL_SOURCE_LABELS[v as ModelSource])}</SelectValue>
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">All sources</SelectItem>
                  {(Object.keys(MODEL_SOURCE_LABELS) as ModelSource[]).map((s) => (
                    <SelectItem key={s} value={s}>
                      {MODEL_SOURCE_LABELS[s]}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Select value={params.priority || 'all'} onValueChange={(v) => setParams({ priority: v === 'all' ? '' : String(v), page: 1 })}>
                <SelectTrigger className="w-40">
                  <SelectValue>{(v: unknown) => (v === 'all' ? 'Any priority' : MODEL_PRIORITY_LABELS[v as ModelPriority])}</SelectValue>
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">Any priority</SelectItem>
                  {MODEL_PRIORITY_OPTIONS.map((o) => (
                    <SelectItem key={o.value} value={o.value}>
                      {o.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </CardHeader>
          <CardContent>
            {models.length === 0 ? (
              <EmptyState
                icon={Library}
                title={filtered ? 'No models match' : 'The catalog is empty'}
                description={filtered ? 'Try a different search or clear the filters.' : 'Search Hugging Face or ModelScope, or register files already on disk (Ollama, LM Studio, plain GGUF / safetensors / MLX).'}
                action={
                  !filtered && (
                    <Button size="sm" className="rounded-sm" onClick={() => router.push('/catalog/new')}>
                      <Plus className="size-4" />
                      Add model
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
                            Model
                            <SortIcon field="name" sortField={sortField(params.sort)} sortDirection={sortDir} />
                          </div>
                        </TableHead>
                        <TableHead>Type</TableHead>
                        <TableHead>Source</TableHead>
                        <TableHead>Default version</TableHead>
                        <TableHead>Priority</TableHead>
                        <TableHead className="text-right">Deployments</TableHead>
                        <TableHead>Security</TableHead>
                        <TableHead className="w-10" />
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {models.map((m) => {
                        const v = m.default_version;
                        return (
                          <TableRow key={m.id} className="cursor-pointer" onClick={() => router.push(`/catalog/${m.id}`)}>
                            <TableCell>
                              <Link href={`/catalog/${m.id}`} className="font-semibold hover:underline" onClick={(e) => e.stopPropagation()}>
                                {m.display_name ?? m.name}
                              </Link>
                              <div className="font-mono text-[11px] text-muted-foreground">
                                {m.name}
                                {formatParams(v?.parameter_count) && <span className="font-sans"> · {formatParams(v?.parameter_count)} params</span>}
                                {m.family && <span className="font-sans"> · {m.family}</span>}
                              </div>
                            </TableCell>
                            <TableCell>
                              <ModelTypeBadge type={m.type} />
                            </TableCell>
                            <TableCell>
                              <SourceBadge source={m.source} withLabel={false} />
                              <div className="mt-0.5 max-w-[220px] truncate font-mono text-[10.5px] text-muted-foreground" title={m.repo_id}>
                                {m.repo_id}
                              </div>
                            </TableCell>
                            <TableCell>
                              {v ? (
                                <div className="space-y-1">
                                  <div className="flex items-center gap-1.5">
                                    <FormatBadge format={v.format} />
                                    <span className="font-mono text-[11px]">{v.quantization ?? v.version}</span>
                                    <span className="text-[11px] text-muted-foreground tabular-nums">{formatBytes(v.total_size_bytes)}</span>
                                  </div>
                                  {v.status !== 'ready' && <StatusBadge variant={VERSION_BADGE[v.status]} className="py-0">{MODEL_VERSION_STATUS_LABELS[v.status]}</StatusBadge>}
                                </div>
                              ) : (
                                <span className="text-xs text-muted-foreground">—</span>
                              )}
                              {(m.version_count ?? 0) > 1 && <div className="text-[10.5px] text-muted-foreground">+{(m.version_count ?? 1) - 1} more</div>}
                            </TableCell>
                            <TableCell>
                              <PriorityBadge priority={m.priority} />
                            </TableCell>
                            <TableCell className="text-right tabular-nums">{m.deployment_count ?? 0}</TableCell>
                            <TableCell>
                              <SecurityFlags trustRemoteCode={m.trust_remote_code} allowPickle={m.allow_pickle} />
                            </TableCell>
                            <TableCell>
                              <DropdownMenu>
                                <DropdownMenuTrigger onClick={(e) => e.stopPropagation()} className="inline-flex h-8 w-8 cursor-pointer items-center justify-center rounded-md hover:bg-accent" aria-label={`Actions for ${m.name}`}>
                                  <MoreVertical className="h-4 w-4" />
                                </DropdownMenuTrigger>
                                <DropdownMenuContent align="end" onClick={(e) => e.stopPropagation()}>
                                  <DropdownMenuItem onClick={() => router.push(`/catalog/${m.id}`)}>
                                    <ExternalLink className="mr-2 h-4 w-4" />
                                    View details
                                  </DropdownMenuItem>
                                  <DropdownMenuItem disabled={v?.status === 'blocked'} onClick={() => router.push(`/deployments/new?model_id=${m.id}`)}>
                                    <Rocket className="mr-2 h-4 w-4" />
                                    Deploy
                                  </DropdownMenuItem>
                                  <DropdownMenuItem disabled={!v || v.status === 'blocked'} onClick={() => setPrefetching(m)}>
                                    <Download className="mr-2 h-4 w-4" />
                                    Prefetch to nodes
                                  </DropdownMenuItem>
                                  <DropdownMenuSeparator />
                                  <DropdownMenuItem variant="destructive" onClick={() => setDeleting(m)}>
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
                  noun="models"
                  onPageChange={(page) => setParams({ page })}
                  onPageSizeChange={(pageSize) => setParams({ pageSize, page: 1 })}
                />
              </>
            )}
          </CardContent>
        </Card>
      )}

      {deleting && <DeleteModelDialog key={deleting.id} model={deleting} open onOpenChange={(o) => !o && setDeleting(null)} />}
      {prefetching?.default_version && (
        <PrefetchDialog key={prefetching.id} open onOpenChange={(o) => !o && setPrefetching(null)} preset={{ modelId: prefetching.id, versionId: prefetching.default_version.id }} />
      )}
    </PageContainer>
  );
}
