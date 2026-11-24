'use client';

import Link from 'next/link';
import { Bell, Bot, Boxes } from 'lucide-react';
import { cn } from '@/lib/utils';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { StatusBadge } from '@/components/shared/StatusBadge';
import { EmptyState } from '@/components/shared/EmptyState';
import { RelativeTime } from '@/components/shared/relative-time';
import { BarList } from '@/components/charts/bar-list';
import type { BarListItem } from '@/components/charts/bar-list';
import {
  ALERT_STATUS_BADGE,
  OUTCOME_BADGE,
  OutcomeMark,
  SeverityMark,
  SWAP_BADGE,
  SwapMark,
  SwapStepTrack,
  isSwapActive,
} from '@/components/observability/activity-items';
import { OUTCOME_LABELS, SWAP_KIND_LABELS, SWAP_STATE_LABELS, SWAP_TRIGGER_LABELS, engineLabel } from '@/components/observability/labels';
import { filterSeries } from '@/components/charts/series-utils';
import type { MetricsResult, DeploymentSummary } from '@/types/metrics';
import type { AlertSummary, RuleRunSummary, SwapSummary } from '@/types/overview';
import { formatCompact, formatMs, formatUsd } from '@/utils/metrics-format';

function CardShell({
  title,
  description,
  href,
  hrefLabel = 'View all',
  extra,
  children,
  className,
}: {
  title: string;
  description: string;
  href: string;
  hrefLabel?: string;
  extra?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <Card className={cn('h-full gap-0 rounded-xl py-0', className)}>
      <CardHeader className="flex flex-row items-start justify-between gap-3 border-b border-border px-5 py-4">
        <div className="min-w-0 space-y-0.5">
          <CardTitle className="text-sm font-semibold">{title}</CardTitle>
          <CardDescription className="truncate">{description}</CardDescription>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {extra}
          <Link href={href} className="text-xs font-medium text-muted-foreground underline-offset-4 transition-colors hover:text-foreground hover:underline">
            {hrefLabel}
          </Link>
        </div>
      </CardHeader>
      <CardContent className="flex-1 px-0 py-0">{children}</CardContent>
    </Card>
  );
}

// ─── Top models ───────────────────────────────────────────────────────────────

export function TopModelsCard({ usage, deployments, rangeLabel }: { usage: MetricsResult | undefined; deployments: DeploymentSummary[] | undefined; rangeLabel: string }) {
  const sum = (metric: string, id: string) =>
    filterSeries(usage, metric, (l) => l.deployment_id === id).reduce((s, x) => s + x.points.reduce((a, p) => a + (p.v ?? 0), 0), 0);
  const ids = [...new Set(filterSeries(usage, 'requests').map((s) => s.labels.deployment_id).filter(Boolean))];
  const items: BarListItem[] = ids
    .map((id) => {
      const dep = deployments?.find((d) => d.id === id);
      const requests = sum('requests', id);
      const tokens = sum('input_tokens', id) + sum('output_tokens', id);
      const cost = sum('cost_usd', id);
      // Requests that never reached a deployment (bad key, unknown model) are metered as "unknown".
      const unrouted = id === 'unknown';
      const label = unrouted ? 'Unrouted requests' : (dep?.name ?? filterSeries(usage, 'requests', (l) => l.deployment_id === id)[0]?.labels.deployment ?? id);
      return {
        id,
        label,
        sublabel: dep ? `${engineLabel(dep.engine)} · ${dep.readyReplicas}/${dep.replicas} ready` : unrouted ? 'no deployment matched' : undefined,
        value: requests,
        valueLabel: `${formatCompact(requests, 1)} req`,
        extraLabel: `${formatCompact(tokens, 1)} tok · ${formatUsd(cost)}`,
        href: unrouted ? undefined : `/deployments/${id}`,
      };
    })
    .filter((item) => item.id !== 'unknown' || item.value > 0)
    .sort((a, b) => b.value - a.value)
    .slice(0, 6);

  return (
    <CardShell title="Top models" description={`By requests · ${rangeLabel}`} href="/deployments" hrefLabel="Deployments">
      {items.length === 0 ? (
        <EmptyState icon={Boxes} title="No traffic yet" description="Deployed models appear here once they serve requests." />
      ) : (
        <div className="px-5 py-4">
          <BarList items={items} color="rgb(var(--chart-2))" />
          <LiveModelFacts deployments={deployments} />
        </div>
      )}
    </CardShell>
  );
}

