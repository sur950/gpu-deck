'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { ArrowLeft, ArrowDownToLine, Check, FolderSearch, Heart, HardDrive, KeyRound, Loader2, Lock, Search, ShieldCheck } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Skeleton } from '@/components/ui/skeleton';
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { PageContainer } from '@/components/shared/PageContainer';
import { PageHeader } from '@/components/shared/PageHeader';
import { EmptyState } from '@/components/shared/EmptyState';
import { ErrorState } from '@/components/shared/ErrorState';
import { SearchInput } from '@/components/shared/search-input';
import { FormatBadge, ModelTypeBadge } from '@/components/models/model-badges';
import { RegisterModelPanel, type RegisterDraft } from '@/components/models/register-model-panel';
import { CredentialPicker } from '@/components/vault/credential-picker';
import { useNodes } from '@/hooks/api/fleet';
import { useCatalogSearch, useRemoteFiles, useScanLocalPath } from '@/hooks/api/models';
import { useUrlState } from '@/hooks/use-url-state';
import { useDebounce } from '@/hooks/use-debounce';
import { cn } from '@/lib/utils';
import { formatBytes, formatRelativeTime } from '@/utils/format';
import { MODEL_FORMAT_LABELS, MODEL_TYPE_OPTIONS, type CatalogSearchResult, type LocalScanCandidate, type ModelFileFormat, type ModelType, type RemoteSource } from '@/types/models';

/** `path` + `node` (+ `pick`, the candidate to select) deep-link a local scan (node detail → Files → Register). */
const DEFAULTS = { source: 'huggingface', q: '', type: '', format: '', sort: 'downloads', path: '', node: '', pick: '' };

const SCAN_ROOTS = [
  { path: '~/.ollama/models', label: 'Ollama' },
  { path: '~/.lmstudio/models', label: 'LM Studio' },
  { path: '~/.cache/huggingface/hub', label: 'HF cache' },
  { path: '/models', label: '/models' },
];

const KIND_BADGE: Record<LocalScanCandidate['kind'], string> = {
  plain: 'plain',
  ollama_blob: 'Ollama',
  lmstudio: 'LM Studio',
  hf_cache: 'HF cache',
  mlx_dir: 'MLX',
};

