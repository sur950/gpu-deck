'use client';

import { useParams } from 'next/navigation';
import { Activity, Coins, Cpu, Download, Loader2, MessageSquare, RefreshCw } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Table, TableBody, TableCell, TableFooter, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { PageContainer } from '@/components/shared/PageContainer';
import { PageHeader } from '@/components/shared/PageHeader';
import { ErrorState } from '@/components/shared/ErrorState';
import { EmptyState } from '@/components/shared/EmptyState';
import { StatusBadge } from '@/components/shared/StatusBadge';
import { StatCard } from '@/components/shared/stat-card';
import { DashboardSkeleton } from '@/components/charts/dashboard-skeleton';
import { useBillingReport, useRegenerateBillingReport } from '@/hooks/api/usage';
import { cn } from '@/lib/utils';
import { fmtMinutes, fmtTokens, fmtUsd, UNALLOCATED } from '@/components/usage/usage-shared';
import { exportReport, REPORT_STATUS } from '@/app/(dashboard)/billing/_components/reports-tab';

const LABELS: Record<string, string> = { api_key: 'API key', model: 'Model', node: 'Node', cluster: 'Cluster', deployment: 'Deployment' };

export function ReportDetail() {
  const { id } = useParams<{ id: string }>();
  const first = useBillingReport(id);
  const busy = first.data?.status === 'pending' || first.data?.status === 'generating';
  const { data: report, isLoading, isError, refetch } = useBillingReport(id, { refetchInterval: busy ? 2_000 : false });
  const regenerate = useRegenerateBillingReport();

  if (isLoading) return <DashboardSkeleton />;
  if (isError || !report) {
    return (
      <PageContainer>
        <ErrorState title="Report not found" description="It may have been deleted." onRetry={() => void refetch()} />
      </PageContainer>
    );
  }
  const st = REPORT_STATUS[report.status];
  const t = report.totals;
  const sum = report.lineItems.reduce(
    (acc, li) => ({ gpu: acc.gpu + li.costBreakdown.gpuTimeUsd, input: acc.input + li.costBreakdown.inputTokensUsd, output: acc.output + li.costBreakdown.outputTokensUsd, cached: acc.cached + li.costBreakdown.cachedTokensUsd }),
    { gpu: 0, input: 0, output: 0, cached: 0 },
  );

  return (
    <PageContainer>
      <PageHeader
        title={report.name}
        description={`${new Date(report.periodStart).toLocaleDateString(undefined, { timeZone: 'UTC' })} – ${new Date(Date.parse(report.periodEnd) - 1).toLocaleDateString(undefined, { timeZone: 'UTC' })} (UTC) · grouped by ${report.groupBy.map((g) => LABELS[g] ?? g).join(', ').toLowerCase()} · ${report.currency}`}
        actions={
          <>
            <StatusBadge variant={st.badge}>
              {busy && <Loader2 className="mr-1 size-3 animate-spin" />}
              {st.label}
            </StatusBadge>
            <Button variant="outline" size="sm" className="rounded-sm" disabled={regenerate.isPending || busy} onClick={() => regenerate.mutate(report.id, { onSuccess: () => void first.refetch() })}>
              <RefreshCw className="size-4" /> Regenerate
            </Button>
            <Button size="sm" className="rounded-sm" disabled={report.status !== 'ready'} onClick={() => void exportReport(report)}>
              <Download className="size-4" /> Export CSV
            </Button>
          </>
        }
      />
      {report.status === 'failed' ? (
        <ErrorState title="The report failed" description={report.errorMessage ?? 'The jobs process could not build it.'} onRetry={() => regenerate.mutate(report.id, { onSuccess: () => void first.refetch() })} />
      ) : busy || !t ? (
        <Card className="rounded-xl">
          <CardContent className="flex items-center gap-3 py-10 text-sm text-muted-foreground">
            <Loader2 className="size-4 animate-spin" /> Generating — the jobs process is summing usage rollups for the period.
          </CardContent>
        </Card>
      ) : (
        <>
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <StatCard label="Total cost" value={fmtUsd(t.costUsd)} icon={Coins} footnote={`GPU time ${fmtUsd(sum.gpu)} · tokens ${fmtUsd(sum.input + sum.output + sum.cached)}`} />
            <StatCard label="Requests" value={fmtTokens(t.requestCount)} icon={Activity} footnote={`${fmtTokens(t.errorCount)} errors`} />
            <StatCard label="Tokens" value={fmtTokens(t.inputTokens + t.outputTokens)} icon={MessageSquare} footnote={`${fmtTokens(t.cachedTokens)} cached input`} />
            <StatCard label="GPU minutes" value={fmtMinutes(t.gpuMinutes)} icon={Cpu} footnote="Uptime × each instance's share of its GPUs (slices prorated)" />
          </div>
          <Card className="gap-0 rounded-xl py-0">
            <CardHeader className="border-b border-border px-5 py-4">
              <CardTitle className="text-sm font-semibold">Line items</CardTitle>
              <CardDescription>Cost split into GPU time and token charges under the plan that applied.</CardDescription>
            </CardHeader>
            <CardContent className="px-5 py-3">
              {report.lineItems.length === 0 ? (
                <EmptyState icon={Coins} title="No usage in this period" />
              ) : (
                <div className="overflow-x-auto">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        {report.groupBy.map((g) => (
                          <TableHead key={g}>{LABELS[g] ?? g}</TableHead>
                        ))}
                        <TableHead>Plan</TableHead>
                        <TableHead className="text-right">Requests</TableHead>
                        <TableHead className="text-right">GPU min</TableHead>
                        <TableHead className="text-right">GPU time</TableHead>
                        <TableHead className="text-right">Input</TableHead>
                        <TableHead className="text-right">Output</TableHead>
                        <TableHead className="text-right">Cached</TableHead>
                        <TableHead className="text-right">Total</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {report.lineItems.map((li, i) => {
                        const unallocated = li.group.api_key === UNALLOCATED;
                        return (
                          <TableRow key={i} className={cn(unallocated && 'bg-muted/30')}>
                            {report.groupBy.map((g) => (
                              <TableCell key={g} className={cn('text-xs font-medium', unallocated && g === 'api_key' && 'italic text-muted-foreground')}>
                                {g === 'api_key' && unallocated ? 'Unallocated (idle loaded)' : li.labels[g] || li.group[g] || (g === 'model' ? 'No model (e.g. SSH GPU instances)' : '—')}
                              </TableCell>
                            ))}
                            <TableCell className="text-xs text-muted-foreground">{li.pricePlanName ?? '—'}</TableCell>
                            <TableCell className="text-right text-xs tabular-nums">{unallocated ? '—' : fmtTokens(li.totals.requestCount)}</TableCell>
                            <TableCell className="text-right text-xs tabular-nums">{fmtMinutes(li.totals.gpuMinutes)}</TableCell>
                            <TableCell className="text-right text-xs tabular-nums">{fmtUsd(li.costBreakdown.gpuTimeUsd)}</TableCell>
                            <TableCell className="text-right text-xs tabular-nums">{fmtUsd(li.costBreakdown.inputTokensUsd)}</TableCell>
                            <TableCell className="text-right text-xs tabular-nums">{fmtUsd(li.costBreakdown.outputTokensUsd)}</TableCell>
                            <TableCell className="text-right text-xs tabular-nums">{fmtUsd(li.costBreakdown.cachedTokensUsd)}</TableCell>
                            <TableCell className="text-right text-xs font-semibold tabular-nums">{fmtUsd(li.totals.costUsd)}</TableCell>
                          </TableRow>
                        );
                      })}
                    </TableBody>
                    <TableFooter>
                      <TableRow>
                        <TableCell colSpan={report.groupBy.length + 1} className="text-xs font-semibold">
                          Total
                        </TableCell>
                        <TableCell className="text-right text-xs tabular-nums">{fmtTokens(t.requestCount)}</TableCell>
                        <TableCell className="text-right text-xs tabular-nums">{fmtMinutes(t.gpuMinutes)}</TableCell>
                        <TableCell className="text-right text-xs tabular-nums">{fmtUsd(sum.gpu)}</TableCell>
                        <TableCell className="text-right text-xs tabular-nums">{fmtUsd(sum.input)}</TableCell>
                        <TableCell className="text-right text-xs tabular-nums">{fmtUsd(sum.output)}</TableCell>
                        <TableCell className="text-right text-xs tabular-nums">{fmtUsd(sum.cached)}</TableCell>
                        <TableCell className="text-right text-xs font-semibold tabular-nums">{fmtUsd(t.costUsd)}</TableCell>
                      </TableRow>
                    </TableFooter>
                  </Table>
                </div>
              )}
            </CardContent>
          </Card>
        </>
      )}
    </PageContainer>
  );
}
