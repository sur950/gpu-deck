'use client';

import { useState } from 'react';
import Link from 'next/link';
import { ArrowLeft, ScrollText, Waypoints } from 'lucide-react';
import { cn } from '@/lib/utils';
import { buttonVariants } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { PageContainer, PAGE_CLASS } from '@/components/shared/PageContainer';
import { PageHeader } from '@/components/shared/PageHeader';
import { EmptyState } from '@/components/shared/EmptyState';
import { ErrorState } from '@/components/shared/ErrorState';
import { DetailList } from '@/components/shared/detail-list';
import { JsonView } from '@/components/shared/json-view';
import { CopyButton } from '@/components/shared/copy-button';
import { TraceWaterfall, SERVICE_LABELS, serviceColor } from '@/components/observability/trace-waterfall';
import { FORMAT_LABELS } from '@/components/observability/labels';
import { statusTone } from '@/app/(dashboard)/observability/traces/_components/traces-view';
import { useTrace } from '@/hooks/use-traces';
import { useDeployments } from '@/hooks/use-metrics';
import { useNodes } from '@/hooks/use-topology';
import { ApiError } from '@/lib/api/client';
import { formatDateTime } from '@/utils/format';
import { formatMs, formatTokens } from '@/utils/metrics-format';

function TraceSkeleton() {
  return (
    <div className={PAGE_CLASS}>
      <Skeleton className="h-4 w-24" />
      <Skeleton className="h-12 w-96" />
      <div className="grid gap-4 xl:grid-cols-3">
        <Skeleton className="h-[420px] rounded-xl xl:col-span-2" />
        <Skeleton className="h-[420px] rounded-xl" />
      </div>
    </div>
  );
}

