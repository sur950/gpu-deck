'use client';

import { useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { useParams, useRouter, useSearchParams } from 'next/navigation';
import { CircleCheck, CircleX, Download, FlaskConical, Loader2, MoreVertical, Pencil, Play, Plus, Square, Trash2, WandSparkles } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Switch } from '@/components/ui/switch';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from '@/components/ui/dropdown-menu';
import { PageContainer } from '@/components/shared/PageContainer';
import { PageHeader } from '@/components/shared/PageHeader';
import { ListPageSkeleton } from '@/components/shared/ListPageSkeleton';
import { ErrorState } from '@/components/shared/ErrorState';
import { EmptyState } from '@/components/shared/EmptyState';
import { StatusBadge } from '@/components/shared/StatusBadge';
import { SearchInput } from '@/components/shared/search-input';
import { DeleteConfirmationDialog } from '@/components/shared/DeleteConfirmationDialog';
import { downloadFromApi } from '@/components/shared/download';
import { RUN_BADGE } from '@/components/fleet/status';
import { LatencyChart, ThroughputChart } from '@/components/models/benchmark-charts';
import { BenchmarkForm, type BenchmarkFormValue } from '@/components/models/benchmark-form';
import {
  useApplyRecommendation,
  useBenchmark,
  useBenchmarkRuns,
  useBenchmarks,
  useCancelBenchmarkRun,
  useCreateBenchmark,
  useDeleteBenchmark,
  useStartBenchmark,
  useUpdateBenchmark,
} from '@/hooks/api/models-benchmarks';
import { useUrlState } from '@/hooks/use-url-state';
import { useDebounce } from '@/hooks/use-debounce';
import { cn } from '@/lib/utils';
import { formatRelativeTime } from '@/utils/format';
import { BENCHMARK_RUN_STATUS_LABELS, type Benchmark, type BenchmarkResult, type BenchmarkRun } from '@/types/models';

function sloText(b: Benchmark): string {
  const s = (b.target_slo ?? {}) as Record<string, number>;
  const parts = [s.ttft_ms_p95 && `TTFT p95 ≤ ${s.ttft_ms_p95} ms`, s.e2e_ms_p95 && `E2E p95 ≤ ${s.e2e_ms_p95} ms`, s.tpot_ms_p95 && `TPOT ≤ ${s.tpot_ms_p95} ms`, s.min_generation_tps && `≥ ${s.min_generation_tps} tok/s`, s.max_error_rate_pct && `errors ≤ ${s.max_error_rate_pct}%`].filter(Boolean);
  return parts.join(' · ') || 'no SLO';
}

function RunStatus({ run }: { run: BenchmarkRun | null | undefined }) {
  if (!run) return <span className="text-xs text-muted-foreground">never run</span>;
  if (run.status === 'running' || run.status === 'pending') {
    return (
      <div className="w-32 space-y-1">
        <div className="flex justify-between text-[11px]">
          <span className="text-info">{run.status === 'pending' ? 'Queued' : 'Running'}</span>
          <span className="font-mono tabular-nums">{Math.round(run.progress_pct)}%</span>
        </div>
        <div className="h-1 overflow-hidden rounded-full bg-muted">
          <div className="h-full rounded-full bg-info transition-[width] duration-700" style={{ width: `${run.progress_pct}%` }} />
        </div>
      </div>
    );
  }
  return <StatusBadge variant={RUN_BADGE[run.status]}>{BENCHMARK_RUN_STATUS_LABELS[run.status]}</StatusBadge>;
}

// ─── List ───────────────────────────────────────────────────────────────────

const DEFAULTS = { search: '' };

