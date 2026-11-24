'use client';

import { useMemo, useState } from 'react';
import { Activity, ArrowDownToLine, Coins, Cpu, Download, FileDown, Gauge, MessageSquare, TriangleAlert } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu';
import { Skeleton } from '@/components/ui/skeleton';
import { PageContainer } from '@/components/shared/PageContainer';
import { PageHeader } from '@/components/shared/PageHeader';
import { ErrorState } from '@/components/shared/ErrorState';
import { EmptyState } from '@/components/shared/EmptyState';
import { StatusBadge } from '@/components/shared/StatusBadge';
import { StatCard } from '@/components/shared/stat-card';
import { TablePagination } from '@/components/shared/table-pagination';
import { DateRangeFilter, USAGE_PRESETS } from '@/components/shared/DateRangeFilter';
import { downloadFromApi } from '@/components/shared/download';
import { TimeSeriesChart, type ChartSeries } from '@/components/charts/time-series-chart';
import type { ChartRow } from '@/components/charts/series-utils';
import { useUsageEvents, useUsageSummary } from '@/hooks/api/usage';
import { usageExportPath, type UsageFilters, type UsageFormat, type UsageGroupBy, type UsageStatus } from '@/hooks/api/usage-api';
import { useApiKeys } from '@/hooks/api/security';
import { useRefDeployments, useRefNodes } from '@/hooks/api/automation';
import { useUrlState } from '@/hooks/use-url-state';
import { useNow } from '@/components/automation/use-now';
import { cn } from '@/lib/utils';
import { bucketStartMs, dimensionColors, fmtMinutes, fmtTokens, fmtUsd, ShareBar, UNALLOCATED, useRangeFromUrl } from '@/components/usage/usage-shared';

const DEFAULTS = { preset: 'last_7d', from: '', to: '', dim: 'api_key', metric: 'requests', key: '', model: '', node: '', format: '', status: '', page: 1, pageSize: 25 };

type Dim = 'api_key' | 'model' | 'node';
type Metric = 'requests' | 'tokens' | 'gpu_minutes' | 'cost';

const METRICS: { value: Metric; label: string }[] = [
  { value: 'requests', label: 'Requests' },
  { value: 'tokens', label: 'Tokens' },
  { value: 'gpu_minutes', label: 'GPU minutes' },
  { value: 'cost', label: 'Cost' },
];

const DIMS: { value: Dim; label: string }[] = [
  { value: 'api_key', label: 'API key' },
  { value: 'model', label: 'Model' },
  { value: 'node', label: 'Node' },
];

function metricValue(metric: Metric, t: { requestCount: number; inputTokens: number; outputTokens: number; gpuMinutes: number; costUsd: number }): number {
  switch (metric) {
    case 'requests':
      return t.requestCount;
    case 'tokens':
      return t.inputTokens + t.outputTokens;
    case 'gpu_minutes':
      return t.gpuMinutes;
    case 'cost':
      return t.costUsd;
  }
}

function formatMetric(metric: Metric, v: number | null, compact: boolean): string {
  if (v === null) return '—';
  if (metric === 'cost') return compact && v >= 1 ? `$${v.toFixed(v >= 100 ? 0 : 1)}` : fmtUsd(v);
  if (metric === 'gpu_minutes') return compact ? `${Math.round(v)}` : fmtMinutes(v);
  return fmtTokens(v);
}