/** One trace: summary, span waterfall and the selected span's attributes. */
export function TraceDetail({ traceId }: { traceId: string }) {
  const trace = useTrace(traceId);
  const deployments = useDeployments();
  const nodes = useNodes();
  const [selected, setSelected] = useState<string | null>(null);

  const back = (
    <Link href="/observability/traces" className="inline-flex items-center gap-1.5 text-xs font-medium text-muted-foreground transition-colors hover:text-foreground">
      <ArrowLeft className="size-3.5" />
      Traces
    </Link>
  );

  if (trace.isPending) return <TraceSkeleton />;
  if (trace.isError || !trace.data) {
    const missing = trace.error instanceof ApiError && trace.error.status === 404;
    return (
      <PageContainer>
        {back}
        {missing ? (
          <Card className="rounded-xl">
            <CardContent>
              <EmptyState icon={Waypoints} title="Trace not found" description="It may be older than the trace retention window, or it was not sampled." />
            </CardContent>
          </Card>
        ) : (
          <ErrorState title="Couldn't load this trace" onRetry={() => void trace.refetch()} />
        )}
      </PageContainer>
    );
  }

  const t = trace.data;
  const root = t.root ?? t.spans[0];
  const span = t.spans.find((s) => s.spanId === selected) ?? root;
  const attrs = (root?.attributes ?? {}) as Record<string, unknown>;
  const num = (k: string) => (typeof attrs[k] === 'number' ? (attrs[k] as number) : null);
  const deployment = root?.deploymentId ? deployments.data?.find((d) => d.id === root.deploymentId) : undefined;
  const node = root?.nodeId ? nodes.data?.find((n) => n.id === root.nodeId) : undefined;
  const engineSpan = t.spans.find((s) => s.name === 'prefill') ?? null;

  return (
    <PageContainer>
      {back}
      <PageHeader
        title={root?.name ?? 'Trace'}
        description={`${root ? formatDateTime(root.ts) : ''} · ${formatMs(t.durationMs)} · ${t.spans.length} spans`}
        actions={
          <>
            {root?.statusCode !== undefined && (
              <span className={cn('inline-flex rounded-md border px-2 py-1 text-xs font-semibold tabular-nums', statusTone(root?.statusCode ?? null))}>HTTP {root?.statusCode ?? '—'}</span>
            )}
            {root?.requestId && (
              <Link
                href={`/observability/logs?mode=history&range=24h&request=${encodeURIComponent(root.requestId)}`}
                className={cn(buttonVariants({ variant: 'outline', size: 'sm' }), 'gap-1.5 rounded-sm')}
              >
                <ScrollText className="size-3.5" />
                Logs for this request
              </Link>
            )}
          </>
        }
      />

      <div className="grid gap-4 xl:grid-cols-3">
        <Card className="gap-0 self-start rounded-xl py-0 xl:col-span-2">
          <CardHeader className="border-b border-border px-5 py-4">
            <CardTitle className="text-sm font-semibold">Waterfall</CardTitle>
            <CardDescription>
              {engineSpan ? `Time to first token ${formatMs(engineSpan.durationMs)} · ` : ''}select a span for its attributes
            </CardDescription>
          </CardHeader>
          <CardContent className="px-0 pb-0 pt-3">
            <TraceWaterfall spans={t.spans} selectedId={span?.spanId ?? null} onSelect={setSelected} />
          </CardContent>
        </Card>

        <div className="space-y-4">
          <Card className="gap-0 rounded-xl py-0">
            <CardHeader className="border-b border-border px-5 py-4">
              <CardTitle className="text-sm font-semibold">Request</CardTitle>
            </CardHeader>
            <CardContent className="px-5 py-4">
              <DetailList
                items={[
                  {
                    label: 'Trace ID',
                    value: (
                      <span className="inline-flex items-center gap-1">
                        <span className="text-[11px] font-mono">{t.traceId.slice(0, 16)}…</span>
                        <CopyButton value={t.traceId} label={undefined} successText="Trace ID copied" />
                      </span>
                    ),
                  },
                  { label: 'Request ID', value: root?.requestId ? <span className="text-[11px] font-mono">{root.requestId}</span> : null },
                  { label: 'Format', value: root?.format ? FORMAT_LABELS[root.format] : null },
                  { label: 'Deployment', value: deployment ? <Link href={`/deployments/${deployment.id}`} className="hover:underline">{deployment.name}</Link> : null },
                  { label: 'Node', value: node ? <span className="text-[11px] font-mono">{node.name}</span> : null },
                  { label: 'Instance', value: root?.instanceId ? <Link href={`/observability/inference?instance=${root.instanceId}`} className="hover:underline">open in inference</Link> : null },
                  { label: 'Tokens', value: num('llm.usage.prompt_tokens') !== null ? `${formatTokens(num('llm.usage.prompt_tokens'))} in · ${formatTokens(num('llm.usage.cached_tokens'))} cached · ${formatTokens(num('llm.usage.completion_tokens'))} out` : null },
                  { label: 'Duration', value: formatMs(t.durationMs) },
                ]}
              />
              {t.otlpUrl && (
                <a href={t.otlpUrl} target="_blank" rel="noreferrer" className="mt-3 inline-block text-xs font-medium text-muted-foreground underline-offset-4 hover:text-foreground hover:underline">
                  Open in the tracing backend
                </a>
              )}
            </CardContent>
          </Card>

          {span && (
            <Card className="gap-0 rounded-xl py-0">
              <CardHeader className="border-b border-border px-5 py-4">
                <CardTitle className="flex items-center gap-2 text-sm font-semibold font-mono">
                  <span aria-hidden className="size-2 rounded-[2px]" style={{ backgroundColor: serviceColor(span.service) }} />
                  {span.name}
                </CardTitle>
                <CardDescription>
                  {SERVICE_LABELS[span.service] ?? span.service} · {formatMs(span.durationMs)}
                  {span.error ? ' · failed' : ''}
                </CardDescription>
              </CardHeader>
              <CardContent className="px-5 py-4">
                <JsonView value={span.attributes ?? {}} maxHeight={280} className="font-mono" />
              </CardContent>
            </Card>
          )}
        </div>
      </div>
    </PageContainer>
  );
}
