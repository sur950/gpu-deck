'use client';

import { useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { ChevronLeft, ChevronRight, RefreshCw, Waypoints } from 'lucide-react';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Skeleton } from '@/components/ui/skeleton';
import { PageContainer } from '@/components/shared/PageContainer';
import { PageHeader } from '@/components/shared/PageHeader';
import { EmptyState } from '@/components/shared/EmptyState';
import { ErrorState } from '@/components/shared/ErrorState';
import { SearchInput } from '@/components/shared/search-input';
import { RelativeTime } from '@/components/shared/relative-time';
import { RangePills, ScopeSelect } from '@/components/charts/range-bar';
import { findPreset } from '@/components/charts/series-utils';
import { FORMAT_LABELS } from '@/components/observability/labels';
import { useTraces } from '@/hooks/use-traces';
import { useDeployments } from '@/hooks/use-metrics';
import { useNodes } from '@/hooks/use-topology';
import { useDebounce } from '@/hooks/use-debounce';
import { useUrlState } from '@/hooks/use-url-state';
import type { TraceSpan, UsageFormat } from '@/types/traces';
import { formatMs, formatTokens } from '@/utils/metrics-format';

const DEFAULTS = { range: '1h', format: '', deployment: '', status: '', min: '', request: '', page: 1, size: 25 };
const RANGES = ['15m', '1h', '6h', '24h'];
const MIN_OPTIONS = [
  { value: '100', label: 'Slower than 100 ms' },
  { value: '1000', label: 'Slower than 1 s' },
  { value: '5000', label: 'Slower than 5 s' },
];
const STATUS_OPTIONS = [{ value: 'errors', label: 'Errors only' }];
const nowMs = (): number => Date.now();

export function statusTone(code: number | null): string {
  if (code === null) return 'border-border bg-muted text-muted-foreground';
  if (code >= 500) return 'border-error/30 bg-error/10 text-error-dark dark:text-error-light';
  if (code >= 400) return 'border-warning/30 bg-warning/10 text-warning-dark dark:text-warning-light';
  return 'border-success/30 bg-success/10 text-success-dark dark:text-success-light';
}

function tokens(span: TraceSpan): { prompt: number | null; output: number | null } {
  const a = span.attributes ?? {};
  const n = (k: string) => (typeof a[k] === 'number' ? (a[k] as number) : null);
  return { prompt: n('llm.usage.prompt_tokens'), output: n('llm.usage.completion_tokens') };
}

