'use client';

import Link from 'next/link';
import { Activity, Cpu, Eye, Gauge, ScrollText, Timer } from 'lucide-react';
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { PageContainer } from '@/components/shared/PageContainer';
import { StatusBadge } from '@/components/shared/StatusBadge';
import { StatCard } from '@/components/shared/stat-card';
import { DetailList } from '@/components/shared/detail-list';
import { DEPLOYMENT_BADGE } from '@/components/fleet/status';
import { ExternalBadge } from '@/components/models/external-badge';
import { InstancesTable } from '@/components/models/instances-table';
import { formatBytes, formatRelativeTime } from '@/utils/format';
import {
  DEPLOYMENT_STATUS_LABELS,
  EXTERNAL_ENGINE_LABELS,
  type Deployment,
} from '@/types/models';

const SOURCE_LABELS: Record<string, string> = {
  metrics: '/metrics',
  health: '/health',
  models: '/v1/models',
  log: 'log file',
};

function when(ts: string | null | undefined): string {
  return ts ? formatRelativeTime(ts) : '—';
}

/**
 * Read-only view of an external (observed) deployment: a model server GPU Deck did not start.
 * No action buttons: GPU Deck never starts, stops, scales, swaps, routes or benchmarks it.
 */
export function ExternalDeploymentDetail({
  deployment: d,
  deviceLabels,
}: {
  deployment: Deployment;
  deviceLabels: Map<string, string>;
}) {
  const ext = d.external;
  const live = d.live ?? {};
  const instances = d.instances ?? [];
  const statusCounts = Object.entries(ext?.status_counts ?? {}).sort(
    ([a], [b]) => a.localeCompare(b),
  );
  const totalRequests = statusCounts.reduce((s, [, n]) => s + n, 0);
  const endpoints = ext?.top_endpoints ?? [];
  const rpm = ext?.requests_per_minute ?? live.requests_per_minute ?? null;

  return (
    <PageContainer>
      <div className="space-y-2">
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="font-mono text-2xl font-semibold tracking-tight sm:text-[26px]">
            {d.name}
          </h1>
          <ExternalBadge />
          <StatusBadge variant={DEPLOYMENT_BADGE[d.status]}>
            {DEPLOYMENT_STATUS_LABELS[d.status]}
          </StatusBadge>
        </div>
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-muted-foreground">
          <span>{d.model_name ?? 'model unknown'}</span>
          {ext && (
            <span className="inline-flex items-center gap-1.5 text-xs">
              <span className="font-medium text-foreground">
                {EXTERNAL_ENGINE_LABELS[ext.engine]}
              </span>
              {ext.version && <span className="font-mono">{ext.version}</span>}
            </span>
          )}
          {ext?.endpoint && (
            <span className="font-mono text-xs">{ext.endpoint}</span>
          )}
          {ext?.node_id && (
            <Link
              href={`/nodes/${ext.node_id}`}
              className="text-xs hover:text-foreground hover:underline"
            >
              on {ext.node_name ?? ext.node_id.slice(0, 8)}
            </Link>
          )}
        </div>
      </div>

      <div
        className="flex items-start gap-3 rounded-lg border border-border bg-muted/40 px-4 py-3 text-sm"
        role="note"
      >
        <Eye
          className="mt-0.5 size-4 shrink-0 text-muted-foreground"
          aria-hidden
        />
        <p>
          GPU Deck did not start this server and only observes it: the node
          agent reads its metrics, health, model list and log, read-only. It is
          never started, stopped, scaled, swapped, routed to or benchmarked from
          here, and it is not billed.
          {ext?.stale_since && (
            <span className="text-warning-dark dark:text-warning-light">
              {' '}
              The agent stopped reporting it {when(ext.stale_since)}; it is
              removed after a day.
            </span>
          )}
        </p>
      </div>
      {(ext?.message || d.status_message) && (
        <div className="rounded-lg border border-warning/40 bg-warning/10 px-4 py-3 text-sm">
          {ext?.message ?? d.status_message}
        </div>
      )}

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-5">
        <StatCard
          label="Sessions"
          value={`${live.active_sessions ?? '—'}`}
          icon={Activity}
          footnote={
            (live.waiting_sessions ?? 0) > 0
              ? `${live.waiting_sessions} waiting`
              : 'No queue'
          }
        />
        <StatCard
          label="Generation"
          value={
            live.generation_tps
              ? `${Math.round(live.generation_tps)} tok/s`
              : '—'
          }
          icon={Gauge}
          footnote={
            live.kv_cache_usage_pct !== undefined &&
            live.kv_cache_usage_pct !== null
              ? `KV cache ${Math.round(live.kv_cache_usage_pct)}% used`
              : 'KV cache —'
          }
        />
        <StatCard
          label="TTFT p95"
          value={live.ttft_ms_p95 ? `${Math.round(live.ttft_ms_p95)} ms` : '—'}
          icon={Timer}
          footnote={
            ext?.sources?.includes('metrics')
              ? 'from /metrics'
              : 'not in the log (needs /metrics)'
          }
        />
        <StatCard
          label="Requests / min"
          value={rpm !== null ? rpm.toFixed(1) : '—'}
          icon={ScrollText}
          footnote={
            totalRequests
              ? `${totalRequests.toLocaleString()} seen in the log`
              : 'last minute'
          }
        />
        <StatCard
          label="GPU memory"
          value={
            ext?.process_vram_bytes ? formatBytes(ext.process_vram_bytes) : '—'
          }
          icon={Cpu}
          footnote={
            ext?.pid ? `process tree of pid ${ext.pid}` : 'process not matched'
          }
        />
      </div>

      <Card className="rounded-md shadow-premium">
        <CardHeader>
          <CardTitle>Instance</CardTitle>
          <CardDescription>
            The observed server with its node, GPUs (where its processes hold
            memory) and live engine metrics.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <InstancesTable
            instances={instances}
            columns={['node']}
            deviceLabels={deviceLabels}
          />
        </CardContent>
      </Card>

      <div className="grid items-start gap-6 xl:grid-cols-2">
        <Card className="rounded-md shadow-premium">
          <CardHeader>
            <CardTitle>Observed server</CardTitle>
            <CardDescription>
              What the agent reports (agent.toml{' '}
              <code className="font-mono text-xs">[[observe]]</code>).
            </CardDescription>
          </CardHeader>
          <CardContent>
            <DetailList
              columns={2}
              items={[
                { label: 'Name', value: ext?.name ?? '—', mono: true },
                {
                  label: 'Engine',
                  value: ext
                    ? `${EXTERNAL_ENGINE_LABELS[ext.engine]}${ext.version ? ` ${ext.version}` : ''}`
                    : '—',
                },
                {
                  label: 'Endpoint',
                  value: ext?.endpoint ?? 'log only',
                  mono: true,
                },
                {
                  label: 'Reads',
                  value:
                    (ext?.sources ?? [])
                      .map((s) => SOURCE_LABELS[s] ?? s)
                      .join(', ') || '—',
                },
                {
                  label: 'Models',
                  value: (ext?.models ?? []).join(', ') || '—',
                  wide: true,
                  mono: true,
                },
                {
                  label: 'Process',
                  value: ext?.pid ? `pid ${ext.pid}` : 'not matched',
                },
                { label: 'Health', value: ext?.health ?? 'unknown' },
                { label: 'First seen', value: when(ext?.first_seen_at) },
                { label: 'Last report', value: when(ext?.last_seen_at) },
                { label: 'Last answered', value: when(ext?.last_ok_at) },
                {
                  label: 'Figures from the log',
                  value: ext?.log_derived
                    ? 'yes (token totals estimated from logged throughput)'
                    : 'no',
                },
              ]}
            />
            <div className="mt-4 flex flex-wrap gap-4 text-xs">
              <Link
                href={`/observability/inference?deployment_id=${d.id}`}
                className="text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
              >
                Inference metrics →
              </Link>
              <Link
                href={`/observability/logs?deployment=${d.id}`}
                className="text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
              >
                Logs →
              </Link>
            </div>
          </CardContent>
        </Card>

        <Card className="rounded-md shadow-premium">
          <CardHeader>
            <CardTitle>Requests</CardTitle>
            <CardDescription>
              From the server&apos;s access log since the agent started
              following it (the agent&apos;s own health and metrics probes are
              not counted).
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {totalRequests === 0 ? (
              <p className="py-6 text-center text-sm text-muted-foreground">
                {ext?.sources?.includes('log')
                  ? 'No requests in the log yet.'
                  : 'Set log_file on the [[observe]] entry to see requests, status codes and endpoints.'}
              </p>
            ) : (
              <>
                <div className="flex flex-wrap gap-2" aria-label="Status codes">
                  {statusCounts.map(([cls, n]) => (
                    <StatusBadge
                      key={cls}
                      variant={
                        cls.startsWith('5')
                          ? 'error'
                          : cls.startsWith('4')
                            ? 'warning'
                            : 'success'
                      }
                    >
                      {cls} · {n.toLocaleString()}
                    </StatusBadge>
                  ))}
                </div>
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Endpoint</TableHead>
                      <TableHead className="text-right">Requests</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {endpoints.map((e) => (
                      <TableRow key={`${e.method} ${e.path}`}>
                        <TableCell className="font-mono text-xs">
                          <span className="text-muted-foreground">
                            {e.method}
                          </span>{' '}
                          {e.path}
                        </TableCell>
                        <TableCell className="text-right font-mono text-xs tabular-nums">
                          {e.count.toLocaleString()}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </>
            )}
          </CardContent>
        </Card>
      </div>
    </PageContainer>
  );
}
