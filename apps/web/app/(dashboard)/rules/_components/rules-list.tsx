'use client';

import { useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { Copy, Edit, FlaskConical, MoreVertical, Play, Plus, Radio, Timer, Trash2, Workflow, Zap } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Switch } from '@/components/ui/switch';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from '@/components/ui/dropdown-menu';
import { Skeleton } from '@/components/ui/skeleton';
import { PageContainer } from '@/components/shared/PageContainer';
import { PageHeader } from '@/components/shared/PageHeader';
import { ListPageSkeleton } from '@/components/shared/ListPageSkeleton';
import { ErrorState } from '@/components/shared/ErrorState';
import { EmptyState } from '@/components/shared/EmptyState';
import { StatusBadge } from '@/components/shared/StatusBadge';
import { StatCard } from '@/components/shared/stat-card';
import { SearchInput } from '@/components/shared/search-input';
import { TablePagination } from '@/components/shared/table-pagination';
import { RelativeTime } from '@/components/shared/relative-time';
import { ConfirmationDialog } from '@/components/shared/ConfirmationDialog';
import { DeleteConfirmationDialog } from '@/components/shared/DeleteConfirmationDialog';
import { useAllRuleRuns, useDeleteRule, useRules, useRunRuleNow, useToggleRule } from '@/hooks/api/automation';
import { RULE_TYPES, type Rule, type RuleRunOutcome, type RuleType } from '@/hooks/api/automation-api';
import { useUrlState } from '@/hooks/use-url-state';
import { useDebounce } from '@/hooks/use-debounce';
import { cn } from '@/lib/utils';
import { RULE_TYPE_META, ruleSentence, sentenceText } from '@/components/automation/rule-meta';
import { nextEvaluation, OUTCOME_META, summarizeRun } from '@/components/automation/run-meta';
import { Countdown } from '@/components/automation/countdown';
import { DryRunDialog } from '@/components/automation/dry-run-dialog';
import { useRefNames } from '@/components/automation/use-ref-names';
import { useNow } from '@/components/automation/use-now';

const DEFAULTS = { search: '', type: '', status: '', page: 1, pageSize: 25 };

const STATUS_OPTIONS = [
  { value: 'all', label: 'All rules' },
  { value: 'enabled', label: 'Enabled' },
  { value: 'disabled', label: 'Disabled' },
  { value: 'live', label: 'Live only' },
  { value: 'dry_run', label: 'In dry run' },
];

function OutcomeBadge({ outcome }: { outcome: RuleRunOutcome }) {
  const meta = OUTCOME_META[outcome];
  return (
    <span className={cn('inline-flex items-center gap-1.5 text-xs font-medium', meta.text)}>
      <span className={cn('size-1.5 rounded-full', meta.dot)} />
      {meta.label}
    </span>
  );
}