function FilterSelect({ value, onChange, options, all, width = 'w-40', label }: { value: string; onChange: (v: string) => void; options: { value: string; label: string }[]; all: string; width?: string; label: string }) {
  return (
    <Select value={value || 'all'} onValueChange={(v) => onChange(v === 'all' ? '' : String(v))}>
      <SelectTrigger className={width} aria-label={label}>
        <SelectValue>{(v: unknown) => options.find((o) => o.value === v)?.label ?? all}</SelectValue>
      </SelectTrigger>
      <SelectContent>
        <SelectItem value="all">{all}</SelectItem>
        {options.map((o) => (
          <SelectItem key={o.value} value={o.value}>
            {o.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

export function MeteringPage() {
  const { params, setParams } = useUrlState(DEFAULTS);
  const range = useRangeFromUrl(params.preset, params.from, params.to);
  const [exporting, setExporting] = useState(false);
  const now = useNow(60_000);
  const keys = useApiKeys({ pageSize: 100 });
  const deployments = useRefDeployments();
  const nodes = useRefNodes();
  const dim = params.dim as Dim;
  const metric = params.metric as Metric;

  const filters: UsageFilters = useMemo(
    () => ({ from: range.from, to: range.to, apiKeyId: params.key || undefined, modelId: params.model || undefined, nodeId: params.node || undefined, format: params.format as UsageFormat | '', status: params.status as UsageStatus | '' }),
    [range.from, range.to, params.key, params.model, params.node, params.format, params.status],
  );
  const spanMs = Date.parse(range.to) - Date.parse(range.from);
  const timeKey: UsageGroupBy = spanMs <= 2.1 * 86_400_000 ? 'hour' : 'day';
  const breakdown = useUsageSummary(filters, [dim]);
  const series = useUsageSummary(filters, [timeKey, dim]);
  const eventParams = useMemo(() => ({ page: params.page, pageSize: params.pageSize }), [params.page, params.pageSize]);
  const events = useUsageEvents(filters, eventParams);

  const totals = breakdown.data?.totals;
  const rows = useMemo(() => [...(breakdown.data?.rows ?? [])].sort((a, b) => metricValue(metric, b.totals) - metricValue(metric, a.totals)), [breakdown.data, metric]);
  const top = rows.filter((r) => r.group[dim] !== UNALLOCATED).slice(0, 5).map((r) => r.group[dim]);
  const ids = [...top, ...(rows.some((r) => !top.includes(r.group[dim]) && r.group[dim] !== UNALLOCATED) ? ['other'] : []), ...(rows.some((r) => r.group[dim] === UNALLOCATED) && metric !== 'requests' && metric !== 'tokens' ? [UNALLOCATED] : [])];
  const colors = dimensionColors(ids);
  const labels = new Map(rows.map((r) => [r.group[dim], r.labels[dim] ?? r.group[dim]]));
  labels.set('other', 'Other');

  // Fold the (time, dimension) rows into one chart row per bucket: top 5 + other (+ unallocated).
  const chartData: ChartRow[] = (() => {
    const topList = top;
    const idList = ids;
    const byTime = new Map<number, ChartRow>();
    for (const r of series.data?.rows ?? []) {
      const t = bucketStartMs(timeKey === 'day' ? r.group.day : r.group.hour);
      if (Number.isNaN(t)) continue;
      const id = topList.includes(r.group[dim]) ? r.group[dim] : r.group[dim] === UNALLOCATED ? UNALLOCATED : 'other';
      if (!idList.includes(id)) continue;
      const row = byTime.get(t) ?? ({ t } as ChartRow);
      row[id] = ((row[id] as number | null) ?? 0) + metricValue(metric, r.totals);
      byTime.set(t, row);
    }
    return [...byTime.values()].sort((a, b) => a.t - b.t);
  })();

  const chartSeries: ChartSeries[] = ids.map((id) => ({ key: id, label: id === UNALLOCATED ? 'Unallocated (idle loaded)' : (labels.get(id) ?? id), color: colors.get(id) ?? 'rgb(var(--chart-2))', kind: 'bar', stackId: 'usage' }));
  const maxValue = Math.max(0, ...rows.map((r) => metricValue(metric, r.totals)));

  async function exportCsv(mode: 'events' | 'summary') {
    setExporting(true);
    try {
      await downloadFromApi(usageExportPath(filters, mode, [dim]), `gpu-deck-usage-${mode}.csv`);
      toast.success(mode === 'events' ? 'Request log exported' : 'Summary exported');
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Export failed');
    } finally {
      setExporting(false);
    }
  }

  const keyOptions = (keys.data?.items ?? []).map((k) => ({ value: k.id, label: k.name }));
  const modelOptions = [...new Map((deployments.data ?? []).map((d) => [d.modelId, { value: d.modelId, label: d.modelName ?? d.name }])).values()];
  const nodeOptions = (nodes.data ?? []).map((n) => ({ value: n.id, label: n.name }));
  const errorRate = totals && totals.requestCount ? (totals.errorCount / totals.requestCount) * 100 : 0;

  return (
    <PageContainer>
      <PageHeader
        title="Metering"
        description="Requests, tokens (cached and reasoning included) and GPU minutes per API key, model and node."
        actions={
          <>
            <DateRangeFilter value={range} onChange={(v) => setParams({ preset: v.preset, from: v.preset === 'custom' ? v.from : '', to: v.preset === 'custom' ? v.to : '', page: 1 })} presets={USAGE_PRESETS} />
            <DropdownMenu>
              <DropdownMenuTrigger render={<Button variant="outline" size="sm" className="rounded-sm" disabled={exporting} />}>
                <Download className="size-4" /> Export CSV
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-56">
                <DropdownMenuItem onClick={() => void exportCsv('events')}>
                  <FileDown className="size-4" /> Request log (filtered)
                </DropdownMenuItem>
                <DropdownMenuItem onClick={() => void exportCsv('summary')}>
                  <ArrowDownToLine className="size-4" /> Totals by {DIMS.find((d) => d.value === dim)?.label.toLowerCase()}
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </>
        }
      />

      <div className="flex flex-wrap items-center gap-2">
        <FilterSelect label="API key" value={params.key} onChange={(v) => setParams({ key: v, page: 1 })} options={keyOptions} all="All API keys" width="w-44" />
        <FilterSelect label="Model" value={params.model} onChange={(v) => setParams({ model: v, page: 1 })} options={modelOptions} all="All models" width="w-48" />
        <FilterSelect label="Node" value={params.node} onChange={(v) => setParams({ node: v, page: 1 })} options={nodeOptions} all="All nodes" />
        <FilterSelect label="Format" value={params.format} onChange={(v) => setParams({ format: v, page: 1 })} options={[{ value: 'openai', label: 'OpenAI' }, { value: 'anthropic', label: 'Anthropic' }, { value: 'ollama', label: 'Ollama' }]} all="All formats" width="w-36" />
        <FilterSelect label="Status" value={params.status} onChange={(v) => setParams({ status: v, page: 1 })} options={[{ value: 'success', label: 'Success' }, { value: 'error', label: 'Error' }, { value: 'timeout', label: 'Timeout' }, { value: 'cancelled', label: 'Cancelled' }]} all="Any status" width="w-36" />
      </div>

      {breakdown.isError ? (
        <ErrorState title="Could not load usage" description="The metering service did not answer. Retry in a moment." onRetry={() => void breakdown.refetch()} />
      ) : (
        <>
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-5">
            {breakdown.isLoading || !totals ? (
              Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className="h-[104px] rounded-md" />)
            ) : (
              <>
                <StatCard label="Requests" value={fmtTokens(totals.requestCount)} icon={Activity} footnote={`${errorRate.toFixed(2)}% errors`} />
                <StatCard label="Input tokens" value={fmtTokens(totals.inputTokens)} icon={MessageSquare} footnote={`${totals.inputTokens ? ((totals.cachedTokens / totals.inputTokens) * 100).toFixed(0) : 0}% served from cache`} />
                <StatCard label="Output tokens" value={fmtTokens(totals.outputTokens)} icon={Gauge} footnote={`${fmtTokens(totals.reasoningTokens)} reasoning`} />
                <StatCard label="GPU minutes" value={fmtMinutes(totals.gpuMinutes)} icon={Cpu} footnote="Uptime × share of each GPU (slices prorated)" />
                <StatCard label="Cost" value={fmtUsd(totals.costUsd)} icon={Coins} footnote="From the attached price plans" />
              </>
            )}
          </div>

          <Card className="gap-0 rounded-xl py-0">
            <CardHeader className="flex flex-col gap-3 border-b border-border px-5 py-4 lg:flex-row lg:items-center lg:justify-between">
              <div className="space-y-0.5">
                <CardTitle className="text-sm font-semibold">Usage over time</CardTitle>
                <CardDescription>
                  {timeKey === 'hour' ? 'Hourly' : 'Daily'} · stacked by {DIMS.find((d) => d.value === dim)?.label.toLowerCase()} · {range.label}
                </CardDescription>
              </div>
              <div className="flex flex-wrap gap-2">
                <Tabs value={dim} onValueChange={(v) => setParams({ dim: String(v) })}>
                  <TabsList className="bg-muted">
                    {DIMS.map((d) => (
                      <TabsTrigger key={d.value} value={d.value} className="px-3 text-xs">
                        {d.label}
                      </TabsTrigger>
                    ))}
                  </TabsList>
                </Tabs>
                <Tabs value={metric} onValueChange={(v) => setParams({ metric: String(v) })}>
                  <TabsList className="bg-muted">
                    {METRICS.map((m) => (
                      <TabsTrigger key={m.value} value={m.value} className="px-3 text-xs">
                        {m.label}
                      </TabsTrigger>
                    ))}
                  </TabsList>
                </Tabs>
              </div>
            </CardHeader>
            <CardContent className="px-2 pb-4 pt-4 sm:px-4">
              {series.isLoading ? (
                <Skeleton className="h-[280px]" />
              ) : (
                <TimeSeriesChart
                  data={chartData}
                  series={chartSeries}
                  fromMs={Date.parse(range.from)}
                  toMs={Math.min(Date.parse(range.to), now)}
                  formatValue={(v, compact) => formatMetric(metric, v, compact)}
                  height={280}
                  refreshing={series.isFetching}
                  emptyTitle="No usage in this range"
                  emptyDescription="Requests through the API layer appear here within a minute."
                />
              )}
            </CardContent>
          </Card>

          <Card className="gap-0 rounded-xl py-0">
            <CardHeader className="border-b border-border px-5 py-4">
              <CardTitle className="text-sm font-semibold">By {DIMS.find((d) => d.value === dim)?.label.toLowerCase()}</CardTitle>
              <CardDescription>{dim === 'api_key' ? '“Unallocated” is GPU time of loaded models nobody used — the cost of keeping them warm.' : 'Totals for the selected range and filters.'}</CardDescription>
            </CardHeader>
            <CardContent className="px-5 py-3">
              {rows.length === 0 ? (
                <EmptyState icon={Activity} title="No usage" description="Nothing matches these filters in this range." />
              ) : (
                <div className="overflow-x-auto">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>{DIMS.find((d) => d.value === dim)?.label}</TableHead>
                        <TableHead className="w-[22%]">Share of {METRICS.find((m) => m.value === metric)?.label.toLowerCase()}</TableHead>
                        <TableHead className="text-right">Requests</TableHead>
                        <TableHead className="text-right">Input</TableHead>
                        <TableHead className="text-right">Cached</TableHead>
                        <TableHead className="text-right">Output</TableHead>
                        <TableHead className="text-right">GPU min</TableHead>
                        <TableHead className="text-right">Cost</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {rows.map((r) => {
                        const id = r.group[dim];
                        const isUnallocated = id === UNALLOCATED;
                        const color = colors.get(top.includes(id) ? id : isUnallocated ? UNALLOCATED : 'other');
                        return (
                          <TableRow key={id} className={cn(isUnallocated && 'bg-muted/30')}>
                            <TableCell>
                              <span className="flex items-center gap-2 text-xs font-medium">
                                <span className="h-3 w-1 rounded-full" style={{ backgroundColor: color }} />
                                {isUnallocated ? <span className="italic text-muted-foreground">Unallocated (idle loaded)</span> : (r.labels[dim] ?? id)}
                              </span>
                            </TableCell>
                            <TableCell>
                              <span className="flex items-center gap-2">
                                <ShareBar value={metricValue(metric, r.totals)} max={maxValue} color={color} />
                                <span className="w-12 text-right font-mono text-[11px] tabular-nums text-muted-foreground">{totals && metricValue(metric, totals) ? `${((metricValue(metric, r.totals) / metricValue(metric, totals)) * 100).toFixed(1)}%` : '—'}</span>
                              </span>
                            </TableCell>
                            <TableCell className="text-right text-xs tabular-nums">{isUnallocated ? '—' : fmtTokens(r.totals.requestCount)}</TableCell>
                            <TableCell className="text-right text-xs tabular-nums">{isUnallocated ? '—' : fmtTokens(r.totals.inputTokens)}</TableCell>
                            <TableCell className="text-right text-xs tabular-nums text-muted-foreground">{isUnallocated ? '—' : fmtTokens(r.totals.cachedTokens)}</TableCell>
                            <TableCell className="text-right text-xs tabular-nums">{isUnallocated ? '—' : fmtTokens(r.totals.outputTokens)}</TableCell>
                            <TableCell className="text-right text-xs tabular-nums">{fmtMinutes(r.totals.gpuMinutes)}</TableCell>
                            <TableCell className="text-right text-xs font-semibold tabular-nums">{fmtUsd(r.totals.costUsd)}</TableCell>
                          </TableRow>
                        );
                      })}
                    </TableBody>
                  </Table>
                </div>
              )}
            </CardContent>
          </Card>

          <Card className="rounded-md shadow-premium">
            <CardHeader>
              <CardTitle className="text-sm font-semibold">Requests</CardTitle>
              <CardDescription>Newest first. Token counts come from the engine response; cost uses the plan in force at the time.</CardDescription>
            </CardHeader>
            <CardContent>
              {events.isLoading ? (
                <div className="space-y-2">{Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-9" />)}</div>
              ) : (events.data?.items.length ?? 0) === 0 ? (
                <EmptyState icon={MessageSquare} title="No requests" description="No request matches these filters in this range." />
              ) : (
                <>
                  <div className="overflow-x-auto">
                    <Table>
                      <TableHeader className="sticky top-0 z-10 bg-card">
                        <TableRow>
                          <TableHead>Time</TableHead>
                          <TableHead>API key</TableHead>
                          <TableHead>Model</TableHead>
                          <TableHead>Endpoint</TableHead>
                          <TableHead className="text-right">In / cached / out</TableHead>
                          <TableHead className="text-right">TTFT</TableHead>
                          <TableHead className="text-right">Latency</TableHead>
                          <TableHead className="text-right">Cost</TableHead>
                          <TableHead className="text-right">Status</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {events.data?.items.map((e) => (
                          <TableRow key={`${e.id}-${e.requestId}`}>
                            <TableCell className="whitespace-nowrap font-mono text-[11px] tabular-nums text-muted-foreground" title={e.requestId}>
                              {new Date(e.ts).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23' })}
                            </TableCell>
                            <TableCell className="text-xs">{e.apiKeyName ?? '—'}</TableCell>
                            <TableCell className="text-xs">
                              {e.modelName}
                              <span className="block text-[11px] text-muted-foreground">{e.nodeName}</span>
                            </TableCell>
                            <TableCell>
                              <span className="font-mono text-[11px]">{e.endpoint}</span>
                              <span className="ml-1.5 text-[10px] uppercase text-muted-foreground">{e.format}{e.stream ? ' · stream' : ''}</span>
                            </TableCell>
                            <TableCell className="text-right font-mono text-[11px] tabular-nums">
                              {e.inputTokens.toLocaleString()} / <span className="text-muted-foreground">{e.cachedTokens.toLocaleString()}</span> / {e.outputTokens.toLocaleString()}
                            </TableCell>
                            <TableCell className="text-right font-mono text-[11px] tabular-nums">{e.ttftMs !== null ? `${e.ttftMs} ms` : '—'}</TableCell>
                            <TableCell className="text-right font-mono text-[11px] tabular-nums">{e.latencyMs !== null ? `${(e.latencyMs / 1000).toFixed(2)} s` : '—'}</TableCell>
                            <TableCell className="text-right font-mono text-[11px] tabular-nums">{fmtUsd(e.costUsd)}</TableCell>
                            <TableCell className="text-right">
                              {e.status === 'success' ? (
                                <StatusBadge variant="success">{e.httpStatus ?? 200}</StatusBadge>
                              ) : (
                                <StatusBadge variant="error">
                                  <TriangleAlert className="mr-1 size-3" />
                                  {e.httpStatus ?? e.status}
                                </StatusBadge>
                              )}
                            </TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </div>
                  <TablePagination page={params.page} pageSize={params.pageSize} total={events.data?.total ?? 0} onPageChange={(p) => setParams({ page: p })} onPageSizeChange={(s) => setParams({ pageSize: s, page: 1 })} noun="requests" />
                </>
              )}
            </CardContent>
          </Card>
        </>
      )}
    </PageContainer>
  );
}