function compact(n: number | null | undefined): string {
  if (n === null || n === undefined) return '—';
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(n >= 1e4 ? 0 : 1)}k`;
  return String(n);
}

function Guidance() {
  return (
    <Card className="rounded-md border-dashed shadow-none">
      <CardContent className="space-y-4 py-2">
        <div className="flex size-10 items-center justify-center rounded-xl border border-border bg-muted/60">
          <ShieldCheck className="size-4 text-muted-foreground" />
        </div>
        <div className="space-y-1">
          <h3 className="text-sm font-semibold">Pick a result to register it</h3>
          <p className="text-[13px] leading-relaxed text-muted-foreground">
            Registering only records the model and its files — nothing is downloaded until you deploy or prefetch it to a node.
          </p>
        </div>
        <ul className="space-y-2 text-[12px] text-muted-foreground">
          <li className="flex gap-2"><Check className="mt-0.5 size-3.5 shrink-0 text-success" /> Safetensors, GGUF and MLX preferred; pickle weights blocked unless allowed.</li>
          <li className="flex gap-2"><Check className="mt-0.5 size-3.5 shrink-0 text-success" /> trust_remote_code off by default.</li>
          <li className="flex gap-2"><Check className="mt-0.5 size-3.5 shrink-0 text-success" /> Every file gets a SHA-256, checked by the agent after download.</li>
          <li className="flex gap-2"><Check className="mt-0.5 size-3.5 shrink-0 text-success" /> Ollama and LM Studio files are referenced in place, never copied.</li>
        </ul>
      </CardContent>
    </Card>
  );
}

function RemoteDraft({ source, repoId, credentialId }: { source: RemoteSource; repoId: string; credentialId: string }) {
  const files = useRemoteFiles(source, repoId, undefined, credentialId);
  if (files.isLoading) {
    return (
      <Card className="rounded-md shadow-premium">
        <CardContent className="space-y-3">
          <Skeleton className="h-5 w-2/3" />
          <Skeleton className="h-40 w-full" />
          <Skeleton className="h-9 w-full" />
        </CardContent>
      </Card>
    );
  }
  if (files.isError || !files.data) {
    return <ErrorState title="Could not list the repository" description="The source did not answer, or the repo is gated and the access token (picked above, else the default one) lacks access." onRetry={() => void files.refetch()} />;
  }
  const d = files.data;
  const draft: RegisterDraft = {
    source,
    repoId: d.repo_id,
    revision: d.revision ?? null,
    files: d.files,
    totalSize: d.total_size_bytes,
    hasPickleOnly: !!d.has_pickle_only_weights,
    suggested: d.suggested ?? {},
    credentialId: credentialId || null,
  };
  return <RegisterModelPanel key={`${source}:${repoId}:${credentialId}`} draft={draft} />;
}

function RemoteSearch({ source, onPick, picked, credentialId }: { source: RemoteSource; onPick: (r: CatalogSearchResult) => void; picked: string; credentialId: string }) {
  const { params, setParams } = useUrlState(DEFAULTS);
  const [q, setQ] = useState(params.q);
  const debounced = useDebounce(q, 400);
  const search = useCatalogSearch({
    q: debounced,
    source,
    type: params.type as ModelType | '',
    format: params.format as ModelFileFormat | '',
    sort: params.sort as 'downloads' | 'likes' | 'last_modified' | 'relevance',
    limit: 30,
    credential_id: credentialId || undefined,
  });
  const results = search.data?.items ?? [];
  return (
    <Card className="rounded-md shadow-premium">
      <CardHeader>
        <div className="flex flex-wrap items-center gap-2">
          <SearchInput
            value={q}
            onChange={(v) => {
              setQ(v);
              setParams({ q: v });
            }}
            placeholder={source === 'huggingface' ? 'Search Hugging Face — qwen3, gemma-3 gguf, bge-m3…' : 'Search ModelScope — Qwen, iic/SenseVoice…'}
            className="max-w-none flex-1"
          />
          <Select value={params.type || 'all'} onValueChange={(v) => setParams({ type: v === 'all' ? '' : String(v) })}>
            <SelectTrigger size="sm" className="w-32">
              <SelectValue>{(v: unknown) => MODEL_TYPE_OPTIONS.find((o) => o.value === v)?.label ?? 'Any type'}</SelectValue>
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">Any type</SelectItem>
              {MODEL_TYPE_OPTIONS.map((o) => (
                <SelectItem key={o.value} value={o.value}>
                  {o.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select value={params.format || 'all'} onValueChange={(v) => setParams({ format: v === 'all' ? '' : String(v) })}>
            <SelectTrigger size="sm" className="w-32">
              <SelectValue>{(v: unknown) => (v === 'all' ? 'Any format' : MODEL_FORMAT_LABELS[v as ModelFileFormat])}</SelectValue>
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">Any format</SelectItem>
              {(['gguf', 'safetensors', 'mlx'] as ModelFileFormat[]).map((f) => (
                <SelectItem key={f} value={f}>
                  {MODEL_FORMAT_LABELS[f]}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select value={params.sort} onValueChange={(v) => setParams({ sort: String(v) })}>
            <SelectTrigger size="sm" className="w-36">
              <SelectValue>{(v: unknown) => ({ downloads: 'Most downloaded', likes: 'Most liked', last_modified: 'Recently updated', relevance: 'Relevance' })[String(v) as 'downloads'] ?? 'Sort'}</SelectValue>
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="downloads">Most downloaded</SelectItem>
              <SelectItem value="likes">Most liked</SelectItem>
              <SelectItem value="last_modified">Recently updated</SelectItem>
              <SelectItem value="relevance">Relevance</SelectItem>
            </SelectContent>
          </Select>
        </div>
      </CardHeader>
      <CardContent className="px-0">
        {!debounced.trim() ? (
          <EmptyState icon={Search} title={`Search ${source === 'huggingface' ? 'Hugging Face' : 'ModelScope'}`} description="Results show formats, size and whether the repo is already in the catalog. Gated and private repos need an access token (pick one above)." />
        ) : search.isLoading ? (
          <div className="space-y-2 px-4">{Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-14 w-full" />)}</div>
        ) : search.isError ? (
          <div className="px-4">
            <ErrorState title="Search failed" description="The source is unreachable from the control plane (offline site? set HF_ENDPOINT to a mirror)." onRetry={() => void search.refetch()} />
          </div>
        ) : results.length === 0 ? (
          <EmptyState icon={Search} title="No results" description="Try fewer words, or a different type / format filter." />
        ) : (
          <ul className="divide-y divide-border border-t border-border">
            {results.map((r) => {
              const active = picked === r.repo_id;
              return (
                <li key={r.repo_id}>
                  <button
                    type="button"
                    onClick={() => onPick(r)}
                    className={cn('flex w-full items-start gap-3 px-4 py-3 text-left transition-colors hover:bg-muted/40 focus-visible:bg-muted/40 focus-visible:outline-none', active && 'bg-muted/60 shadow-[inset_3px_0_0_rgb(var(--brand))]')}
                  >
                    <div className="min-w-0 flex-1 space-y-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="truncate font-mono text-[13px] font-medium">
                          <span className="text-muted-foreground">{r.author}/</span>
                          {r.name}
                        </span>
                        {r.gated && <Lock className="size-3 text-muted-foreground" aria-label="Gated" />}
                        {r.already_registered_model_id && (
                          <Link href={`/catalog/${r.already_registered_model_id}`} onClick={(e) => e.stopPropagation()} className="rounded-sm border border-success/40 bg-success/10 px-1.5 text-[10.5px] font-medium text-success-dark dark:text-success-light">
                            in catalog
                          </Link>
                        )}
                      </div>
                      <div className="flex flex-wrap items-center gap-1.5">
                        {r.detected_type && <ModelTypeBadge type={r.detected_type} />}
                        {r.formats.map((f) => (
                          <FormatBadge key={f} format={f} />
                        ))}
                        {r.pipeline_tag && <span className="text-[11px] text-muted-foreground">{r.pipeline_tag}</span>}
                      </div>
                    </div>
                    <div className="grid shrink-0 grid-cols-3 gap-4 text-right text-[11px] text-muted-foreground tabular-nums">
                      <span title="Downloads">
                        <ArrowDownToLine className="mr-0.5 inline size-3" />
                        {compact(r.downloads)}
                      </span>
                      <span title="Likes">
                        <Heart className="mr-0.5 inline size-3" />
                        {compact(r.likes)}
                      </span>
                      <span title="Total size">{r.total_size_bytes ? formatBytes(r.total_size_bytes) : '—'}</span>
                      <span className="col-span-3 text-[10.5px]">{r.last_modified ? `updated ${formatRelativeTime(r.last_modified)}` : ''}</span>
                    </div>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

function LocalScan({ onPick, picked }: { onPick: (c: LocalScanCandidate, nodeId: string | null) => void; picked: string }) {
  const { params } = useUrlState(DEFAULTS);
  const nodes = useNodes({ page_size: 200 }, { live: false });
  const scan = useScanLocalPath();
  const [path, setPath] = useState(params.path || '~/.ollama/models');
  const [nodeId, setNodeId] = useState(params.node);
  const candidates = scan.data?.items ?? [];
  const autoRan = useRef(false);

  function run(p = path) {
    setPath(p);
    scan.mutate({ path: p, node_id: nodeId || null, recursive: true, max_depth: 4 });
  }

  // Deep link from a node scan: scan the same path on the same node once, and
  // pre-select the candidate when it is the only one.
  useEffect(() => {
    if (autoRan.current || !params.path) return;
    autoRan.current = true;
    scan.mutate(
      { path: params.path, node_id: params.node || null, recursive: true, max_depth: 4 },
      {
        onSuccess: (res) => {
          const want = params.pick || params.path;
          const match = res.items.find((c) => c.path === want) ?? (res.items.length === 1 ? res.items[0] : undefined);
          if (match) onPick(match, params.node || null);
        },
      },
    );
  }, [params.path, params.node, params.pick, scan, onPick]);

  return (
    <Card className="rounded-md shadow-premium">
      <CardHeader className="space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <Select value={nodeId || 'cp'} onValueChange={(v) => setNodeId(v === 'cp' ? '' : String(v))}>
            <SelectTrigger size="sm" className="w-52">
              <SelectValue>{(v: unknown) => (v === 'cp' ? 'Control plane host' : `On ${nodes.data?.items.find((n) => n.id === v)?.name ?? 'node'}`)}</SelectValue>
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="cp">Control plane host</SelectItem>
              {(nodes.data?.items ?? []).map((n) => (
                <SelectItem key={n.id} value={n.id} disabled={n.status !== 'online'}>
                  On {n.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <form
            className="flex min-w-[260px] flex-1 items-center gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              run();
            }}
          >
            <Input value={path} onChange={(e) => setPath(e.target.value)} placeholder="/models or ~/.ollama/models or /path/model.gguf" className="h-9 font-mono text-xs" aria-label="Path to scan" />
            <Button type="submit" size="sm" className="rounded-sm" disabled={!path.trim() || scan.isPending}>
              {scan.isPending ? <Loader2 className="size-4 animate-spin" /> : <FolderSearch className="size-4" />}
              Scan
            </Button>
          </form>
        </div>
        <div className="flex flex-wrap items-center gap-1.5 text-[11px] text-muted-foreground">
          Known locations:
          {SCAN_ROOTS.map((r) => (
            <button key={r.path} type="button" onClick={() => run(r.path)} className="rounded-sm border border-border px-1.5 py-0.5 font-mono text-[10.5px] hover:border-foreground/30 hover:text-foreground">
              {r.path}
            </button>
          ))}
        </div>
      </CardHeader>
      <CardContent className="px-0">
        {scan.isIdle ? (
          <EmptyState icon={HardDrive} title="Scan a folder or a file" description="Finds GGUF, safetensors and MLX weights, Ollama manifests (resolved to their blobs) and LM Studio folders. Scanning on a node goes through its agent." />
        ) : scan.isPending ? (
          <div className="space-y-2 px-4">{Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} className="h-16 w-full" />)}</div>
        ) : candidates.length === 0 ? (
          <EmptyState icon={FolderSearch} title="No model files found" description="Nothing that looks like weights under that path (searched 4 levels deep)." />
        ) : (
          <ul className="divide-y divide-border border-t border-border">
            {candidates.map((c) => (
              <li key={c.path}>
                <button
                  type="button"
                  onClick={() => onPick(c, nodeId || null)}
                  className={cn('flex w-full items-start gap-3 px-4 py-3 text-left transition-colors hover:bg-muted/40', picked === c.path && 'bg-muted/60 shadow-[inset_3px_0_0_rgb(var(--brand))]')}
                >
                  <div className="min-w-0 flex-1 space-y-1">
                    <div className="flex items-center gap-2">
                      <span className="text-[13px] font-medium">{c.suggested?.name ?? c.path.split('/').pop()}</span>
                      <span className="rounded-sm border border-border px-1.5 text-[10.5px] text-muted-foreground">{KIND_BADGE[c.kind]}</span>
                      <FormatBadge format={c.format} />
                      {c.suggested?.quantization && <span className="font-mono text-[11px] text-muted-foreground">{c.suggested.quantization}</span>}
                    </div>
                    <div className="truncate font-mono text-[10.5px] text-muted-foreground" title={c.path}>
                      {c.path}
                    </div>
                  </div>
                  <div className="shrink-0 text-right text-[11px] text-muted-foreground tabular-nums">
                    <div>{formatBytes(c.total_size_bytes)}</div>
                    <div>{c.files.length} file{c.files.length === 1 ? '' : 's'}</div>
                  </div>
                </button>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

export function AddModel() {
  const router = useRouter();
  const { params, setParams } = useUrlState(DEFAULTS);
  const source = (['huggingface', 'modelscope', 'local_path'].includes(params.source) ? params.source : 'huggingface') as 'huggingface' | 'modelscope' | 'local_path';
  const [pickedRemote, setPickedRemote] = useState<{ source: RemoteSource; repoId: string } | null>(null);
  const [pickedLocal, setPickedLocal] = useState<{ candidate: LocalScanCandidate; nodeId: string | null } | null>(null);
  // Access token credential for the remote source (kept in state: the search box writes the URL too).
  const [token, setToken] = useState<{ source: string; id: string }>({ source: '', id: '' });
  const tokenId = token.source === source ? token.id : '';

  const localDraft = useMemo<RegisterDraft | null>(
    () =>
      pickedLocal
        ? {
            source: 'local_path',
            repoId: pickedLocal.candidate.path,
            files: pickedLocal.candidate.files,
            totalSize: pickedLocal.candidate.total_size_bytes,
            hasPickleOnly: pickedLocal.candidate.format === 'pickle',
            suggested: pickedLocal.candidate.suggested ?? {},
            localKind: pickedLocal.candidate.kind,
            nodeId: pickedLocal.nodeId,
          }
        : null,
    [pickedLocal],
  );

  return (
    <PageContainer>
      <PageHeader
        title="Add a model"
        description="Hugging Face, ModelScope or files already on a disk. Registering records versions, files and hashes; nodes download on demand."
        actions={
          <Button variant="outline" size="sm" className="rounded-sm" onClick={() => router.push('/catalog')}>
            <ArrowLeft className="size-4" />
            Back to catalog
          </Button>
        }
      />
      <div className="flex flex-wrap items-center justify-between gap-3">
        <Tabs value={source} onValueChange={(v) => setParams({ source: String(v) })}>
          <TabsList className="bg-muted">
            <TabsTrigger value="huggingface" className="px-3 text-xs">Hugging Face</TabsTrigger>
            <TabsTrigger value="modelscope" className="px-3 text-xs">ModelScope</TabsTrigger>
            <TabsTrigger value="local_path" className="px-3 text-xs">Local path</TabsTrigger>
          </TabsList>
        </Tabs>
        {source !== 'local_path' && (
          <div className="flex w-full items-center gap-2 sm:w-auto">
            <span className="flex shrink-0 items-center gap-1.5 text-xs font-medium text-muted-foreground">
              <KeyRound className="size-3.5" /> Access token
            </span>
            <div className="min-w-0 flex-1 sm:w-80">
              <CredentialPicker value={tokenId} onChange={(id) => setToken({ source, id })} kinds={[source]} allowNone noneLabel="Default token" placeholder="Default token" />
            </div>
          </div>
        )}
      </div>
      <div className="grid items-start gap-6 xl:grid-cols-[minmax(0,1fr)_440px]">
        {source === 'local_path' ? (
          <LocalScan picked={pickedLocal?.candidate.path ?? ''} onPick={(candidate, nodeId) => setPickedLocal({ candidate, nodeId })} />
        ) : (
          <RemoteSearch key={source} source={source} credentialId={tokenId} picked={pickedRemote?.source === source ? pickedRemote.repoId : ''} onPick={(r) => setPickedRemote({ source, repoId: r.repo_id })} />
        )}
        <div className="xl:sticky xl:top-4">
          {source === 'local_path' ? (
            localDraft ? <RegisterModelPanel key={localDraft.repoId} draft={localDraft} /> : <Guidance />
          ) : pickedRemote && pickedRemote.source === source ? (
            <RemoteDraft source={pickedRemote.source} repoId={pickedRemote.repoId} credentialId={tokenId} />
          ) : (
            <Guidance />
          )}
        </div>
      </div>
    </PageContainer>
  );
}
