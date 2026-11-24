'use client';

import { CalendarRange, Coins, Cpu, MoonStar, TrendingUp } from 'lucide-react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { ErrorState } from '@/components/shared/ErrorState';
import { StatCard } from '@/components/shared/stat-card';
import { useBillingEstimate, usePricePlans } from '@/hooks/api/usage';
import { useRefClusters, useRefNodes } from '@/hooks/api/automation';
import type { RefCluster, RefNode } from '@/hooks/api/automation-api';
import { effectivePlanId } from '@/app/(dashboard)/billing/_components/plans-tab';
import type { UsageSummaryRow, UsageTotals } from '@/hooks/api/usage-api';
import { cn } from '@/lib/utils';
import { dimensionColors, fmtMinutes, fmtUsd, ShareBar, UNALLOCATED } from '@/components/usage/usage-shared';

function Breakdown({ title, description, rows, dim, extra }: { title: string; description: string; rows: UsageSummaryRow[]; dim: string; extra?: (row: UsageSummaryRow) => string | null }) {
  // Requests that never reached a node / model cost nothing unless GPU time was allocated to them.
  const sorted = rows.filter((r) => r.group[dim] !== 'unrouted' || r.totals.costUsd > 0).sort((a, b) => b.totals.costUsd - a.totals.costUsd);
  const total = sorted.reduce((s, r) => s + r.totals.costUsd, 0);
  const max = Math.max(0, ...sorted.map((r) => r.totals.costUsd));
  const colors = dimensionColors(sorted.map((r) => r.group[dim]));
  return (
    <Card className="gap-0 rounded-xl py-0">
      <CardHeader className="border-b border-border px-5 py-4">
        <CardTitle className="text-sm font-semibold">{title}</CardTitle>
        <CardDescription>{description}</CardDescription>
      </CardHeader>
      <CardContent className="px-5 py-4">
        {sorted.length === 0 ? (
          <p className="py-6 text-center text-xs text-muted-foreground">No cost recorded yet this month.</p>
        ) : (
          <ol className="space-y-3.5">
            {sorted.map((r) => {
              const id = r.group[dim];
              const unallocated = id === UNALLOCATED;
              const sub = extra?.(r);
              return (
                <li key={id}>
                  <div className="mb-1.5 flex items-baseline justify-between gap-3 text-xs">
                    <span className={cn('min-w-0 truncate font-medium', unallocated && 'italic text-muted-foreground')}>
                      {unallocated ? 'Unallocated (idle loaded)' : (r.labels[dim] ?? id)}
                      {sub && <span className="ml-2 font-normal not-italic text-muted-foreground">{sub}</span>}
                    </span>
                    <span className="shrink-0 tabular-nums">
                      <span className="font-semibold">{fmtUsd(r.totals.costUsd)}</span>
                      <span className="ml-2 text-muted-foreground">{total ? `${((r.totals.costUsd / total) * 100).toFixed(1)}%` : ''}</span>
                    </span>
                  </div>
                  <ShareBar value={r.totals.costUsd} max={max} color={colors.get(id)} />
                  {unallocated && <p className="mt-1 text-[11px] text-muted-foreground">GPU minutes of loaded models with no traffic — what keeping them warm costs. Idle-unload rules reduce it.</p>}
                </li>
              );
            })}
          </ol>
        )}
      </CardContent>
    </Card>
  );
}

/** Cost per cluster, folded from the per-node rows (a node's cost belongs to its cluster). */
function byCluster(rows: UsageSummaryRow[], nodes: RefNode[] | undefined, clusters: RefCluster[] | undefined): UsageSummaryRow[] {
  const out = new Map<string, UsageSummaryRow>();
  for (const row of rows) {
    const node = nodes?.find((n) => n.id === row.group.node);
    const id = node?.clusterId ?? 'unrouted';
    const label = node ? (clusters?.find((c) => c.id === node.clusterId)?.name ?? node.clusterId) : 'Unrouted (no node)';
    const acc = out.get(id) ?? { group: { cluster: id }, labels: { cluster: label }, totals: { ...EMPTY_TOTALS } };
    for (const key of SUMMED) acc.totals[key] += row.totals[key];
    out.set(id, acc);
  }
  return [...out.values()];
}

