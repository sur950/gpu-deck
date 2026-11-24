'use client';

import { useState } from 'react';
import Link from 'next/link';
import { useParams, useRouter } from 'next/navigation';
import {
  ChevronRight,
  CircleCheck,
  Download,
  ExternalLink,
  GitBranchPlus,
  HardDrive,
  Layers,
  Loader2,
  MoreVertical,
  Rocket,
  Save,
  ScanSearch,
  ShieldAlert,
  Star,
  Trash2,
  TriangleAlert,
  XCircle,
} from 'lucide-react';
import { Button, buttonVariants } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Switch } from '@/components/ui/switch';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from '@/components/ui/dropdown-menu';
import { PageContainer } from '@/components/shared/PageContainer';
import { ListPageSkeleton } from '@/components/shared/ListPageSkeleton';
import { ErrorState } from '@/components/shared/ErrorState';
import { EmptyState } from '@/components/shared/EmptyState';
import { StatusBadge } from '@/components/shared/StatusBadge';
import { StatCard } from '@/components/shared/stat-card';
import { Field } from '@/components/shared/form-dialog';
import { CredentialPicker } from '@/components/vault/credential-picker';
import { CopyButton } from '@/components/shared/copy-button';
import { DeleteConfirmationDialog } from '@/components/shared/DeleteConfirmationDialog';
import { KeyValueEditor, recordToRows, rowsToFlags, type KeyValueRow } from '@/components/shared/key-value-editor';
import { COMPAT_BADGE, DEPLOYMENT_BADGE, VERSION_BADGE } from '@/components/fleet/status';
import { EngineBadge, FormatBadge, ModelTypeBadge, PriorityBadge, SecurityFlags, SourceBadge } from '@/components/models/model-badges';
import { AddVersionDialog } from '@/components/models/add-version-dialog';
import { DeleteModelDialog } from '@/components/models/delete-model-dialog';
import { PrefetchDialog } from '@/components/models/prefetch-dialog';
import {
  useCompatCheck,
  useDeleteModelVersion,
  useModel,
  useModelFiles,
  useModelVersionCache,
  useModelVersions,
  useUpdateModel,
  useUpdateModelVersion,
  useVerifyModelVersion,
} from '@/hooks/api/models';
import { useDeployments } from '@/hooks/api/models-deployments';
import { cn } from '@/lib/utils';
import { formatBytes, formatDate, formatRelativeTime } from '@/utils/format';
import {
  DEPLOYMENT_STATUS_LABELS,
  MODEL_PRIORITY_DESCRIPTIONS,
  MODEL_PRIORITY_LABELS,
  MODEL_PRIORITY_OPTIONS,
  MODEL_VERSION_STATUS_LABELS,
  type CompatCheck,
  type Model,
  type ModelPriority,
  type ModelVersion,
} from '@/types/models';

function repoUrl(model: Model): string | null {
  if (model.source === 'huggingface') return `https://huggingface.co/${model.repo_id}`;
  if (model.source === 'modelscope') return `https://modelscope.cn/models/${model.repo_id}`;
  return null;
}

function formatParams(n: number | null | undefined): string {
  if (!n) return '—';
  if (n >= 1e9) return `${(n / 1e9).toFixed(n >= 1e10 ? 0 : 2)}B`;
  return `${Math.round(n / 1e6)}M`;
}

