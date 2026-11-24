'use client';

import { useMemo, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { ArrowLeftRight, CircleCheck, Hourglass, Loader2, Play, Timer, TriangleAlert, X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Skeleton } from '@/components/ui/skeleton';
import { PageContainer } from '@/components/shared/PageContainer';
import { PageHeader } from '@/components/shared/PageHeader';
import { ListPageSkeleton } from '@/components/shared/ListPageSkeleton';
import { ErrorState } from '@/components/shared/ErrorState';
import { EmptyState } from '@/components/shared/EmptyState';
import { StatusBadge } from '@/components/shared/StatusBadge';
import { StatCard } from '@/components/shared/stat-card';
import { TablePagination } from '@/components/shared/table-pagination';
import { RelativeTime } from '@/components/shared/relative-time';
import { ConfirmationDialog } from '@/components/shared/ConfirmationDialog';
import { useCancelSwap, useSwaps } from '@/hooks/api/automation';
import type { SwapKind, SwapOperation, SwapState, SwapTrigger } from '@/hooks/api/automation-api';
import { useUrlState } from '@/hooks/use-url-state';
import { cn } from '@/lib/utils';
import { formatMs, isActive, KIND_META, STATE_META, swapDurationMs, TRIGGER_LABELS } from '@/components/automation/swap-meta';
import { SwapPipeline, SwapStepsMini } from '@/components/automation/swap-pipeline';
import { StartSwapDialog } from '@/components/automation/start-swap-dialog';
import { useNow } from '@/components/automation/use-now';

const DEFAULTS = { state: '', kind: '', trigger: '', page: 1, pageSize: 25 };

const STATE_FILTERS: { value: string; label: string }[] = [
  { value: 'all', label: 'All states' },
  { value: 'completed', label: 'Completed' },
  { value: 'failed', label: 'Failed' },
  { value: 'rolled_back', label: 'Rolled back' },
  { value: 'cancelled', label: 'Cancelled' },
];

function TriggerCell({ swap }: { swap: SwapOperation }) {
  return (
    <div className="min-w-0 text-xs">
      <p className="font-medium">{TRIGGER_LABELS[swap.trigger]}</p>
      {swap.ruleName ? (
        <p className="truncate text-[11px] text-muted-foreground">{swap.ruleName}</p>
      ) : swap.layoutId ? (
        <Link href={`/layouts/${swap.layoutId}`} onClick={(e) => e.stopPropagation()} className="text-[11px] text-muted-foreground hover:text-foreground">
          layout
        </Link>
      ) : null}
    </div>
  );
}

export function SwapsList() {
  const router = useRouter();
  const now = useNow(1000);
  const { params, setParams } = useUrlState(DEFAULTS);
  const [startOpen, setStartOpen] = useState(false);
  const [cancelling, setCancelling] = useState<SwapOperation | null>(null);
  const live = useSwaps({ activeOnly: true, pageSize: 50 }, { refetchInterval: 2_000 });
  const [since] = useState(() => new Date(Date.now() - 24 * 3_600_000).toISOString());
  const day = useSwaps({ from: since, pageSize: 500 }, { refetchInterval: 15_000 });
  const query = useMemo(
    () => ({ state: params.state as SwapState | '', kind: params.kind as SwapKind | '', trigger: params.trigger as SwapTrigger | '', page: params.page, pageSize: params.pageSize }),
    [params.state, params.kind, params.trigger, params.page, params.pageSize],
  );
  const history = useSwaps(query, { refetchInterval: (live.data?.items.length ?? 0) > 0 ? 5_000 : false });
  const cancel = useCancelSwap();

  const active = live.data?.items ?? [];
  const dayItems = day.data?.items ?? [];
  const done = dayItems.filter((s) => s.state === 'completed');
  const bad = dayItems.filter((s) => s.state === 'failed' || s.state === 'rolled_back');
  const durations = done.map((s) => swapDurationMs(s, now)).filter((d): d is number => d !== null).sort((a, b) => a - b);
  const median = durations.length ? durations[Math.floor(durations.length / 2)] : null;

  if (history.isLoading && !history.data) return <ListPageSkeleton />;

  return (
    <PageContainer>
      <PageHeader
        title="Swaps"
        description="Every load, unload, move and restart — drained first, health-checked after, rolled back if a step fails."
        actions={
          <Button size="sm" className="rounded-sm" onClick={() => setStartOpen(true)}>
            <Play className="size-4" /> Start a swap
          </Button>
        }
      />

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard label="In progress" value={active.length} icon={Loader2} footnote={active.length ? 'Updating every 2 s' : 'Nothing moving right now'} />
        <StatCard label="Completed (24 h)" value={done.length} icon={CircleCheck} footnote={`${dayItems.length} swaps started in 24 h`} />
        <StatCard label="Failed or rolled back (24 h)" value={bad.length} icon={TriangleAlert} footnote={bad.length ? 'Rolled-back swaps kept the old instance serving' : 'No failures'} />
        <StatCard label="Median duration (24 h)" value={formatMs(median)} icon={Timer} footnote="Completed swaps, drain included" />
      </div>

      <Card className="gap-0 rounded-xl py-0">
        <CardHeader className="flex flex-row items-center justify-between border-b border-border px-5 py-4">
          <div className="space-y-0.5">
            <CardTitle className="flex items-center gap-2 text-sm font-semibold">
              <span className="relative flex size-2">
                {active.length > 0 && <span className="absolute inline-flex size-full animate-ping rounded-full bg-info/60" />}
                <span className={cn('relative inline-flex size-2 rounded-full', active.length ? 'bg-info' : 'bg-muted-foreground/40')} />
              </span>
              Live
            </CardTitle>
            <CardDescription>drain → wait for 0 in flight → unload → load → health → route back</CardDescription>
          </div>
        </CardHeader>
        <CardContent className="px-5 py-4">
          {live.isLoading ? (
            <Skeleton className="h-24" />
          ) : active.length === 0 ? (
            <div className="flex items-center gap-3 py-3 text-xs text-muted-foreground">
              <Hourglass className="size-4" /> No swap is running. Rules, schedules and manual swaps appear here while they run.
            </div>
          ) : (
            <ul className="space-y-3">
              {active.map((swap) => {
                const meta = KIND_META[swap.kind];
                const Icon = meta.icon;
                return (
                  <li key={swap.id} className="rounded-lg border border-info/25 bg-info/[0.03] p-4">
                    <div className="mb-4 flex flex-wrap items-center gap-3">
                      <span className="flex size-8 items-center justify-center rounded-lg border border-border bg-muted/60">
                        <Icon className="size-4" />
                      </span>
                      <div className="min-w-0">
                        <Link href={`/swaps/${swap.id}`} className="text-[13px] font-semibold hover:underline">
                          {meta.label} {swap.deploymentName ?? ''}
                        </Link>
                        <p className="text-[11px] text-muted-foreground">
                          {TRIGGER_LABELS[swap.trigger]}
                          {swap.ruleName ? ` · ${swap.ruleName}` : ''} · running {formatMs(swapDurationMs(swap, now))}
                        </p>
                      </div>
                      <StatusBadge variant={STATE_META[swap.state].badge} className="ml-auto">
                        {STATE_META[swap.state].label}
                      </StatusBadge>
                      <Button variant="ghost" size="xs" onClick={() => setCancelling(swap)}>
                        <X className="size-3" /> Cancel
                      </Button>
                    </div>
                    <SwapPipeline steps={swap.steps} now={now} size="lg" />
                  </li>
                );
              })}
            </ul>
          )}
        </CardContent>
      </Card>

      {history.isError ? (
        <ErrorState title="Could not load swaps" description="The control plane did not answer. Retry in a moment." onRetry={() => void history.refetch()} />
      ) : (
        <Card className="rounded-md shadow-premium">
          <CardHeader>
            <div className="flex flex-wrap items-center gap-3">
              <Select value={params.state || 'all'} onValueChange={(v) => setParams({ state: v === 'all' ? '' : String(v), page: 1 })}>
                <SelectTrigger className="w-40">
                  <SelectValue>{(v: unknown) => STATE_FILTERS.find((o) => o.value === v)?.label ?? 'All states'}</SelectValue>
                </SelectTrigger>
                <SelectContent>
                  {STATE_FILTERS.map((o) => (
                    <SelectItem key={o.value} value={o.value}>
                      {o.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Select value={params.kind || 'all'} onValueChange={(v) => setParams({ kind: v === 'all' ? '' : String(v), page: 1 })}>
                <SelectTrigger className="w-40">
                  <SelectValue>{(v: unknown) => (v && v !== 'all' ? KIND_META[v as SwapKind].label : 'All operations')}</SelectValue>
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">All operations</SelectItem>
                  {(Object.keys(KIND_META) as SwapKind[]).map((k) => (
                    <SelectItem key={k} value={k}>
                      {KIND_META[k].label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Select value={params.trigger || 'all'} onValueChange={(v) => setParams({ trigger: v === 'all' ? '' : String(v), page: 1 })}>
                <SelectTrigger className="w-40">
                  <SelectValue>{(v: unknown) => (v && v !== 'all' ? TRIGGER_LABELS[v as SwapTrigger] : 'All triggers')}</SelectValue>
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">All triggers</SelectItem>
                  {(Object.keys(TRIGGER_LABELS) as SwapTrigger[]).map((t) => (
                    <SelectItem key={t} value={t}>
                      {TRIGGER_LABELS[t]}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </CardHeader>
          <CardContent>
            {(history.data?.items.length ?? 0) === 0 ? (
              <EmptyState icon={ArrowLeftRight} title="No swaps match" description="Swaps are created by rules, layouts, auto-heal and the button above." />
            ) : (
              <>
                <div className="overflow-x-auto">
                  <Table>
                    <TableHeader className="sticky top-0 z-10 bg-card">
                      <TableRow>
                        <TableHead>Operation</TableHead>
                        <TableHead>Trigger</TableHead>
                        <TableHead>Steps</TableHead>
                        <TableHead>Started</TableHead>
                        <TableHead className="text-right">Duration</TableHead>
                        <TableHead className="text-right">State</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {history.data?.items.map((swap) => {
                        const meta = KIND_META[swap.kind];
                        const Icon = meta.icon;
                        return (
                          <TableRow key={swap.id} className="cursor-pointer" onClick={() => router.push(`/swaps/${swap.id}`)}>
                            <TableCell>
                              <div className="flex items-center gap-3">
                                <span className="flex size-7 items-center justify-center rounded-md border border-border bg-muted/60">
                                  <Icon className="size-3.5" />
                                </span>
                                <div className="min-w-0">
                                  <Link href={`/swaps/${swap.id}`} onClick={(e) => e.stopPropagation()} className="text-[13px] font-semibold hover:underline">
                                    {meta.label} {swap.deploymentName ?? ''}
                                  </Link>
                                  {swap.errorMessage ? <p className="max-w-[360px] truncate text-[11px] text-error-dark dark:text-error-light">{swap.errorMessage}</p> : <p className="max-w-[360px] truncate text-[11px] text-muted-foreground">{String(swap.plan.reason ?? '')}</p>}
                                </div>
                              </div>
                            </TableCell>
                            <TableCell>
                              <TriggerCell swap={swap} />
                            </TableCell>
                            <TableCell>
                              <SwapStepsMini steps={swap.steps} />
                            </TableCell>
                            <TableCell className="text-xs">
                              <RelativeTime value={swap.startedAt ?? swap.createdAt} />
                            </TableCell>
                            <TableCell className="text-right font-mono text-xs tabular-nums">{formatMs(swapDurationMs(swap, now))}</TableCell>
                            <TableCell className="text-right">
                              <StatusBadge variant={STATE_META[swap.state].badge}>
                                {isActive(swap.state) && <Loader2 className="mr-1 size-3 animate-spin" />}
                                {STATE_META[swap.state].label}
                                {swap.dryRun ? ' · dry run' : ''}
                              </StatusBadge>
                            </TableCell>
                          </TableRow>
                        );
                      })}
                    </TableBody>
                  </Table>
                </div>
                <TablePagination page={params.page} pageSize={params.pageSize} total={history.data?.total ?? 0} onPageChange={(p) => setParams({ page: p })} onPageSizeChange={(s) => setParams({ pageSize: s, page: 1 })} noun="swaps" />
              </>
            )}
          </CardContent>
        </Card>
      )}

      <StartSwapDialog open={startOpen} onOpenChange={setStartOpen} />
      <ConfirmationDialog
        open={!!cancelling}
        onOpenChange={(o) => !o && setCancelling(null)}
        title="Cancel this swap?"
        description="The swap manager stops before the next step and restores routing to the instance that was serving. A step already running is allowed to finish or time out."
        confirmText="Cancel swap"
        cancelText="Keep running"
        variant="destructive"
        onConfirm={() => cancelling && cancel.mutate(cancelling.id)}
        isLoading={cancel.isPending}
      />
    </PageContainer>
  );
}