const SUMMED = ['requestCount', 'successCount', 'errorCount', 'inputTokens', 'outputTokens', 'cachedTokens', 'reasoningTokens', 'mediaUnits', 'gpuSeconds', 'gpuMinutes', 'costUsd'] as const;
const EMPTY_TOTALS: UsageTotals = { requestCount: 0, successCount: 0, errorCount: 0, inputTokens: 0, outputTokens: 0, cachedTokens: 0, reasoningTokens: 0, mediaUnits: 0, gpuSeconds: 0, gpuMinutes: 0, costUsd: 0, latencyMsAvg: null, ttftMsAvg: null };

export function OverviewTab() {
  const estimate = useBillingEstimate();
  const plans = usePricePlans({ pageSize: 100 });
  const nodes = useRefNodes();
  const clusters = useRefClusters();
  if (estimate.isError) return <ErrorState title="Could not load the estimate" onRetry={() => void estimate.refetch()} />;
  if (estimate.isLoading || !estimate.data) {
    return (
      <div className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">{Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-[104px] rounded-md" />)}</div>
        <Skeleton className="h-72 rounded-xl" />
      </div>
    );
  }
  const e = estimate.data;
  const unallocated = e.byApiKey.find((r) => r.group.api_key === UNALLOCATED)?.totals.costUsd ?? 0;
  const monthLabel = new Date(e.periodStart).toLocaleString(undefined, { month: 'long', year: 'numeric', timeZone: 'UTC' });
  const planFor = (nodeId: string) => {
    const node = nodes.data?.find((n) => n.id === nodeId);
    const planList = plans.data?.items ?? [];
    return planList.find((p) => p.id === effectivePlanId(node, clusters.data ?? [], planList))?.name ?? null;
  };

  return (
    <div className="space-y-6">
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard label={`${monthLabel} so far`} value={fmtUsd(e.totals.costUsd)} icon={Coins} footnote={`as of ${new Date(e.asOf).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })}`} />
        <StatCard label="Projected month end" value={fmtUsd(e.projectedCostUsd)} icon={TrendingUp} footnote="Linear from the month so far" />
        <StatCard label="Idle loaded (unallocated)" value={fmtUsd(unallocated)} icon={MoonStar} footnote={e.totals.costUsd ? `${((unallocated / e.totals.costUsd) * 100).toFixed(1)}% of spend` : '—'} />
        <StatCard label="Billable GPU minutes" value={fmtMinutes(e.totals.gpuMinutes)} icon={Cpu} footnote="Uptime × each instance's share of its GPUs (slices prorated)" />
      </div>
      <div className="grid gap-6 xl:grid-cols-3">
        <div className="xl:col-span-2">
          <Breakdown title="By API key" description="GPU minutes are allocated to keys by tokens processed each minute; unused minutes stay unallocated." rows={e.byApiKey} dim="api_key" />
        </div>
        <Breakdown title="By cluster" description="Every node's cost, summed per cluster." rows={byCluster(e.byNode, nodes.data, clusters.data)} dim="cluster" />
      </div>
      <div className="grid gap-6 lg:grid-cols-2">
        <Breakdown title="By model" description="All keys, all nodes." rows={e.byModel} dim="model" />
        <Breakdown title="By node" description="Price plan per node (node overrides cluster)." rows={e.byNode} dim="node" extra={(r) => planFor(r.group.node)} />
      </div>
      <p className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
        <CalendarRange className="size-3.5" /> Period {new Date(e.periodStart).toLocaleDateString()} – {new Date(e.periodEnd).toLocaleDateString()} (UTC). Closed months are frozen as reports.
      </p>
    </div>
  );
}