/** Live per-deployment figures under the ranking (sessions, throughput, p95). */
function LiveModelFacts({ deployments }: { deployments: DeploymentSummary[] | undefined }) {
  const running = (deployments ?? []).filter((d) => d.readyReplicas > 0);
  if (running.length === 0) return null;
  return (
    <table className="mt-5 w-full text-[11px] tabular-nums">
      <caption className="sr-only">Live figures per deployment</caption>
      <thead>
        <tr className="text-muted-foreground">
          <th scope="col" className="pb-1.5 text-left font-medium">Now</th>
          <th scope="col" className="pb-1.5 text-right font-medium">Sessions</th>
          <th scope="col" className="pb-1.5 text-right font-medium">tok/s</th>
          <th scope="col" className="pb-1.5 text-right font-medium">TTFT p95</th>
        </tr>
      </thead>
      <tbody className="divide-y divide-border/70">
        {running.map((d) => (
          <tr key={d.id}>
            <th scope="row" className="truncate py-1.5 text-left font-medium">{d.name}</th>
            <td className="py-1.5 text-right">
              {d.live.activeSessions ?? '—'}
              {d.live.waitingSessions ? <span className="text-warning-dark dark:text-warning-light"> +{d.live.waitingSessions}</span> : null}
            </td>
            <td className="py-1.5 text-right">{formatCompact(d.live.generationTps, 0)}</td>
            <td className="py-1.5 text-right">{formatMs(d.live.ttftMsP95)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// ─── Rule actions & swaps ─────────────────────────────────────────────────────

type ActivityEntry =
  | { kind: 'swap'; at: string; swap: SwapSummary }
  | { kind: 'run'; at: string; run: RuleRunSummary };

function swapTitle(swap: SwapSummary): string {
  return `${SWAP_KIND_LABELS[swap.kind]} ${swap.deploymentName ?? ''}`.trim();
}

function swapDetail(swap: SwapSummary): string {
  const reason = typeof swap.plan.reason === 'string' ? swap.plan.reason : null;
  const by = swap.ruleName ? `by ${swap.ruleName}` : SWAP_TRIGGER_LABELS[swap.trigger];
  if (swap.errorMessage) return swap.errorMessage;
  return reason ? `${by} · ${reason}` : by;
}

function swapDuration(swap: SwapSummary): string | null {
  if (!swap.startedAt) return null;
  const end = swap.finishedAt ? new Date(swap.finishedAt).getTime() : Date.now();
  return formatMs(end - new Date(swap.startedAt).getTime());
}

/** Rule decisions that did something (or would have) and the swaps they — or people — started. */
export function ActivityCard({ runs, swaps }: { runs: RuleRunSummary[]; swaps: SwapSummary[] }) {
  const swapIds = new Set(swaps.map((s) => s.id));
  const entries: ActivityEntry[] = [
    ...swaps.map((swap) => ({ kind: 'swap' as const, at: swap.startedAt ?? swap.createdAt, swap })),
    ...runs
      .filter((run) => run.outcome !== 'no_match' && run.outcome !== 'skipped')
      // A run whose action started a swap is shown through that swap.
      .filter((run) => !run.actions.some((a) => a.swapOperationId && swapIds.has(a.swapOperationId)))
      .map((run) => ({ kind: 'run' as const, at: run.evaluatedAt, run })),
  ]
    .sort((a, b) => b.at.localeCompare(a.at))
    .slice(0, 6);

  const inFlight = swaps.filter((s) => isSwapActive(s.state)).length;

  return (
    <CardShell
      title="Rule actions & swaps"
      description="What the automation did, and every load / unload"
      href="/swaps"
      hrefLabel="Swaps"
      extra={
        inFlight > 0 ? (
          <span className="inline-flex items-center gap-1 rounded-full border border-info/30 bg-info/10 px-2 py-0.5 text-[11px] font-semibold text-info-dark dark:text-info-light">
            {inFlight} in flight
          </span>
        ) : null
      }
    >
      {entries.length === 0 ? (
        <EmptyState icon={Bot} title="Nothing yet" description="Rule decisions and swaps show up here as they happen." />
      ) : (
        <ul className="divide-y divide-border">
          {entries.map((entry) =>
            entry.kind === 'swap' ? (
              <li key={`swap-${entry.swap.id}`}>
                <Link href={`/swaps/${entry.swap.id}`} className="flex gap-3 px-5 py-3 outline-none transition-colors hover:bg-accent/40 focus-visible:bg-accent/40">
                  <SwapMark state={entry.swap.state} />
                  <div className="min-w-0 flex-1 space-y-1">
                    <div className="flex items-center justify-between gap-2">
                      <p className="truncate text-[13px] font-semibold">{swapTitle(entry.swap)}</p>
                      <StatusBadge variant={SWAP_BADGE[entry.swap.state]} className="shrink-0 px-1.5 py-0 text-[10px]">
                        {SWAP_STATE_LABELS[entry.swap.state]}
                      </StatusBadge>
                    </div>
                    <p className="truncate text-xs text-muted-foreground">{swapDetail(entry.swap)}</p>
                    <div className="flex items-center gap-3">
                      <SwapStepTrack steps={entry.swap.steps} className="flex-1" />
                      <span className="shrink-0 text-[11px] text-muted-foreground tabular-nums">
                        {swapDuration(entry.swap)} · <RelativeTime value={entry.at} />
                      </span>
                    </div>
                  </div>
                </Link>
              </li>
            ) : (
              <li key={`run-${entry.run.id}`}>
                <Link href={`/rules/${entry.run.ruleId}`} className="flex gap-3 px-5 py-3 outline-none transition-colors hover:bg-accent/40 focus-visible:bg-accent/40">
                  <OutcomeMark outcome={entry.run.outcome} />
                  <div className="min-w-0 flex-1 space-y-1">
                    <div className="flex items-center justify-between gap-2">
                      <p className="truncate text-[13px] font-semibold">{entry.run.ruleName}</p>
                      <StatusBadge variant={OUTCOME_BADGE[entry.run.outcome]} className="shrink-0 px-1.5 py-0 text-[10px]">
                        {OUTCOME_LABELS[entry.run.outcome]}
                      </StatusBadge>
                    </div>
                    <p className="truncate text-xs text-muted-foreground">{runDetail(entry.run)}</p>
                    <p className="text-[11px] text-muted-foreground">
                      <RelativeTime value={entry.at} />
                    </p>
                  </div>
                </Link>
              </li>
            ),
          )}
        </ul>
      )}
    </CardShell>
  );
}

function runDetail(run: RuleRunSummary): string {
  if (run.errorMessage) return run.errorMessage;
  const action = run.actions[0];
  if (action?.message) return run.outcome === 'dry_run' ? `Dry run: ${action.message}` : action.message;
  const d = run.decision;
  if (run.outcome === 'cooldown' && typeof d.cooldown_remaining_minutes === 'number') return `Matched, cooling down for ${d.cooldown_remaining_minutes} more min`;
  return 'Evaluated';
}

// ─── Alerts ───────────────────────────────────────────────────────────────────

export function AlertsCard({ alerts, openCount }: { alerts: AlertSummary[]; openCount: number }) {
  const sorted = [...alerts].sort((a, b) => {
    const rank = (x: AlertSummary) => (x.status === 'open' ? 0 : x.status === 'acknowledged' ? 1 : 2);
    return rank(a) - rank(b) || b.lastSeenAt.localeCompare(a.lastSeenAt);
  });
  return (
    <CardShell
      title="Alerts"
      description="Latest events, open first"
      href="/alerts"
      extra={
        openCount > 0 ? (
          <span className="inline-flex items-center gap-1 rounded-full border border-warning/30 bg-warning/10 px-2 py-0.5 text-[11px] font-semibold text-warning-dark tabular-nums dark:text-warning-light">
            {openCount} open
          </span>
        ) : null
      }
    >
      {sorted.length === 0 ? (
        <EmptyState icon={Bell} title="No alerts" description="Slack, email and webhook alerts are listed here when they fire." />
      ) : (
        <ul className="divide-y divide-border">
          {sorted.slice(0, 5).map((alert) => (
            <li key={alert.id}>
              <Link href={`/alerts?event=${alert.id}`} className={cn('flex gap-3 px-5 py-3 outline-none transition-colors hover:bg-accent/40 focus-visible:bg-accent/40', alert.status === 'resolved' && 'opacity-70')}>
                <SeverityMark severity={alert.severity} />
                <div className="min-w-0 flex-1 space-y-1">
                  <div className="flex items-start justify-between gap-2">
                    <p className="line-clamp-2 text-[13px] font-semibold leading-snug">{alert.title}</p>
                    <StatusBadge variant={ALERT_STATUS_BADGE[alert.status]} className="shrink-0 px-1.5 py-0 text-[10px] capitalize">
                      {alert.status}
                    </StatusBadge>
                  </div>
                  <p className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
                    {alert.resourceName && <span className="truncate font-mono">{alert.resourceName}</span>}
                    {alert.occurrences > 1 && <span className="shrink-0 tabular-nums">· ×{alert.occurrences}</span>}
                    <span className="shrink-0">
                      · <RelativeTime value={alert.lastSeenAt} />
                    </span>
                  </p>
                </div>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </CardShell>
  );
}