export function BenchmarksList() {
  const router = useRouter();
  const { params, setParams } = useUrlState(DEFAULTS);
  const [search, setSearch] = useState(params.search);
  const debounced = useDebounce(search, 300);
  const urlSearch = params.search;
  useEffect(() => {
    if (debounced !== urlSearch) setParams({ search: debounced });
  }, [debounced, urlSearch, setParams]);
  const { data, isLoading, isError, refetch } = useBenchmarks({ search: debounced, page_size: 100 });
  const start = useStartBenchmark();
  const remove = useDeleteBenchmark();
  const [deleting, setDeleting] = useState<Benchmark | null>(null);
  const list = data?.items ?? [];

  if (isLoading && !data) return <ListPageSkeleton stats={0} />;
  return (
    <PageContainer>
      <PageHeader
        title="Benchmarks"
        description="Load-test a deployment through the API layer, find the best concurrency for a latency target, and apply the tuned settings."
        actions={
          <Button size="sm" className="rounded-sm" onClick={() => router.push('/benchmarks/new')}>
            <Plus className="size-4" />
            New benchmark
          </Button>
        }
      />
      {isError ? (
        <ErrorState title="Could not load benchmarks" onRetry={() => void refetch()} />
      ) : (
        <Card className="rounded-md shadow-premium">
          <CardHeader>
            <SearchInput value={search} onChange={setSearch} placeholder="Search benchmarks…" />
          </CardHeader>
          <CardContent>
            {list.length === 0 ? (
              <EmptyState
                icon={FlaskConical}
                title={debounced ? 'No benchmarks match' : 'No benchmarks yet'}
                description="Define a concurrency sweep and an SLO; every run is kept so you can compare engines, flags and hardware."
                action={
                  !debounced && (
                    <Button size="sm" className="rounded-sm" onClick={() => router.push('/benchmarks/new')}>
                      <Plus className="size-4" />
                      New benchmark
                    </Button>
                  )
                }
              />
            ) : (
              <div className="overflow-x-auto">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Benchmark</TableHead>
                      <TableHead>Deployment</TableHead>
                      <TableHead>Sweep</TableHead>
                      <TableHead>Target</TableHead>
                      <TableHead>Last run</TableHead>
                      <TableHead className="text-right">Best concurrency</TableHead>
                      <TableHead className="w-10" />
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {list.map((b) => {
                      const run = b.last_run;
                      const running = run?.status === 'running' || run?.status === 'pending';
                      return (
                        <TableRow key={b.id} className="cursor-pointer" onClick={() => router.push(`/benchmarks/${b.id}`)}>
                          <TableCell>
                            <Link href={`/benchmarks/${b.id}`} className="font-semibold hover:underline" onClick={(e) => e.stopPropagation()}>
                              {b.name}
                            </Link>
                            <div className="text-[11px] text-muted-foreground">
                              {b.run_count ?? 0} run{b.run_count === 1 ? '' : 's'}
                              {b.auto_tune ? ' · auto-tune' : ''}
                            </div>
                          </TableCell>
                          <TableCell>
                            <Link href={`/deployments/${b.deployment_id}`} className="font-mono text-xs hover:underline" onClick={(e) => e.stopPropagation()}>
                              {b.deployment_name ?? b.deployment_id.slice(0, 8)}
                            </Link>
                          </TableCell>
                          <TableCell className="font-mono text-[11px] text-muted-foreground">{(b.concurrency_levels ?? []).join(' · ')}</TableCell>
                          <TableCell className="max-w-[240px] text-xs text-muted-foreground">{sloText(b)}</TableCell>
                          <TableCell>
                            <RunStatus run={run} />
                            {run?.finished_at && <div className="mt-0.5 text-[10.5px] text-muted-foreground">{formatRelativeTime(run.finished_at)}</div>}
                          </TableCell>
                          <TableCell className="text-right">
                            {run?.best_concurrency ? (
                              <span className="inline-flex items-center gap-1.5 font-mono text-sm font-semibold tabular-nums">
                                {run.slo_met ? <CircleCheck className="size-3.5 text-success" /> : <CircleX className="size-3.5 text-error" />}
                                {run.best_concurrency}
                              </span>
                            ) : (
                              <span className="text-xs text-muted-foreground">—</span>
                            )}
                          </TableCell>
                          <TableCell>
                            <DropdownMenu>
                              <DropdownMenuTrigger onClick={(e) => e.stopPropagation()} className="inline-flex h-8 w-8 cursor-pointer items-center justify-center rounded-md hover:bg-accent" aria-label={`Actions for ${b.name}`}>
                                <MoreVertical className="h-4 w-4" />
                              </DropdownMenuTrigger>
                              <DropdownMenuContent align="end" onClick={(e) => e.stopPropagation()}>
                                <DropdownMenuItem disabled={running} onClick={() => start.mutate({ id: b.id })}>
                                  <Play className="mr-2 h-4 w-4" />
                                  Run sweep
                                </DropdownMenuItem>
                                <DropdownMenuItem disabled={running} onClick={() => start.mutate({ id: b.id, autoTune: true })}>
                                  <WandSparkles className="mr-2 h-4 w-4" />
                                  Run auto-tune
                                </DropdownMenuItem>
                                <DropdownMenuItem onClick={() => router.push(`/benchmarks/${b.id}/edit`)}>
                                  <Pencil className="mr-2 h-4 w-4" />
                                  Edit
                                </DropdownMenuItem>
                                <DropdownMenuSeparator />
                                <DropdownMenuItem variant="destructive" onClick={() => setDeleting(b)}>
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
            )}
          </CardContent>
        </Card>
      )}
      <DeleteConfirmationDialog
        open={!!deleting}
        onOpenChange={(o) => !o && setDeleting(null)}
        itemName={deleting?.name ?? ''}
        resourceType="benchmark"
        warningMessage="All of its runs and results are deleted too."
        isDeleting={remove.isPending}
        onConfirm={() => {
          if (deleting) remove.mutate(deleting.id, { onSuccess: () => setDeleting(null) });
        }}
      />
    </PageContainer>
  );
}

// ─── New / edit ─────────────────────────────────────────────────────────────

export function BenchmarkNew() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const create = useCreateBenchmark();
  const start = useStartBenchmark();
  function submit({ body, runNow }: BenchmarkFormValue) {
    create.mutate(body, {
      onSuccess: (b) => {
        if (runNow) start.mutate({ id: b.id, autoTune: body.auto_tune });
        router.push(`/benchmarks/${b.id}`);
      },
    });
  }
  return (
    <PageContainer className="max-w-6xl">
      <PageHeader title="New benchmark" description="Runs through the API layer exactly like a client would, against one instance of the deployment." />
      <BenchmarkForm presetDeployment={searchParams.get('deployment_id') ?? undefined} isPending={create.isPending} onSubmit={submit} onCancel={() => router.push('/benchmarks')} />
    </PageContainer>
  );
}

export function BenchmarkEdit() {
  const router = useRouter();
  const { id } = useParams<{ id: string }>();
  const { data, isLoading, isError, refetch } = useBenchmark(id);
  const update = useUpdateBenchmark();
  if (isLoading) return <ListPageSkeleton stats={0} rows={4} />;
  if (isError || !data) {
    return (
      <PageContainer>
        <ErrorState title="Benchmark not found" onRetry={() => void refetch()} />
      </PageContainer>
    );
  }
  return (
    <PageContainer className="max-w-6xl">
      <PageHeader title={`Edit ${data.name}`} description="Changes apply to the next run; past runs keep their own settings." />
      <BenchmarkForm
        initial={data}
        isPending={update.isPending}
        onCancel={() => router.push(`/benchmarks/${data.id}`)}
        onSubmit={({ body }) => {
          const { deployment_id: _deployment, ...rest } = body;
          void _deployment;
          update.mutate({ id: data.id, body: rest }, { onSuccess: () => router.push(`/benchmarks/${data.id}`) });
        }}
      />
    </PageContainer>
  );
}

// ─── Detail ─────────────────────────────────────────────────────────────────

function ResultsTable({ results, best }: { results: BenchmarkResult[]; best?: number | null }) {
  const p = (r: BenchmarkResult, k: 'ttft_ms' | 'e2e_ms' | 'tpot_ms', q: string) => {
    const v = (r[k] as Record<string, unknown> | undefined)?.[q];
    return typeof v === 'number' ? v : null;
  };
  return (
    <div className="overflow-x-auto">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="text-right">Concurrency</TableHead>
            <TableHead className="text-right">Req/s</TableHead>
            <TableHead className="text-right">tok/s</TableHead>
            <TableHead className="text-right">TTFT p50</TableHead>
            <TableHead className="text-right">TTFT p95</TableHead>
            <TableHead className="text-right">TPOT p50</TableHead>
            <TableHead className="text-right">E2E p95</TableHead>
            <TableHead className="text-right">Errors</TableHead>
            <TableHead>SLO</TableHead>
            <TableHead>Flags</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {results.map((r, i) => (
            <TableRow key={i} className={cn(r.concurrency === best && !r.launch_flags && 'bg-success/5')}>
              <TableCell className="text-right font-mono text-xs font-semibold tabular-nums">{r.concurrency}</TableCell>
              <TableCell className="text-right font-mono text-xs tabular-nums">{r.rps?.toFixed(2) ?? '—'}</TableCell>
              <TableCell className="text-right font-mono text-xs tabular-nums">{r.generation_tps ? Math.round(r.generation_tps) : '—'}</TableCell>
              <TableCell className="text-right font-mono text-xs tabular-nums">{p(r, 'ttft_ms', 'p50') ?? '—'}</TableCell>
              <TableCell className="text-right font-mono text-xs tabular-nums">{p(r, 'ttft_ms', 'p95') ?? '—'}</TableCell>
              <TableCell className="text-right font-mono text-xs tabular-nums">{p(r, 'tpot_ms', 'p50') ?? '—'}</TableCell>
              <TableCell className="text-right font-mono text-xs tabular-nums">{p(r, 'e2e_ms', 'p95') ?? '—'}</TableCell>
              <TableCell className="text-right font-mono text-xs tabular-nums">{r.errors ?? 0}</TableCell>
              <TableCell>{r.slo_met ? <CircleCheck className="size-4 text-success" aria-label="met" /> : <CircleX className="size-4 text-error" aria-label="missed" />}</TableCell>
              <TableCell className="max-w-[220px] truncate font-mono text-[10.5px] text-muted-foreground">{r.launch_flags ? Object.entries(r.launch_flags).map(([k, v]) => `${k} ${String(v)}`).join(' ') : 'baseline'}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

function Recommendation({ run }: { run: BenchmarkRun }) {
  const apply = useApplyRecommendation();
  const [flags, setFlags] = useState(true);
  const [traffic, setTraffic] = useState(true);
  const [replicas, setReplicas] = useState(false);
  const rec = (run.recommendation ?? {}) as { concurrency?: number; launch_flags?: Record<string, unknown>; replicas?: number; reason?: string };
  if (!rec.reason && !rec.concurrency) return null;
  return (
    <Card className={cn('relative overflow-hidden rounded-md shadow-premium', run.slo_met ? 'border-success/30' : 'border-warning/40')}>
      <span aria-hidden className="pointer-events-none absolute -top-16 -right-12 size-44 rounded-full bg-brand/10 blur-3xl" />
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <WandSparkles className="size-4 text-muted-foreground" />
          {rec.concurrency ? `Run at concurrency ${rec.concurrency}` : 'No level met the SLO'}
        </CardTitle>
        <CardDescription className="text-[13px] leading-relaxed">{rec.reason}</CardDescription>
      </CardHeader>
      {rec.concurrency && (
        <CardContent className="space-y-3">
          <div className="flex flex-wrap gap-1">
            {Object.entries(rec.launch_flags ?? {}).map(([k, v]) => (
              <code key={k} className="rounded-sm border border-border bg-muted/40 px-1.5 py-0.5 font-mono text-[10.5px]">
                {v === true ? k : `${k} ${String(v)}`}
              </code>
            ))}
          </div>
          <div className="grid gap-2 text-xs sm:grid-cols-3">
            <label className="flex items-center justify-between gap-2 rounded-md bg-muted/40 px-3 py-2">
              Launch flags
              <Switch size="sm" checked={flags} onCheckedChange={setFlags} aria-label="Apply launch flags" />
            </label>
            <label className="flex items-center justify-between gap-2 rounded-md bg-muted/40 px-3 py-2">
              Max concurrent / instance
              <Switch size="sm" checked={traffic} onCheckedChange={setTraffic} aria-label="Apply traffic config" />
            </label>
            <label className="flex items-center justify-between gap-2 rounded-md bg-muted/40 px-3 py-2">
              Replicas ({rec.replicas ?? '—'})
              <Switch size="sm" checked={replicas} onCheckedChange={setReplicas} aria-label="Apply replicas" />
            </label>
          </div>
          <div className="flex justify-end">
            <Button size="sm" className="rounded-sm" disabled={apply.isPending || (!flags && !traffic && !replicas)} onClick={() => apply.mutate({ runId: run.id, body: { apply_launch_flags: flags, apply_traffic_config: traffic, apply_replicas: replicas } })}>
              {apply.isPending ? <Loader2 className="size-4 animate-spin" /> : <WandSparkles className="size-4" />}
              Apply to deployment
            </Button>
          </div>
          {flags && <p className="text-[11px] text-muted-foreground">Flag changes roll through the replicas one at a time.</p>}
        </CardContent>
      )}
    </Card>
  );
}

export function BenchmarkDetail() {
  const router = useRouter();
  const { id } = useParams<{ id: string }>();
  const { params, setParams } = useUrlState({ run: '' });
  const { data: b, isLoading, isError, refetch } = useBenchmark(id);
  const runs = useBenchmarkRuns(id);
  const start = useStartBenchmark();
  const cancel = useCancelBenchmarkRun();
  const remove = useDeleteBenchmark();
  const [deleteOpen, setDeleteOpen] = useState(false);
  const runList = useMemo(() => runs.data?.items ?? [], [runs.data]);
  const selected = runList.find((r) => r.id === params.run) ?? runList[0];

  if (isLoading) return <ListPageSkeleton stats={0} rows={5} />;
  if (isError || !b) {
    return (
      <PageContainer>
        <ErrorState title="Benchmark not found" onRetry={() => void refetch()} />
      </PageContainer>
    );
  }
  const slo = (b.target_slo ?? {}) as Record<string, number>;
  const running = runList.some((r) => r.status === 'running' || r.status === 'pending');
  const workload = (b.workload ?? {}) as Record<string, unknown>;

  return (
    <PageContainer>
      <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div className="space-y-1.5">
          <h1 className="text-2xl font-semibold tracking-tight sm:text-[28px]">{b.name}</h1>
          <p className="text-sm text-muted-foreground">
            <Link href={`/deployments/${b.deployment_id}`} className="font-mono text-foreground hover:underline">
              {b.deployment_name}
            </Link>{' '}
            · levels {(b.concurrency_levels ?? []).join(', ')} · {b.duration_seconds}s each · {String(workload.input_tokens ?? '?')} in / {String(workload.output_tokens ?? '?')} out tokens
          </p>
          <p className="text-xs text-muted-foreground">{sloText(b)}</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="outline" size="sm" className="rounded-sm" disabled={running || start.isPending} onClick={() => start.mutate({ id: b.id }, { onSuccess: (r) => setParams({ run: r.id }) })}>
            <Play className="size-4" />
            Run sweep
          </Button>
          <Button size="sm" className="rounded-sm" disabled={running || start.isPending} onClick={() => start.mutate({ id: b.id, autoTune: true }, { onSuccess: (r) => setParams({ run: r.id }) })}>
            <WandSparkles className="size-4" />
            Auto-tune
          </Button>
          <DropdownMenu>
            <DropdownMenuTrigger className="inline-flex h-9 cursor-pointer items-center justify-center rounded-sm border border-border bg-background px-3 hover:bg-muted dark:border-input dark:bg-input/30" aria-label="More actions">
              <MoreVertical className="h-4 w-4" />
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem onClick={() => router.push(`/benchmarks/${b.id}/edit`)}>
                <Pencil className="mr-2 h-4 w-4" />
                Edit
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem variant="destructive" onClick={() => setDeleteOpen(true)}>
                <Trash2 className="mr-2 h-4 w-4" />
                Delete
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </div>

      <div className="grid items-start gap-6 xl:grid-cols-[260px_minmax(0,1fr)]">
        <Card className="gap-0 rounded-md py-0 shadow-premium">
          <CardHeader className="border-b border-border px-4 py-3">
            <CardTitle className="text-sm">Runs</CardTitle>
          </CardHeader>
          <CardContent className="px-0">
            {runList.length === 0 ? (
              <p className="p-4 text-center text-xs text-muted-foreground">No runs yet.</p>
            ) : (
              <ul className="divide-y divide-border">
                {runList.map((r) => (
                  <li key={r.id}>
                    <button type="button" onClick={() => setParams({ run: r.id })} className={cn('w-full space-y-1 px-4 py-2.5 text-left transition-colors hover:bg-muted/40', selected?.id === r.id && 'bg-muted/60 shadow-[inset_3px_0_0_rgb(var(--brand))]')}>
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-xs font-medium">{formatRelativeTime(r.started_at ?? r.created_at)}</span>
                        <RunStatus run={r} />
                      </div>
                      <div className="text-[11px] text-muted-foreground">
                        {r.results.some((x) => x.launch_flags) ? 'auto-tune · ' : ''}
                        {r.best_concurrency ? `best ${r.best_concurrency}` : r.error_message ? 'error' : `${r.results.length} levels`}
                      </div>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>

        {!selected ? (
          <Card className="rounded-md shadow-premium">
            <CardContent>
              <EmptyState icon={FlaskConical} title="Run the sweep" description="Each level ramps to the target concurrency, holds it for the duration and records TTFT, time per token, end-to-end latency and throughput." />
            </CardContent>
          </Card>
        ) : (
          <div className="space-y-6">
            {(selected.status === 'running' || selected.status === 'pending') && (
              <div className="flex items-center gap-4 rounded-lg border border-info/30 bg-info/10 px-4 py-3">
                <Loader2 className="size-4 animate-spin text-info" />
                <div className="flex-1">
                  <p className="text-sm font-medium">{selected.status === 'pending' ? 'Waiting for the jobs process…' : `Running — ${selected.results.length} level(s) done`}</p>
                  <div className="mt-1.5 h-1 overflow-hidden rounded-full bg-muted">
                    <div className="h-full rounded-full bg-info transition-[width] duration-700" style={{ width: `${selected.progress_pct}%` }} />
                  </div>
                </div>
                <Button variant="outline" size="sm" className="rounded-sm" disabled={cancel.isPending} onClick={() => cancel.mutate({ runId: selected.id, benchmarkId: b.id })}>
                  <Square className="size-4" />
                  Cancel
                </Button>
              </div>
            )}
            {selected.error_message && <div className="rounded-lg border border-error/30 bg-error/10 px-4 py-3 text-sm">{selected.error_message}</div>}
            {selected.status === 'completed' && <Recommendation run={selected} />}
            {selected.results.length > 0 && (
              <div className="grid gap-6 2xl:grid-cols-2">
                <Card className="gap-0 rounded-xl py-0 shadow-premium">
                  <CardHeader className="border-b border-border px-5 py-4">
                    <CardTitle className="text-sm font-semibold">Latency vs concurrency</CardTitle>
                    <CardDescription>Milliseconds, per level</CardDescription>
                  </CardHeader>
                  <CardContent className="px-2 py-4 sm:px-4">
                    <LatencyChart results={selected.results} sloTtft={slo.ttft_ms_p95 ?? null} best={selected.best_concurrency} />
                  </CardContent>
                </Card>
                <Card className="gap-0 rounded-xl py-0 shadow-premium">
                  <CardHeader className="border-b border-border px-5 py-4">
                    <CardTitle className="text-sm font-semibold">Throughput vs concurrency</CardTitle>
                    <CardDescription>Generated tokens/s and requests/s</CardDescription>
                  </CardHeader>
                  <CardContent className="px-2 py-4 sm:px-4">
                    <ThroughputChart results={selected.results} best={selected.best_concurrency} />
                  </CardContent>
                </Card>
              </div>
            )}
            <Card className="rounded-md shadow-premium">
              <CardHeader className="flex flex-row items-start justify-between gap-3">
                <div>
                  <CardTitle>Results</CardTitle>
                  <CardDescription>Highlighted: the recommended level. Auto-tune rows list the flag variant they ran with.</CardDescription>
                </div>
                <Button
                  variant="outline"
                  size="sm"
                  className="rounded-sm"
                  disabled={selected.results.length === 0}
                  onClick={() => downloadFromApi(`/benchmark-runs/${selected.id}/export`, `benchmark-run-${selected.id.slice(0, 8)}.csv`).catch((e: unknown) => toast.error(e instanceof Error ? e.message : 'Export failed'))}
                >
                  <Download className="size-4" />
                  CSV
                </Button>
              </CardHeader>
              <CardContent>{selected.results.length ? <ResultsTable results={selected.results} best={selected.best_concurrency} /> : <p className="py-4 text-center text-sm text-muted-foreground">No levels finished yet.</p>}</CardContent>
            </Card>
          </div>
        )}
      </div>

      <DeleteConfirmationDialog open={deleteOpen} onOpenChange={setDeleteOpen} itemName={b.name} resourceType="benchmark" warningMessage="All of its runs and results are deleted too." isDeleting={remove.isPending} onConfirm={() => remove.mutate(b.id, { onSuccess: () => router.push('/benchmarks') })} />
    </PageContainer>
  );
}
