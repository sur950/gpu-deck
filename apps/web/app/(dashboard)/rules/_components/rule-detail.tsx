'use client';

import { useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { useParams, useRouter, useSearchParams } from 'next/navigation';
import { Copy, Edit, FlaskConical, History, MoreVertical, Play, Radio, Trash2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Switch } from '@/components/ui/switch';
import { Skeleton } from '@/components/ui/skeleton';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from '@/components/ui/dropdown-menu';
import { PageContainer } from '@/components/shared/PageContainer';
import { PageHeader } from '@/components/shared/PageHeader';
import { ErrorState } from '@/components/shared/ErrorState';
import { EmptyState } from '@/components/shared/EmptyState';
import { RelativeTime } from '@/components/shared/relative-time';
import { ConfirmationDialog } from '@/components/shared/ConfirmationDialog';
import { DeleteConfirmationDialog } from '@/components/shared/DeleteConfirmationDialog';
import { DashboardSkeleton } from '@/components/charts/dashboard-skeleton';
import { useDeleteRule, useRule, useRuleRuns, useRuleSchemas, useRunRuleNow, useToggleRule } from '@/hooks/api/automation';
import type { RuleRun, RuleRunOutcome } from '@/hooks/api/automation-api';
import { cn } from '@/lib/utils';
import { behaviourSentence, describeTarget, formatSeconds, RULE_TYPE_META, RuleGlyph, ruleSentence } from '@/components/automation/rule-meta';
import { RuleSentence } from '@/components/automation/rule-sentence';
import { nextEvaluation, OUTCOME_META } from '@/components/automation/run-meta';
import { Countdown } from '@/components/automation/countdown';
import { DryRunDialog } from '@/components/automation/dry-run-dialog';
import { DecisionView, formatDecisionValue } from '@/components/automation/decision-view';
import { OutcomeStrip, RunTimeline } from '@/components/automation/run-timeline';
import { useRefNames } from '@/components/automation/use-ref-names';
import { useNow } from '@/components/automation/use-now';
import { FIELD_COPY, parseTitle } from '@/components/automation/schema-utils';
import { isEmptyTarget } from '@/components/automation/target-match';

type OutcomeFilter = 'all' | 'acted' | RuleRunOutcome;

const FILTERS: { value: OutcomeFilter; label: string }[] = [
  { value: 'all', label: 'All' },
  { value: 'acted', label: 'Acted or would act' },
  { value: 'skipped', label: 'Held back' },
  { value: 'cooldown', label: 'Cooldown' },
  { value: 'error', label: 'Errors' },
  { value: 'no_match', label: 'No match' },
];

function Tile({ label, children, hint }: { label: string; children: React.ReactNode; hint?: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-border bg-background/50 px-3.5 py-3">
      <p className="text-[11px] font-medium text-muted-foreground">{label}</p>
      <p className="mt-1 text-[15px] font-semibold tabular-nums tracking-tight">{children}</p>
      {hint && <p className="mt-0.5 truncate text-[11px] text-muted-foreground">{hint}</p>}
    </div>
  );
}

export function RuleDetail() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const searchParams = useSearchParams();
  const refs = useRefNames();
  const { schemas } = useRuleSchemas();
  const { data: rule, isLoading, isError, refetch } = useRule(id);
  const [filter, setFilter] = useState<OutcomeFilter>('all');
  const [pageSize, setPageSize] = useState(30);
  const [selectedId, setSelectedId] = useState<string | null>(searchParams.get('run'));
  const [dryOpen, setDryOpen] = useState(searchParams.get('dryrun') === '1');
  const [runOpen, setRunOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [window24] = useState(() => ({ from: Date.now() - 24 * 3_600_000, to: Date.now() }));
  const now = useNow(30_000);

  const outcome = filter === 'all' || filter === 'acted' ? '' : filter;
  const runsQuery = useRuleRuns(id, { pageSize: filter === 'acted' ? 200 : pageSize, outcome });
  const stripQuery = useRuleRuns(id, { pageSize: 500, from: new Date(window24.from).toISOString() });
  const toggle = useToggleRule();
  const runNow = useRunRuleNow();
  const del = useDeleteRule();

  // Strip the one-shot ?dryrun=1 from the address once the dialog is open.
  useEffect(() => {
    if (searchParams.get('dryrun') === '1') router.replace(`/rules/${id}`, { scroll: false });
  }, [id, router, searchParams]);

  const runs = useMemo(() => {
    const list = runsQuery.data?.items ?? [];
    return filter === 'acted' ? list.filter((r) => r.outcome === 'fired' || r.outcome === 'dry_run').slice(0, pageSize) : list;
  }, [runsQuery.data, filter, pageSize]);
  const selected: RuleRun | undefined = runs.find((r) => r.id === selectedId) ?? runs.find((r) => r.outcome !== 'no_match') ?? runs[0];

  if (isLoading) return <DashboardSkeleton />;
  if (isError || !rule) {
    return (
      <PageContainer>
        <ErrorState title="Rule not found" description="It may have been deleted, or the control plane did not answer." onRetry={() => void refetch()} />
      </PageContainer>
    );
  }

  const meta = RULE_TYPE_META[rule.type];
  const Icon = meta.icon;
  const next = nextEvaluation(rule, now);
  const paramsSchema = schemas.params[rule.type];
  const stripRuns = stripQuery.data?.items ?? [];
  const total = runsQuery.data?.total ?? 0;
  const hasMore = filter !== 'acted' && runs.length < total;

  return (
    <PageContainer>
      <PageHeader
        title={rule.name}
        description={rule.description ?? meta.tagline}
        actions={
          <>
            <label className="mr-1 flex items-center gap-2 text-xs font-medium text-muted-foreground">
              {rule.enabled ? 'Enabled' : 'Disabled'}
              <Switch checked={rule.enabled} onCheckedChange={(v) => toggle.mutate({ id: rule.id, enabled: v })} aria-label={rule.enabled ? 'Disable rule' : 'Enable rule'} />
            </label>
            <Button variant="outline" size="sm" className="rounded-sm" onClick={() => setDryOpen(true)}>
              <FlaskConical className="size-4" /> Dry run now
            </Button>
            {!rule.dryRun && (
              <Button variant="outline" size="sm" className="rounded-sm" onClick={() => setRunOpen(true)}>
                <Play className="size-4" /> Run now
              </Button>
            )}
            <Button size="sm" className="rounded-sm" onClick={() => router.push(`/rules/${rule.id}/edit`)}>
              <Edit className="size-4" /> Edit
            </Button>
            <DropdownMenu>
              <DropdownMenuTrigger render={<Button variant="ghost" size="icon-sm" aria-label="More actions" />}>
                <MoreVertical className="size-4" />
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-44">
                <DropdownMenuItem onClick={() => router.push(`/rules/new?from=${rule.id}`)}>
                  <Copy className="size-4" /> Duplicate
                </DropdownMenuItem>
                <DropdownMenuSeparator />
                <DropdownMenuItem className="text-destructive" onClick={() => setDeleteOpen(true)}>
                  <Trash2 className="size-4" /> Delete
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </>
        }
      />

      {/* Sentence band */}
      <section className="relative overflow-hidden rounded-2xl border border-border bg-card px-5 py-6 shadow-premium sm:px-8">
        <div aria-hidden className="bg-dot-grid pointer-events-none absolute inset-0 [mask-image:linear-gradient(to_left,black,transparent_60%)]" />
        <div aria-hidden className="pointer-events-none absolute -right-16 -top-24 size-64 rounded-full bg-brand/10 blur-3xl" />
        <div className="relative grid gap-6 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-center">
          <div className="space-y-3">
            <div className="flex flex-wrap items-center gap-2">
              <span className="inline-flex items-center gap-1.5 rounded-full border border-border bg-background/70 px-2.5 py-1 text-[11px] font-medium text-muted-foreground backdrop-blur">
                <Icon className="size-3.5 text-foreground/80" />
                {meta.label}
              </span>
              <span className={cn('inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] font-medium', rule.dryRun ? 'border-info/30 bg-info/10 text-info-dark dark:text-info-light' : 'border-success/30 bg-success/10 text-success-dark dark:text-success-light')}>
                {rule.dryRun ? <FlaskConical className="size-3" /> : <Radio className="size-3" />}
                {rule.dryRun ? 'Dry run — records only' : 'Live — acts on the fleet'}
              </span>
              {!rule.enabled && <span className="rounded-full border border-border bg-muted px-2.5 py-1 text-[11px] font-medium text-muted-foreground">Disabled</span>}
            </div>
            <RuleSentence parts={ruleSentence(rule.type, rule.params, rule.target, refs)} size="lg" className="max-w-3xl text-foreground/70" />
            <RuleSentence parts={behaviourSentence({ dryRun: rule.dryRun, cooldownSeconds: rule.cooldownSeconds, type: rule.type })} size="sm" />
          </div>
          <RuleGlyph type={rule.type} className="hidden h-16 w-36 text-brand/70 lg:block" />
        </div>
        <div className="relative mt-6 grid gap-3 sm:grid-cols-3 xl:grid-cols-6">
          <Tile label="Next evaluation">{rule.enabled ? <Countdown at={next} /> : 'paused'}</Tile>
          <Tile label="Last evaluated">{rule.lastEvaluatedAt ? <RelativeTime value={rule.lastEvaluatedAt} /> : '—'}</Tile>
          <Tile label="Last fired" hint={rule.lastFiredAt ? new Date(rule.lastFiredAt).toLocaleString() : undefined}>
            {rule.lastFiredAt ? <RelativeTime value={rule.lastFiredAt} /> : 'never'}
          </Tile>
          <Tile label="Times fired">{rule.fireCount.toLocaleString()}</Tile>
          <Tile label="Cooldown">{rule.type === 'time_schedule' ? '—' : rule.cooldownSeconds ? formatSeconds(rule.cooldownSeconds) : 'none'}</Tile>
          <Tile label={rule.type === 'time_schedule' ? 'Fires' : 'Checked every'} hint={`order ${rule.priority}`}>
            {rule.type === 'time_schedule' ? 'on schedule' : formatSeconds(rule.evaluationIntervalSeconds)}
          </Tile>
        </div>
      </section>

      {/* Run history */}
      <Card className="gap-0 rounded-xl py-0">
        <CardHeader className="flex flex-col gap-3 border-b border-border px-5 py-4 lg:flex-row lg:items-center lg:justify-between">
          <div className="space-y-0.5">
            <CardTitle className="flex items-center gap-2 text-sm font-semibold">
              <History className="size-4" /> Run history
            </CardTitle>
            <CardDescription>Every evaluation that acted, would have acted or was held back — plus hourly “nothing to do” checkpoints.</CardDescription>
          </div>
          <div className="flex flex-wrap gap-1" role="group" aria-label="Filter by outcome">
            {FILTERS.map((f) => (
              <button
                key={f.value}
                type="button"
                aria-pressed={filter === f.value}
                onClick={() => {
                  setFilter(f.value);
                  setSelectedId(null);
                }}
                className={cn('h-7 rounded-full border px-3 text-[11px] font-medium outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring/30', filter === f.value ? 'border-foreground/30 bg-foreground text-background' : 'border-border text-muted-foreground hover:text-foreground')}
              >
                {f.label}
              </button>
            ))}
          </div>
        </CardHeader>
        <CardContent className="space-y-5 px-5 py-5">
          <div>
            <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
              <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">Last 24 hours</p>
              <ul className="flex flex-wrap gap-3 text-[11px] text-muted-foreground">
                {(['fired', 'dry_run', 'skipped', 'cooldown', 'error', 'no_match'] as RuleRunOutcome[]).map((o) => (
                  <li key={o} className="flex items-center gap-1.5">
                    <span className={cn('h-2.5 w-[3px] rounded-full', OUTCOME_META[o].dot)} />
                    {OUTCOME_META[o].label} <span className="tabular-nums text-foreground/80">{stripRuns.filter((r) => r.outcome === o).length}</span>
                  </li>
                ))}
              </ul>
            </div>
            {stripQuery.isLoading ? <Skeleton className="h-9" /> : <OutcomeStrip runs={stripRuns} fromMs={window24.from} toMs={Math.max(window24.to, now)} selectedId={selected?.id} onSelect={(r) => { setFilter('all'); setSelectedId(r.id); }} />}
          </div>

          {runsQuery.isLoading ? (
            <div className="grid gap-6 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
              <div className="space-y-2">{Array.from({ length: 7 }).map((_, i) => <Skeleton key={i} className="h-11" />)}</div>
              <Skeleton className="h-80" />
            </div>
          ) : runs.length === 0 ? (
            <EmptyState icon={History} title={filter === 'all' ? 'No runs yet' : 'No runs with this outcome'} description={filter === 'all' ? 'The evaluator has not looked at this rule yet. “Dry run now” evaluates it immediately.' : 'Pick another filter or run a dry run.'} />
          ) : (
            <div className="grid gap-6 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
              <div className="max-h-[640px] overflow-y-auto pr-1">
                <RunTimeline runs={runs} selectedId={selected?.id ?? null} onSelect={(r) => setSelectedId(r.id)} />
                {hasMore && (
                  <Button variant="ghost" size="sm" className="mt-2 w-full text-muted-foreground" onClick={() => setPageSize((s) => s + 30)} disabled={runsQuery.isFetching}>
                    Load older runs ({total - runs.length} more)
                  </Button>
                )}
              </div>
              <div className="lg:sticky lg:top-4 lg:self-start">
                <div className="rounded-xl border border-border bg-background/40 p-4">{selected ? <DecisionView key={selected.id} run={selected} /> : null}</div>
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Configuration */}
      <Card className="gap-0 rounded-xl py-0">
        <CardHeader className="border-b border-border px-5 py-4">
          <CardTitle className="text-sm font-semibold">Configuration</CardTitle>
          <CardDescription>The stored parameters, exactly as the evaluator reads them.</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-6 px-5 py-5 lg:grid-cols-2">
          <dl className="grid gap-1.5">
            {Object.entries(rule.params).map(([key, value]) => {
              const title = parseTitle(paramsSchema?.properties?.[key]?.title);
              const label = FIELD_COPY[key]?.label ?? (title.label || key);
              return (
                <div key={key} className="flex items-baseline justify-between gap-4 rounded-lg bg-muted/50 px-3 py-2">
                  <dt className="flex items-center gap-2 text-xs text-muted-foreground">
                    {title.variable && <span className="font-mono text-[10px] font-semibold text-foreground/70">{title.variable}</span>}
                    {label}
                  </dt>
                  <dd className="text-right text-xs font-medium tabular-nums">{key === 'layout_id' && typeof value === 'string' ? <Link href={`/layouts/${value}`} className="hover:underline">{refs.layouts.get(value) ?? value}</Link> : formatDecisionValue(key, value)}</dd>
                </div>
              );
            })}
          </dl>
          <div className="space-y-3">
            <div className="rounded-lg border border-border p-3">
              <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">Targets</p>
              <p className="mt-1 text-xs">{rule.type === 'time_schedule' ? 'Defined by the layout' : isEmptyTarget(rule.target) ? 'Every deployment' : describeTarget(rule.target, refs)}</p>
            </div>
            <dl className="grid grid-cols-2 gap-1.5 text-xs">
              <div className="rounded-lg bg-muted/50 px-3 py-2">
                <dt className="text-muted-foreground">Created</dt>
                <dd className="font-medium">{new Date(rule.createdAt).toLocaleString()}</dd>
              </div>
              <div className="rounded-lg bg-muted/50 px-3 py-2">
                <dt className="text-muted-foreground">Updated</dt>
                <dd className="font-medium">{rule.updatedAt ? <RelativeTime value={rule.updatedAt} /> : '—'}</dd>
              </div>
            </dl>
          </div>
        </CardContent>
      </Card>

      <DryRunDialog rule={dryOpen ? rule : null} open={dryOpen} onOpenChange={setDryOpen} />
      <ConfirmationDialog
        open={runOpen}
        onOpenChange={setRunOpen}
        title={`Run “${rule.name}” now?`}
        description="Evaluates immediately, ignoring the cooldown, and acts if the condition holds. Any swaps it starts show up on the Swaps page."
        confirmText="Run now"
        onConfirm={() => runNow.mutate(rule.id, { onSuccess: (run) => setSelectedId(run.id) })}
        isLoading={runNow.isPending}
      />
      <DeleteConfirmationDialog
        open={deleteOpen}
        onOpenChange={setDeleteOpen}
        itemName={rule.name}
        resourceType="rule"
        warningMessage="The rule stops being evaluated immediately. Past runs stay in the audit log."
        isDeleting={del.isPending}
        onConfirm={() => del.mutate(rule.id, { onSuccess: () => router.push('/rules') })}
      />
    </PageContainer>
  );
}