export function RulesList() {
  const router = useRouter();
  const { params, setParams } = useUrlState(DEFAULTS);
  const [search, setSearch] = useState(params.search);
  const debouncedSearch = useDebounce(search, 300);
  const [dryRunRule, setDryRunRule] = useState<Rule | null>(null);
  const [runRule, setRunRule] = useState<Rule | null>(null);
  const [deleting, setDeleting] = useState<Rule | null>(null);
  const refs = useRefNames();
  const now = useNow(15_000);

  const urlSearch = params.search;
  useEffect(() => {
    if (debouncedSearch !== urlSearch) setParams({ search: debouncedSearch, page: 1 });
  }, [debouncedSearch, urlSearch, setParams]);

  const enabledFilter = params.status === 'enabled' ? true : params.status === 'disabled' ? false : null;
  const query = useMemo(
    () => ({ search: debouncedSearch, type: params.type as RuleType | '', enabled: enabledFilter, page: params.page, pageSize: params.pageSize, sort: 'priority' }),
    [debouncedSearch, params.type, enabledFilter, params.page, params.pageSize],
  );
  const { data, isLoading, isError, refetch } = useRules(query);
  const all = useRules({ pageSize: 500 });
  const [since] = useState(() => new Date(Date.now() - 24 * 3_600_000).toISOString());
  const recent = useAllRuleRuns({ pageSize: 60, from: since });
  const toggle = useToggleRule();
  const del = useDeleteRule();
  const runNow = useRunRuleNow();

  const rules = (data?.items ?? []).filter((r) => (params.status === 'dry_run' ? r.dryRun : params.status === 'live' ? !r.dryRun : true));
  const everything = all.data?.items ?? [];
  const enabledCount = everything.filter((r) => r.enabled).length;
  const dryCount = everything.filter((r) => r.enabled && r.dryRun).length;
  const recentRuns = recent.data?.items ?? [];
  const acted24h = recentRuns.filter((r) => r.outcome === 'fired').length;
  const would24h = recentRuns.filter((r) => r.outcome === 'dry_run').length;
  const notable = recentRuns.filter((r) => r.outcome !== 'no_match').slice(0, 7);
  const soonest = everything
    .map((r) => ({ r, at: nextEvaluation(r, now) }))
    .filter((x): x is { r: Rule; at: number } => x.at !== null && x.r.type === 'time_schedule')
    .sort((a, b) => a.at - b.at)[0];

  if (isLoading && !data) return <ListPageSkeleton />;
  const filtered = !!debouncedSearch || !!params.type || !!params.status;

  return (
    <PageContainer>
      <PageHeader
        title="Rules"
        description="Unload idle models, follow a schedule, shrink and grow with traffic, make room by priority and heal failing instances."
        actions={
          <Button size="sm" className="rounded-sm" onClick={() => router.push('/rules/new')}>
            <Plus className="size-4" />
            New rule
          </Button>
        }
      />

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard label="Enabled rules" value={`${enabledCount} / ${everything.length}`} icon={Workflow} footnote={`${everything.length - enabledCount} disabled`} />
        <StatCard label="Watching in dry run" value={dryCount} icon={FlaskConical} footnote={`${would24h} would-act decisions in 24 h`} />
        <StatCard label="Acted in the last 24 h" value={acted24h} icon={Zap} footnote="Fired runs that started swaps" />
        <StatCard
          label="Next scheduled switch"
          value={soonest ? <Countdown at={soonest.at} /> : '—'}
          icon={Timer}
          footnote={soonest ? `${soonest.r.name} · ${new Date(soonest.at).toLocaleString(undefined, { weekday: 'short', hour: '2-digit', minute: '2-digit' })}` : 'No time schedules enabled'}
        />
      </div>

      {isError ? (
        <ErrorState title="Could not load rules" description="The control plane did not answer. Retry in a moment." onRetry={() => void refetch()} />
      ) : (
        <div className="grid gap-6 2xl:grid-cols-[minmax(0,1fr)_340px]">
          <Card className="rounded-md shadow-premium">
            <CardHeader>
              <div className="flex flex-wrap items-center gap-3">
                <SearchInput value={search} onChange={setSearch} placeholder="Search rules…" />
                <Select value={params.type || 'all'} onValueChange={(v) => setParams({ type: v === 'all' ? '' : String(v), page: 1 })}>
                  <SelectTrigger className="w-48">
                    <SelectValue>{(v: unknown) => (v && v !== 'all' ? RULE_TYPE_META[v as RuleType].label : 'All types')}</SelectValue>
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">All types</SelectItem>
                    {RULE_TYPES.map((t) => (
                      <SelectItem key={t} value={t}>
                        {RULE_TYPE_META[t].label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <Select value={params.status || 'all'} onValueChange={(v) => setParams({ status: v === 'all' ? '' : String(v), page: 1 })}>
                  <SelectTrigger className="w-40">
                    <SelectValue>{(v: unknown) => STATUS_OPTIONS.find((o) => o.value === v)?.label ?? 'All rules'}</SelectValue>
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
              {rules.length === 0 ? (
                <EmptyState
                  icon={Workflow}
                  title={filtered ? 'No rules match' : 'No rules yet'}
                  description={filtered ? 'Try a different search or clear the filters.' : 'Start with an idle-unload rule in dry run: it records what it would free without touching anything.'}
                  action={
                    !filtered && (
                      <Button size="sm" className="rounded-sm" onClick={() => router.push('/rules/new?type=idle_unload')}>
                        <Plus className="size-4" />
                        New rule
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
                          <TableHead className="w-12">
                            <span className="sr-only">Enabled</span>
                          </TableHead>
                          <TableHead>Rule</TableHead>
                          <TableHead>Mode</TableHead>
                          <TableHead>Last decision</TableHead>
                          <TableHead>Last fired</TableHead>
                          <TableHead>Next evaluation</TableHead>
                          <TableHead className="text-right">Fired</TableHead>
                          <TableHead className="w-10" />
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {rules.map((rule) => {
                          const meta = RULE_TYPE_META[rule.type];
                          const Icon = meta.icon;
                          const next = nextEvaluation(rule, now);
                          return (
                            <TableRow key={rule.id} className={cn('cursor-pointer', !rule.enabled && 'opacity-60')} onClick={() => router.push(`/rules/${rule.id}`)}>
                              <TableCell onClick={(e) => e.stopPropagation()}>
                                <Switch checked={rule.enabled} onCheckedChange={(v) => toggle.mutate({ id: rule.id, enabled: v })} aria-label={`${rule.enabled ? 'Disable' : 'Enable'} ${rule.name}`} />
                              </TableCell>
                              <TableCell className="max-w-[420px]">
                                <div className="flex items-start gap-3">
                                  <span className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-lg border border-border bg-muted/60">
                                    <Icon className="size-4 text-foreground/80" />
                                  </span>
                                  <div className="min-w-0">
                                    <Link href={`/rules/${rule.id}`} onClick={(e) => e.stopPropagation()} className="text-[13px] font-semibold hover:underline">
                                      {rule.name}
                                    </Link>
                                    <p className="truncate text-xs text-muted-foreground" title={sentenceText(ruleSentence(rule.type, rule.params, rule.target, refs))}>
                                      <span className="text-foreground/70">{meta.label}</span> · {sentenceText(ruleSentence(rule.type, rule.params, rule.target, refs))}
                                    </p>
                                  </div>
                                </div>
                              </TableCell>
                              <TableCell>
                                {rule.dryRun ? (
                                  <StatusBadge variant="dry_run">
                                    <FlaskConical className="mr-1 size-3" />
                                    Dry run
                                  </StatusBadge>
                                ) : (
                                  <StatusBadge variant="success">
                                    <Radio className="mr-1 size-3" />
                                    Live
                                  </StatusBadge>
                                )}
                              </TableCell>
                              <TableCell>
                                {rule.lastRun ? (
                                  <div className="space-y-0.5">
                                    <OutcomeBadge outcome={rule.lastRun.outcome} />
                                    <RelativeTime value={rule.lastRun.evaluatedAt} className="block text-[11px] text-muted-foreground" />
                                  </div>
                                ) : (
                                  <span className="text-xs text-muted-foreground">Not evaluated yet</span>
                                )}
                              </TableCell>
                              <TableCell className="text-xs">{rule.lastFiredAt ? <RelativeTime value={rule.lastFiredAt} /> : <span className="text-muted-foreground">never</span>}</TableCell>
                              <TableCell className="text-xs tabular-nums">
                                {rule.enabled ? (
                                  <span className="inline-flex items-center gap-1.5">
                                    <span className="size-1.5 rounded-full bg-success" />
                                    <Countdown at={next} />
                                  </span>
                                ) : (
                                  <span className="text-muted-foreground">paused</span>
                                )}
                              </TableCell>
                              <TableCell className="text-right text-xs tabular-nums">{rule.fireCount.toLocaleString()}</TableCell>
                              <TableCell onClick={(e) => e.stopPropagation()}>
                                <DropdownMenu>
                                  <DropdownMenuTrigger render={<Button variant="ghost" size="icon-sm" aria-label={`Actions for ${rule.name}`} />}>
                                    <MoreVertical className="size-4" />
                                  </DropdownMenuTrigger>
                                  <DropdownMenuContent align="end" className="w-48">
                                    <DropdownMenuItem onClick={() => setDryRunRule(rule)}>
                                      <FlaskConical className="size-4" /> Dry run now
                                    </DropdownMenuItem>
                                    <DropdownMenuItem disabled={rule.dryRun} onClick={() => setRunRule(rule)}>
                                      <Play className="size-4" /> Run now{rule.dryRun ? ' (live only)' : ''}
                                    </DropdownMenuItem>
                                    <DropdownMenuSeparator />
                                    <DropdownMenuItem onClick={() => router.push(`/rules/${rule.id}/edit`)}>
                                      <Edit className="size-4" /> Edit
                                    </DropdownMenuItem>
                                    <DropdownMenuItem onClick={() => router.push(`/rules/new?from=${rule.id}`)}>
                                      <Copy className="size-4" /> Duplicate
                                    </DropdownMenuItem>
                                    <DropdownMenuSeparator />
                                    <DropdownMenuItem className="text-destructive" onClick={() => setDeleting(rule)}>
                                      <Trash2 className="size-4" /> Delete
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
                  <TablePagination page={params.page} pageSize={params.pageSize} total={data?.total ?? 0} onPageChange={(p) => setParams({ page: p })} onPageSizeChange={(s) => setParams({ pageSize: s, page: 1 })} noun="rules" />
                </>
              )}
            </CardContent>
          </Card>

          <Card className="h-fit gap-0 rounded-xl py-0">
            <CardHeader className="border-b border-border px-5 py-4">
              <CardTitle className="text-sm font-semibold">Recent decisions</CardTitle>
              <CardDescription>Last 24 h, excluding “nothing to do”</CardDescription>
            </CardHeader>
            <CardContent className="px-2 py-2">
              {recent.isLoading ? (
                <div className="space-y-2 p-3">
                  {Array.from({ length: 5 }).map((_, i) => (
                    <Skeleton key={i} className="h-10" />
                  ))}
                </div>
              ) : notable.length === 0 ? (
                <p className="px-3 py-6 text-center text-xs text-muted-foreground">No rule has acted or held back in the last day.</p>
              ) : (
                <ul>
                  {notable.map((run) => {
                    const meta = OUTCOME_META[run.outcome];
                    return (
                      <li key={run.id}>
                        <Link href={`/rules/${run.ruleId}?run=${run.id}`} className="flex gap-3 rounded-lg px-3 py-2.5 outline-none transition-colors hover:bg-muted/50 focus-visible:ring-2 focus-visible:ring-ring/30">
                          <span className={cn('mt-1.5 size-2 shrink-0 rounded-full', meta.dot)} />
                          <span className="min-w-0 flex-1">
                            <span className="flex items-baseline justify-between gap-2">
                              <span className="truncate text-xs font-semibold">{run.ruleName ?? 'Rule'}</span>
                              <RelativeTime value={run.evaluatedAt} className="shrink-0 text-[11px] text-muted-foreground" />
                            </span>
                            <span className="line-clamp-2 text-[11px] leading-relaxed text-muted-foreground">
                              <span className={cn('font-medium', meta.text)}>{meta.short}</span> · {summarizeRun(run)}
                            </span>
                          </span>
                        </Link>
                      </li>
                    );
                  })}
                </ul>
              )}
            </CardContent>
          </Card>
        </div>
      )}

      <DryRunDialog rule={dryRunRule} open={!!dryRunRule} onOpenChange={(o) => !o && setDryRunRule(null)} />
      <ConfirmationDialog
        open={!!runRule}
        onOpenChange={(o) => !o && setRunRule(null)}
        title={`Run “${runRule?.name ?? ''}” now?`}
        description="Evaluates immediately, ignoring the cooldown, and acts if the condition holds — swaps start right away and can be followed on the Swaps page."
        confirmText="Run now"
        onConfirm={() => runRule && runNow.mutate(runRule.id)}
        isLoading={runNow.isPending}
      />
      <DeleteConfirmationDialog
        open={!!deleting}
        onOpenChange={(o) => !o && setDeleting(null)}
        itemName={deleting?.name ?? ''}
        resourceType="rule"
        warningMessage="Its run history is kept in the audit log; the rule stops being evaluated immediately."
        isDeleting={del.isPending}
        onConfirm={() => {
          if (deleting) del.mutate(deleting.id, { onSuccess: () => setDeleting(null) });
        }}
      />
    </PageContainer>
  );
}