function VersionFiles({ model, version }: { model: Model; version: ModelVersion }) {
  const files = useModelFiles(model.id, version.id);
  const cache = useModelVersionCache(model.id, version.id);
  const byNode = new Map<string, { name: string; ready: number; total: number; downloading: number }>();
  for (const item of cache.data?.items ?? []) {
    const entry = byNode.get(item.node_id) ?? { name: item.node_name ?? item.node_id.slice(0, 8), ready: 0, total: 0, downloading: 0 };
    entry.total += 1;
    if (item.state === 'ready') entry.ready += 1;
    if (item.state === 'downloading') entry.downloading += 1;
    byNode.set(item.node_id, entry);
  }
  return (
    <div className="space-y-3 border-t border-border bg-muted/20 px-4 py-3">
      {files.isLoading ? (
        <p className="text-xs text-muted-foreground">Loading files…</p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>File</TableHead>
              <TableHead>Format</TableHead>
              <TableHead className="text-right">Size</TableHead>
              <TableHead>SHA-256</TableHead>
              <TableHead className="text-right">Cached on</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {files.data?.items.map((f) => (
              <TableRow key={f.id}>
                <TableCell className="max-w-[320px]">
                  <span className="block truncate font-mono text-[11px]" title={f.path}>
                    {f.path}
                  </span>
                  {f.shard_count ? <span className="text-[10.5px] text-muted-foreground">shard {f.shard_index} of {f.shard_count}</span> : null}
                </TableCell>
                <TableCell>
                  <FormatBadge format={f.format} />
                </TableCell>
                <TableCell className="text-right font-mono text-[11px] tabular-nums">{formatBytes(f.size_bytes)}</TableCell>
                <TableCell>
                  {f.sha256 ? (
                    <span className="inline-flex items-center gap-1 font-mono text-[10.5px] text-muted-foreground">
                      {f.sha256.slice(0, 12)}…{f.sha256.slice(-6)}
                      <CopyButton value={f.sha256} successText="SHA-256 copied" />
                    </span>
                  ) : (
                    <span className="text-[11px] text-warning-dark dark:text-warning-light">not hashed yet</span>
                  )}
                </TableCell>
                <TableCell className="text-right text-xs tabular-nums">{f.cached_on_nodes ?? 0} node(s)</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
      <div className="flex flex-wrap items-center gap-2 text-[11px] text-muted-foreground">
        <HardDrive className="size-3.5" />
        {byNode.size === 0
          ? 'Not cached on any node yet.'
          : [...byNode.entries()].map(([nodeId, n]) => (
              <Link key={nodeId} href={`/model-cache?node=${nodeId}`} className="rounded-sm border border-border px-1.5 py-0.5 hover:border-foreground/30 hover:text-foreground">
                {n.name} · {n.downloading ? 'downloading' : n.ready === n.total ? 'ready' : `${n.ready}/${n.total}`}
              </Link>
            ))}
      </div>
    </div>
  );
}

function VersionsCard({ model }: { model: Model }) {
  const versions = useModelVersions(model.id);
  const verify = useVerifyModelVersion();
  const update = useUpdateModelVersion();
  const remove = useDeleteModelVersion();
  const [open, setOpen] = useState<string | null>(model.default_version?.id ?? null);
  const [adding, setAdding] = useState(false);
  const [deleting, setDeleting] = useState<ModelVersion | null>(null);
  const [prefetch, setPrefetch] = useState<ModelVersion | null>(null);
  const list = versions.data?.items ?? [];
  return (
    <Card className="gap-0 rounded-md py-0 shadow-premium">
      <CardHeader className="flex flex-row items-start justify-between gap-4 border-b border-border px-4 py-4">
        <div>
          <CardTitle>Versions</CardTitle>
          <CardDescription>Each version is a set of files with hashes. Deployments use the default unless they pin one.</CardDescription>
        </div>
        <Button variant="outline" size="sm" className="rounded-sm" onClick={() => setAdding(true)}>
          <GitBranchPlus className="size-4" />
          Add version
        </Button>
      </CardHeader>
      <CardContent className="px-0">
        {list.length === 0 ? (
          <EmptyState icon={Layers} title="No versions" description="Add one to make the model deployable." />
        ) : (
          <ul className="divide-y divide-border">
            {list.map((v) => {
              const expanded = open === v.id;
              return (
                <li key={v.id}>
                  <div className="flex items-center gap-3 px-4 py-3">
                    <button type="button" onClick={() => setOpen(expanded ? null : v.id)} className="flex min-w-0 flex-1 items-center gap-3 text-left" aria-expanded={expanded}>
                      <ChevronRight className={cn('size-4 shrink-0 text-muted-foreground transition-transform', expanded && 'rotate-90')} />
                      <span className="min-w-0">
                        <span className="flex flex-wrap items-center gap-2">
                          <span className="font-mono text-[13px] font-semibold">{v.version}</span>
                          {v.is_default && (
                            <span className="inline-flex items-center gap-1 rounded-sm border border-brand/40 bg-brand/10 px-1.5 text-[10.5px] font-medium">
                              <Star className="size-2.5" />
                              default
                            </span>
                          )}
                          <FormatBadge format={v.format} />
                          {v.quantization && <span className="font-mono text-[11px] text-muted-foreground">{v.quantization}</span>}
                          <StatusBadge variant={VERSION_BADGE[v.status]} className="py-0">
                            {v.status === 'verifying' && <Loader2 className="mr-1 size-3 animate-spin" />}
                            {MODEL_VERSION_STATUS_LABELS[v.status]}
                          </StatusBadge>
                        </span>
                        <span className="mt-0.5 block text-[11px] text-muted-foreground">
                          {v.file_count ?? 0} files · {formatBytes(v.total_size_bytes)} · {formatParams(v.parameter_count)} params
                          {v.revision ? <> · rev <span className="font-mono">{v.revision}</span></> : null} · added {formatRelativeTime(v.created_at)}
                        </span>
                        {v.status_message && <span className="mt-0.5 block text-[11px] text-warning-dark dark:text-warning-light">{v.status_message}</span>}
                      </span>
                    </button>
                    <DropdownMenu>
                      <DropdownMenuTrigger className="inline-flex h-8 w-8 cursor-pointer items-center justify-center rounded-md hover:bg-accent" aria-label={`Actions for ${v.version}`}>
                        <MoreVertical className="h-4 w-4" />
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end">
                        <DropdownMenuItem disabled={v.is_default} onClick={() => update.mutate({ modelId: model.id, versionId: v.id, body: { is_default: true } })}>
                          <Star className="mr-2 h-4 w-4" />
                          Make default
                        </DropdownMenuItem>
                        <DropdownMenuItem disabled={verify.isPending} onClick={() => verify.mutate({ modelId: model.id, versionId: v.id })}>
                          <ScanSearch className="mr-2 h-4 w-4" />
                          Re-verify hashes
                        </DropdownMenuItem>
                        <DropdownMenuItem disabled={v.status === 'blocked'} onClick={() => setPrefetch(v)}>
                          <Download className="mr-2 h-4 w-4" />
                          Prefetch to nodes
                        </DropdownMenuItem>
                        <DropdownMenuSeparator />
                        <DropdownMenuItem variant="destructive" disabled={v.is_default && list.length > 1} onClick={() => setDeleting(v)}>
                          <Trash2 className="mr-2 h-4 w-4" />
                          Delete version
                        </DropdownMenuItem>
                      </DropdownMenuContent>
                    </DropdownMenu>
                  </div>
                  {expanded && <VersionFiles model={model} version={v} />}
                </li>
              );
            })}
          </ul>
        )}
      </CardContent>
      {adding && <AddVersionDialog model={model} existing={list} open onOpenChange={setAdding} />}
      {prefetch && <PrefetchDialog key={prefetch.id} open onOpenChange={(o) => !o && setPrefetch(null)} preset={{ modelId: model.id, versionId: prefetch.id }} />}
      <DeleteConfirmationDialog
        open={!!deleting}
        onOpenChange={(o) => !o && setDeleting(null)}
        itemName={deleting?.version ?? ''}
        resourceType="version"
        warningMessage="Cached copies on nodes are evicted by the normal cleanup."
        isDeleting={remove.isPending}
        onConfirm={() => {
          if (deleting) remove.mutate({ modelId: model.id, versionId: deleting.id }, { onSuccess: () => setDeleting(null) });
        }}
      />
    </Card>
  );
}

function SettingsCard({ model }: { model: Model }) {
  const update = useUpdateModel();
  const [priority, setPriority] = useState<ModelPriority>(model.priority);
  const [parser, setParser] = useState(model.tool_parser_default ?? '');
  const [context, setContext] = useState(model.default_context_size ? String(model.default_context_size) : '');
  const [flags, setFlags] = useState<KeyValueRow[]>(() => recordToRows(model.default_launch_flags));
  const [aliases, setAliases] = useState((model.aliases ?? []).join(', '));
  const [trust, setTrust] = useState(model.trust_remote_code);
  const [pickle, setPickle] = useState(model.allow_pickle);
  const [description, setDescription] = useState(model.description ?? '');
  const meta = (model.metadata ?? {}) as Record<string, unknown>;
  const [tokenId, setTokenId] = useState(typeof meta.credential_id === 'string' ? meta.credential_id : '');
  const remote = model.source === 'huggingface' || model.source === 'modelscope';

  function save() {
    const restMeta = Object.fromEntries(Object.entries(meta).filter(([k]) => k !== 'credential_id'));
    update.mutate({
      id: model.id,
      body: {
        priority,
        tool_parser_default: parser.trim() || null,
        default_context_size: context ? Number(context) : null,
        default_launch_flags: rowsToFlags(flags),
        aliases: aliases.split(',').map((a) => a.trim()).filter(Boolean),
        trust_remote_code: trust,
        allow_pickle: pickle,
        description: description.trim() || null,
        ...(remote ? { metadata: tokenId ? { ...restMeta, credential_id: tokenId } : restMeta } : {}),
      },
    });
  }

  return (
    <Card className="rounded-md shadow-premium">
      <CardHeader>
        <CardTitle>Serving defaults</CardTitle>
        <CardDescription>Applied to new deployments of this model. Priority is read live by the rules engine.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <Field label="Priority">
          <div className="space-y-1.5" role="radiogroup" aria-label="Priority">
            {MODEL_PRIORITY_OPTIONS.map((o) => (
              <button
                key={o.value}
                type="button"
                role="radio"
                aria-checked={priority === o.value}
                onClick={() => setPriority(o.value)}
                className={cn('flex w-full items-start gap-2.5 rounded-lg border p-2.5 text-left transition-colors', priority === o.value ? 'border-foreground/60 bg-muted/40 ring-1 ring-foreground/50' : 'border-border hover:border-foreground/25')}
              >
                <PriorityBadge priority={o.value} className="mt-px shrink-0" />
                <span className="text-[11px] leading-snug text-muted-foreground">{MODEL_PRIORITY_DESCRIPTIONS[o.value]}</span>
              </button>
            ))}
          </div>
        </Field>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Tool parser" htmlFor="m-parser">
            <Input id="m-parser" value={parser} onChange={(e) => setParser(e.target.value)} placeholder="none" className="font-mono text-sm" />
          </Field>
          <Field label="Context (tokens)" htmlFor="m-ctx">
            <Input id="m-ctx" type="number" value={context} onChange={(e) => setContext(e.target.value)} placeholder="engine default" className="font-mono text-sm" />
          </Field>
        </div>
        <Field label="Aliases" hint="Extra serving names, comma separated." htmlFor="m-alias">
          <Input id="m-alias" value={aliases} onChange={(e) => setAliases(e.target.value)} placeholder="qwen-small, router" className="font-mono text-sm" />
        </Field>
        <Field label="Default launch flags" hint="Blank value = bare flag. Merged under each deployment's own flags.">
          <KeyValueEditor rows={flags} onChange={setFlags} keyPlaceholder="--flash-attn" valuePlaceholder="" addLabel="Add flag" />
        </Field>
        <Field label="Description" htmlFor="m-desc">
          <Input id="m-desc" value={description} onChange={(e) => setDescription(e.target.value)} placeholder="What it is used for" />
        </Field>
        {remote && (
          <Field label="Access token" hint="Used when nodes download this model (gated / private repos). Empty = the default token.">
            <CredentialPicker value={tokenId} onChange={(id) => setTokenId(id)} kinds={[model.source]} allowNone noneLabel="Default token" placeholder="Default token" />
          </Field>
        )}
        <div className="space-y-2 rounded-lg border border-border p-3">
          <label className="flex items-center justify-between gap-3 text-xs">
            <span>
              Trust remote code
              {trust && <ShieldAlert className="ml-1 inline size-3.5 text-warning" />}
            </span>
            <Switch checked={trust} onCheckedChange={setTrust} aria-label="Trust remote code" />
          </label>
          <label className="flex items-center justify-between gap-3 text-xs">
            <span>
              Allow pickle weights
              {pickle && <ShieldAlert className="ml-1 inline size-3.5 text-error" />}
            </span>
            <Switch checked={pickle} onCheckedChange={setPickle} aria-label="Allow pickle" />
          </label>
          {(trust || pickle) && <p className="text-[11px] text-muted-foreground">Code from the repository can run on your GPU nodes. Recorded in the audit log.</p>}
        </div>
        <Button size="sm" className="w-full rounded-sm" disabled={update.isPending} onClick={save}>
          {update.isPending ? <Loader2 className="size-4 animate-spin" /> : <Save className="size-4" />}
          Save defaults
        </Button>
      </CardContent>
    </Card>
  );
}

const SEVERITY_ICON = { info: CircleCheck, warning: TriangleAlert, error: XCircle } as const;
const SEVERITY_TONE = { info: 'text-success', warning: 'text-warning', error: 'text-error' } as const;

function CompatCard({ model }: { model: Model }) {
  const check = useCompatCheck();
  const version = model.default_version;
  const results: CompatCheck[] = check.data?.items ?? [];
  return (
    <Card className="rounded-md shadow-premium">
      <CardHeader className="flex flex-row items-start justify-between gap-4">
        <div>
          <CardTitle>Engine compatibility</CardTitle>
          <CardDescription>Format, model type, the accelerators in this fleet and a VRAM estimate (weights + KV cache) for the default version.</CardDescription>
        </div>
        <Button variant="outline" size="sm" className="rounded-sm" disabled={!version || check.isPending} onClick={() => version && check.mutate({ model_version_id: version.id, gpu_count: 1, context_size: model.default_context_size ?? null })}>
          {check.isPending ? <Loader2 className="size-4 animate-spin" /> : <ScanSearch className="size-4" />}
          {results.length ? 'Re-check' : 'Check engines'}
        </Button>
      </CardHeader>
      <CardContent>
        {results.length === 0 ? (
          <p className="py-4 text-center text-sm text-muted-foreground">Run a check to see which engines can serve {version ? <span className="font-mono">{version.version}</span> : 'this model'}.</p>
        ) : (
          <ul className="divide-y divide-border rounded-lg border border-border">
            {results.map((c) => (
              <li key={c.id} className="grid gap-2 px-3 py-2.5 sm:grid-cols-[180px_1fr_auto] sm:items-start">
                <div>
                  <EngineBadge engine={c.engine_name} version={c.engine_version} />
                  <div className="mt-1 flex items-center gap-2">
                    <StatusBadge variant={COMPAT_BADGE[c.result]} className="py-0">{c.result}</StatusBadge>
                    <span className="font-mono text-[11px] text-muted-foreground">score {c.score}</span>
                  </div>
                </div>
                <ul className="space-y-0.5">
                  {c.reasons.map((r, i) => {
                    const Icon = SEVERITY_ICON[r.severity ?? 'info'];
                    return (
                      <li key={i} className="flex gap-1.5 text-[11px] leading-snug">
                        <Icon className={cn('mt-px size-3 shrink-0', SEVERITY_TONE[r.severity ?? 'info'])} />
                        <span className="text-muted-foreground">{r.message}</span>
                      </li>
                    );
                  })}
                </ul>
                <span className="font-mono text-[11px] text-muted-foreground tabular-nums sm:text-right">{c.estimated_vram_bytes ? `~${formatBytes(c.estimated_vram_bytes)}` : ''}</span>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

export function ModelDetail() {
  const router = useRouter();
  const { id } = useParams<{ id: string }>();
  const { data: model, isLoading, isError, refetch } = useModel(id);
  const deployments = useDeployments({ model_id: id, page_size: 50 }, { enabled: !!id });
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [prefetchOpen, setPrefetchOpen] = useState(false);
  const cacheItems = useModelVersionCache(id, model?.default_version?.id ?? '');

  if (isLoading) return <ListPageSkeleton stats={4} rows={4} />;
  if (isError || !model) {
    return (
      <PageContainer>
        <ErrorState title="Model not found" description="It may have been deleted, or the control plane is unreachable." onRetry={() => void refetch()} />
      </PageContainer>
    );
  }

  const v = model.default_version;
  const url = repoUrl(model);
  const blocked = v?.status === 'blocked';
  const cachedNodes = new Set((cacheItems.data?.items ?? []).filter((c) => c.state === 'ready').map((c) => c.node_id)).size;
  const deploymentList = deployments.data?.items ?? [];

  return (
    <PageContainer>
      <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div className="space-y-2">
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="text-2xl font-semibold tracking-tight sm:text-[28px]">{model.display_name ?? model.name}</h1>
            <ModelTypeBadge type={model.type} />
            <PriorityBadge priority={model.priority} />
          </div>
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-muted-foreground">
            <span className="inline-flex items-center gap-1 font-mono text-[13px] text-foreground">
              {model.name}
              <CopyButton value={model.name} successText="Serving name copied" />
            </span>
            <SourceBadge source={model.source} />
            {url ? (
              <a href={url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 font-mono text-xs hover:text-foreground hover:underline">
                {model.repo_id}
                <ExternalLink className="size-3" />
              </a>
            ) : (
              <span className="max-w-[520px] truncate font-mono text-xs" title={model.repo_id}>
                {model.repo_id}
              </span>
            )}
            <SecurityFlags trustRemoteCode={model.trust_remote_code} allowPickle={model.allow_pickle} />
          </div>
          {model.description && <p className="max-w-3xl text-sm text-muted-foreground">{model.description}</p>}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="outline" size="sm" className="rounded-sm" disabled={!v || blocked} onClick={() => setPrefetchOpen(true)}>
            <Download className="size-4" />
            Prefetch
          </Button>
          <Link href={`/deployments/new?model_id=${model.id}`} aria-disabled={blocked} className={cn(buttonVariants({ size: 'sm' }), 'rounded-sm', blocked && 'pointer-events-none opacity-50')}>
            <Rocket className="size-4" />
            Deploy
          </Link>
          <DropdownMenu>
            <DropdownMenuTrigger className="inline-flex h-9 cursor-pointer items-center justify-center rounded-sm border border-border bg-background px-3 hover:bg-muted dark:border-input dark:bg-input/30" aria-label="More actions">
              <MoreVertical className="h-4 w-4" />
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem variant="destructive" onClick={() => setDeleteOpen(true)}>
                <Trash2 className="mr-2 h-4 w-4" />
                Delete model
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </div>

      {blocked && (
        <div className="flex items-start gap-3 rounded-lg border border-error/30 bg-error/10 px-4 py-3 text-sm">
          <ShieldAlert className="mt-0.5 size-4 shrink-0 text-error" />
          <span>
            <span className="font-medium">Default version blocked.</span> <span className="text-muted-foreground">{v?.status_message ?? 'The weights are pickle files.'} Enable “Allow pickle weights” in the defaults if you trust this repository.</span>
          </span>
        </div>
      )}

      <div className="grid gap-4 md:grid-cols-4">
        <StatCard label="Default version" value={<span className="font-mono text-xl">{v?.version ?? '—'}</span>} icon={Layers} footnote={v ? `${v.format.toUpperCase()} · ${formatBytes(v.total_size_bytes)}` : 'No versions'} />
        <StatCard label="Parameters" value={formatParams(v?.parameter_count)} icon={ScanSearch} footnote={model.family ? `family ${model.family}` : 'family unknown'} />
        <StatCard label="Deployments" value={deploymentList.length} icon={Rocket} footnote={`${deploymentList.filter((d) => d.status === 'running').length} running`} />
        <StatCard label="Cached on" value={`${cachedNodes} node${cachedNodes === 1 ? '' : 's'}`} icon={HardDrive} footnote="Default version, all files ready" />
      </div>

      <div className="grid items-start gap-6 lg:grid-cols-3">
        <div className="space-y-6 lg:col-span-2">
          <VersionsCard model={model} />
          <CompatCard model={model} />
          <Card className="rounded-md shadow-premium">
            <CardHeader>
              <CardTitle>Deployments</CardTitle>
              <CardDescription>Where this model is served.</CardDescription>
            </CardHeader>
            <CardContent>
              {deploymentList.length === 0 ? (
                <p className="py-4 text-center text-sm text-muted-foreground">Not deployed yet.</p>
              ) : (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Deployment</TableHead>
                      <TableHead>Engine</TableHead>
                      <TableHead className="text-right">Replicas</TableHead>
                      <TableHead>Status</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {deploymentList.map((d) => (
                      <TableRow key={d.id} className="cursor-pointer" onClick={() => router.push(`/deployments/${d.id}`)}>
                        <TableCell className="font-medium">{d.name}</TableCell>
                        <TableCell>
                          <EngineBadge engine={d.engine_name} version={d.engine_version} />
                        </TableCell>
                        <TableCell className="text-right tabular-nums">
                          {d.ready_replicas ?? 0} / {d.replicas}
                        </TableCell>
                        <TableCell>
                          <StatusBadge variant={DEPLOYMENT_BADGE[d.status]}>{DEPLOYMENT_STATUS_LABELS[d.status]}</StatusBadge>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
            </CardContent>
          </Card>
        </div>
        <div className="space-y-6">
          <SettingsCard key={model.updated_at} model={model} />
          <Card className="rounded-md shadow-premium">
            <CardHeader>
              <CardTitle>Record</CardTitle>
            </CardHeader>
            <CardContent className="space-y-1.5 text-xs">
              <div className="flex justify-between gap-3"><span className="text-muted-foreground">Registered</span><span>{formatDate(model.created_at)}</span></div>
              <div className="flex justify-between gap-3"><span className="text-muted-foreground">Updated</span><span>{model.updated_at ? formatRelativeTime(model.updated_at) : '—'}</span></div>
              <div className="flex justify-between gap-3"><span className="text-muted-foreground">Tags</span><span className="truncate">{(model.tags ?? []).join(', ') || '—'}</span></div>
              <div className="flex justify-between gap-3"><span className="text-muted-foreground">Priority</span><span>{MODEL_PRIORITY_LABELS[model.priority]}</span></div>
              <div className="flex justify-between gap-3"><span className="text-muted-foreground">ID</span><span className="font-mono text-[10.5px]">{model.id}</span></div>
            </CardContent>
          </Card>
        </div>
      </div>

      {deleteOpen && <DeleteModelDialog model={model} open onOpenChange={setDeleteOpen} onDeleted={() => router.push('/catalog')} />}
      {prefetchOpen && v && <PrefetchDialog open onOpenChange={setPrefetchOpen} preset={{ modelId: model.id, versionId: v.id }} />}
    </PageContainer>
  );
}