/** Observability › Traces: root spans through the API layer, filtered in the URL; click through to the waterfall. */
export function TracesView() {
  const router = useRouter();
  const { params, setParams } = useUrlState(DEFAULTS);
  const [anchor, setAnchor] = useState(() => Date.now());
  const preset = findPreset(params.range, '1h');

  const [request, setRequest] = useState(params.request);
  const debounced = useDebounce(request.trim(), 300);
  useEffect(() => {
    if (debounced !== params.request) setParams({ request: debounced, page: 1 });
  }, [debounced, params.request, setParams]);

  const deployments = useDeployments();
  const nodes = useNodes();
  const deploymentNames = useMemo(() => new Map((deployments.data ?? []).map((d) => [d.id, d.name])), [deployments.data]);
  const nodeNames = useMemo(() => new Map((nodes.data ?? []).map((n) => [n.id, n.name])), [nodes.data]);

  const traces = useTraces({
    page: params.page,
    pageSize: params.size,
    from: new Date(anchor - preset.ms).toISOString(),
    to: new Date(anchor).toISOString(),
    format: (params.format || undefined) as UsageFormat | undefined,
    deploymentId: params.deployment || undefined,
    error: params.status === 'errors' || undefined,
    minDurationMs: params.min ? Number(params.min) : undefined,
    requestId: params.request || undefined,
  });

  const items = traces.data?.items ?? [];
  const maxDuration = Math.max(1, ...items.map((s) => s.durationMs ?? 0));
  const errorsOnPage = items.filter((s) => s.error).length;
  const sortedDur = items.map((s) => s.durationMs ?? 0).sort((a, b) => a - b);
  const p95 = sortedDur.length ? sortedDur[Math.min(sortedDur.length - 1, Math.floor(sortedDur.length * 0.95))] : null;
  const total = traces.data?.total ?? 0;
  const pages = Math.max(1, Math.ceil(total / params.size));

  return (
    <PageContainer>
      <PageHeader
        title="Traces"
        description="Requests through the API layer, agent proxy and engine (OpenTelemetry)."
        actions={
          <Button variant="outline" size="sm" className="gap-1.5 rounded-sm" onClick={() => setAnchor(nowMs())}>
            <RefreshCw className={cn('size-3.5', traces.isFetching && 'animate-spin')} />
            Refresh
          </Button>
        }
      />

      <Card className="gap-0 overflow-hidden rounded-xl py-0">
        <div className="flex flex-wrap items-center gap-2 border-b border-border px-4 py-3">
          <SearchInput value={request} onChange={setRequest} placeholder="Request ID…" className="max-w-[210px]" />
          <RangePills
            value={preset.id}
            onChange={(id) => {
              setAnchor(nowMs());
              setParams({ range: id, page: 1 });
            }}
            presets={RANGES}
          />
          <ScopeSelect value={params.format} onValueChange={(v) => setParams({ format: v, page: 1 })} placeholder="All formats" options={Object.entries(FORMAT_LABELS).map(([value, label]) => ({ value, label }))} width="w-[128px]" ariaLabel="API format" />
          <ScopeSelect value={params.deployment} onValueChange={(v) => setParams({ deployment: v, page: 1 })} placeholder="All deployments" options={(deployments.data ?? []).map((d) => ({ value: d.id, label: d.name }))} width="w-[160px]" ariaLabel="Deployment" />
          <ScopeSelect value={params.status} onValueChange={(v) => setParams({ status: v, page: 1 })} placeholder="Any status" options={STATUS_OPTIONS} width="w-[136px]" ariaLabel="Status" />
          <ScopeSelect value={params.min} onValueChange={(v) => setParams({ min: v, page: 1 })} placeholder="Any duration" options={MIN_OPTIONS} width="w-[164px]" ariaLabel="Minimum duration" />
        </div>

        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-b border-border px-4 py-2 text-xs text-muted-foreground tabular-nums">
          <span>
            <span className="font-semibold text-foreground">{total.toLocaleString()}</span> traces · {preset.label.toLowerCase()}
          </span>
          {items.length > 0 && (
            <>
              <span>
                p95 on this page <span className="font-semibold text-foreground">{formatMs(p95)}</span>
              </span>
              <span className={cn(errorsOnPage > 0 && 'font-semibold text-error-dark dark:text-error-light')}>
                {errorsOnPage} failed on this page
              </span>
            </>
          )}
        </div>

        {traces.isPending ? (
          <div className="space-y-2 p-4">
            {Array.from({ length: 10 }).map((_, i) => (
              <Skeleton key={i} className="h-9" />
            ))}
          </div>
        ) : traces.isError ? (
          <div className="p-4">
            <ErrorState title="Couldn't load traces" description="The trace index did not answer. Try again in a moment." onRetry={() => void traces.refetch()} />
          </div>
        ) : items.length === 0 ? (
          <EmptyState icon={Waypoints} title="No traces match" description="Widen the time range or clear a filter. Traces are sampled by the API layer's OpenTelemetry exporter." />
        ) : (
          <div className={cn('overflow-x-auto transition-opacity', traces.isFetching && 'opacity-70')}>
            <Table className="text-xs tabular-nums">
              <TableHeader>
                <TableRow>
                  <TableHead className="pl-5">Time</TableHead>
                  <TableHead>Request</TableHead>
                  <TableHead>Deployment</TableHead>
                  <TableHead>Format</TableHead>
                  <TableHead className="text-right">Status</TableHead>
                  <TableHead className="text-right">Tokens in / out</TableHead>
                  <TableHead className="min-w-56 pr-5">Duration</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {items.map((span) => {
                  const t = tokens(span);
                  const href = `/observability/traces/${span.traceId}`;
                  return (
                    <TableRow key={span.traceId} className="cursor-pointer" onClick={() => router.push(href)}>
                      <TableCell className="whitespace-nowrap py-2.5 pl-5 text-muted-foreground">
                        <RelativeTime value={span.ts} tickMs={15_000} />
                      </TableCell>
                      <TableCell className="max-w-[320px]">
                        <Link href={href} onClick={(e) => e.stopPropagation()} className="block truncate font-medium hover:underline font-mono">
                          {span.name}
                        </Link>
                        <span className="block truncate text-[11px] text-muted-foreground font-mono">{span.requestId ?? span.traceId}</span>
                      </TableCell>
                      <TableCell>
                        <span className="block font-medium">{(span.deploymentId && deploymentNames.get(span.deploymentId)) ?? '—'}</span>
                        <span className="block text-[11px] text-muted-foreground font-mono">{(span.nodeId && nodeNames.get(span.nodeId)) ?? ''}</span>
                      </TableCell>
                      <TableCell>{span.format ? FORMAT_LABELS[span.format] : '—'}</TableCell>
                      <TableCell className="text-right">
                        <span className={cn('inline-flex rounded-md border px-1.5 py-0.5 text-[11px] font-semibold', statusTone(span.statusCode))}>{span.statusCode ?? '—'}</span>
                      </TableCell>
                      <TableCell className="text-right text-muted-foreground">
                        {t.prompt !== null ? (
                          <>
                            <span className="text-foreground">{formatTokens(t.prompt)}</span> / {formatTokens(t.output)}
                          </>
                        ) : (
                          '—'
                        )}
                      </TableCell>
                      <TableCell className="pr-5">
                        <div className="flex items-center gap-2">
                          <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-muted">
                            <div
                              className={cn('h-full rounded-full', span.error ? 'bg-error' : 'bg-chart-2')}
                              style={{ width: `${Math.max(1.5, ((span.durationMs ?? 0) / maxDuration) * 100)}%` }}
                            />
                          </div>
                          <span className="w-16 shrink-0 text-right font-medium">{formatMs(span.durationMs)}</span>
                        </div>
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </div>
        )}

        {items.length > 0 && (
          <div className="flex items-center justify-between border-t border-border px-4 py-3 text-xs text-muted-foreground">
            <span className="tabular-nums">
              Page {params.page} of {pages} · {params.size} per page
            </span>
            <div className="flex items-center gap-1">
              <Button variant="outline" size="icon-sm" className="size-8" aria-label="Previous page" disabled={params.page <= 1} onClick={() => setParams({ page: params.page - 1 })}>
                <ChevronLeft className="size-4" />
              </Button>
              <Button variant="outline" size="icon-sm" className="size-8" aria-label="Next page" disabled={!traces.data?.hasMore} onClick={() => setParams({ page: params.page + 1 })}>
                <ChevronRight className="size-4" />
              </Button>
            </div>
          </div>
        )}
      </Card>
    </PageContainer>
  );
}
