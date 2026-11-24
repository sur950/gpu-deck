'use client';

import { useMemo } from 'react';
import { Activity, Database, Gauge, Timer, TimerReset, Users } from 'lucide-react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { PageContainer } from '@/components/shared/PageContainer';
import { PageHeader } from '@/components/shared/PageHeader';
import { EmptyState } from '@/components/shared/EmptyState';
import { ErrorState } from '@/components/shared/ErrorState';
import { RangeBar, ScopeSelect } from '@/components/charts/range-bar';
import { LiveIndicator } from '@/components/charts/live-indicator';
import { StatTile } from '@/components/charts/stat-tile';
import { asResult, clampSeries, combineSeries, filterSeries, groupSeriesBy, latestValue, sumValues, toSpark } from '@/components/charts/series-utils';
import { EntityMetricChart, MetricsChartCard } from '@/components/observability/entity-chart';
import type { ChartEntity } from '@/components/observability/entity-chart';
import { InstanceTable } from '@/components/observability/instance-table';
import { SessionsPanel } from '@/components/observability/sessions-panel';
import { MetricsPageSkeleton } from '@/components/observability/page-skeletons';
import { useDeployments, useInferenceMetrics, useInstances, useInstanceSessions, useSessionMetrics } from '@/hooks/use-metrics';
import { useNodes } from '@/hooks/use-topology';
import { readEngineSamples, useLiveEvents } from '@/hooks/use-live-events';
import { useDeploymentColors } from '@/hooks/use-entity-colors';
import { useTimeRange } from '@/hooks/use-time-range';
import { useUrlState } from '@/hooks/use-url-state';
import { formatCompact, formatMs, formatPct } from '@/utils/metrics-format';

const RANGES = ['15m', '1h', '6h', '24h', '7d'];
const SCOPE_DEFAULTS = { deployment: '', node: '', instance: '', origin: '' };
const ORIGIN_OPTIONS = [
  { value: 'managed', label: 'Managed only' },
  { value: 'external', label: 'External only' },
];
const ENGINE_METRICS = [
  'active_sessions',
  'waiting_sessions',
  'generation_tps',
  'prompt_tps',
  'requests_delta',
  'ttft_ms_p50',
  'ttft_ms_p95',
  'e2e_ms_p50',
  'e2e_ms_p95',
  'kv_cache_usage_pct',
  'cached_tokens_delta',
  'computed_tokens',
  'errors_delta',
];
const SESSION_WINDOW_MS = 15 * 60_000;
const SERIES_2 = 'rgb(var(--chart-2))';
const SERIES_3 = 'rgb(var(--chart-3))';
const SERIES_4 = 'rgb(var(--chart-4))';

/** Observability › Inference: live tiles, per-deployment charts, instance table, per-slot sessions and KV cache. */
export function InferenceView() {
  const range = useTimeRange('1h');
  const { params, setParams } = useUrlState(SCOPE_DEFAULTS);
  const deploymentId = params.deployment || undefined;
  const nodeId = params.node || undefined;

  const origin = params.origin === 'managed' || params.origin === 'external' ? params.origin : '';
  const deployments = useDeployments();
  const nodes = useNodes();
  const instances = useInstances({ deploymentId, nodeId });
  const rawMetrics = useInferenceMetrics({ from: range.from, to: range.to, step: range.step, deploymentId, nodeId, metrics: ENGINE_METRICS, aggregate: 'none' });
  const live = useLiveEvents({ topics: ['metrics'], scope: { deploymentId, nodeId }, intervalSeconds: 2 });
  const colors = useDeploymentColors();

  // Origin filter (managed / external observed servers) is applied client-side by deployment.
  const scopedDeployments = useMemo(() => (deployments.data ?? []).filter((d) => !origin || (origin === 'external') === d.external), [deployments.data, origin]);
  const originIds = useMemo(() => (origin ? new Set(scopedDeployments.map((d) => d.id)) : null), [origin, scopedDeployments]);
  const metrics = useMemo(
    () => ({ ...rawMetrics, data: rawMetrics.data && originIds ? { ...rawMetrics.data, series: rawMetrics.data.series.filter((s) => originIds.has(String(s.labels.deployment_id ?? ''))) } : rawMetrics.data }),
    [rawMetrics, originIds],
  );
  const instanceList = useMemo(() => (instances.data ?? []).filter((i) => !origin || (origin === 'external') === i.external), [instances.data, origin]);
  const ready = instanceList.filter((i) => i.state === 'ready');
  const scopeIds = useMemo(() => new Set(instanceList.map((i) => i.id)), [instanceList]);
  const engineLive = readEngineSamples(live.latest.metrics).filter((s) => scopeIds.has(s.instanceId));

  // Selected instance for the sessions panel: URL, else the busiest generating one.
  const selectedId =
    (params.instance && instanceList.some((i) => i.id === params.instance) ? params.instance : null) ??
    [...ready].sort((a, b) => (b.live.generationTps ?? 0) - (a.live.generationTps ?? 0) || (b.live.activeSessions ?? 0) - (a.live.activeSessions ?? 0))[0]?.id ??
    null;
  const selected = instanceList.find((i) => i.id === selectedId);
  const sessions = useInstanceSessions(selectedId);
  const sessionTo = new Date(range.toMs).toISOString();
  const sessionFrom = new Date(range.toMs - SESSION_WINDOW_MS).toISOString();
  const history = useSessionMetrics({ instanceId: selectedId ?? '', from: sessionFrom, to: sessionTo, step: '15s' }, Boolean(selectedId));

  const r = metrics.data;
  const derived = useMemo(() => {
    const requests = filterSeries(r, 'requests_delta');
    const byDeployment = (metric: string, mode: 'sum' | 'avg' | 'weighted') => groupSeriesBy(r, metric, 'deployment_id', mode, mode === 'weighted' ? 'requests_delta' : undefined);
    return {
      active: combineSeries(filterSeries(r, 'active_sessions'), 'sum'),
      waiting: combineSeries(filterSeries(r, 'waiting_sessions'), 'sum'),
      tps: combineSeries(filterSeries(r, 'generation_tps'), 'sum'),
      promptTps: combineSeries(filterSeries(r, 'prompt_tps'), 'sum'),
      requests: combineSeries(requests, 'sum'),
      errors: combineSeries(filterSeries(r, 'errors_delta'), 'sum'),
      ttft50: combineSeries(filterSeries(r, 'ttft_ms_p50'), 'weighted', requests),
      ttft95: combineSeries(filterSeries(r, 'ttft_ms_p95'), 'weighted', requests),
      e2e50: combineSeries(filterSeries(r, 'e2e_ms_p50'), 'weighted', requests),
      e2e95: combineSeries(filterSeries(r, 'e2e_ms_p95'), 'weighted', requests),
      kv: combineSeries(filterSeries(r, 'kv_cache_usage_pct'), 'avg'),
      cached: combineSeries(filterSeries(r, 'cached_tokens_delta'), 'sum'),
      // computed = prompt − cached can come back negative when an engine's prompt counter already
      // excludes cache hits (llama.cpp); clamp so the share never exceeds 100 %.
      computed: clampSeries(combineSeries(filterSeries(r, 'computed_tokens'), 'sum')),
      perDeployment: asResult([
        ...byDeployment('active_sessions', 'sum'),
        ...byDeployment('waiting_sessions', 'sum'),
        ...byDeployment('generation_tps', 'sum'),
        ...byDeployment('kv_cache_usage_pct', 'avg'),
      ]),
    };
  }, [r]);

  const errorRate = useMemo(() => {
    if (!derived.requests || !derived.errors) return undefined;
    const errs = new Map(derived.errors.points.map((p) => [p.t, p.v ?? 0]));
    return asResult([
      {
        metric: 'error_rate_pct',
        unit: '%',
        labels: {},
        points: derived.requests.points.map((p) => ({ t: p.t, v: p.v ? ((errs.get(p.t) ?? 0) / p.v) * 100 : p.v === 0 ? 0 : null })),
      },
    ]);
  }, [derived.requests, derived.errors]);

  const tokensResult = useMemo(() => asResult([...(derived.cached ? [{ ...derived.cached, metric: 'cached' }] : []), ...(derived.computed ? [{ ...derived.computed, metric: 'computed' }] : [])]), [derived.cached, derived.computed]);
  const latencyResult = useMemo(
    () =>
      asResult(
        [
          derived.ttft50 && { ...derived.ttft50, metric: 'ttft50' },
          derived.ttft95 && { ...derived.ttft95, metric: 'ttft95' },
          derived.e2e50 && { ...derived.e2e50, metric: 'e2e50' },
          derived.e2e95 && { ...derived.e2e95, metric: 'e2e95' },
        ].filter((x): x is NonNullable<typeof x> => Boolean(x)),
      ),
    [derived.ttft50, derived.ttft95, derived.e2e50, derived.e2e95],
  );

  const entities: ChartEntity[] = useMemo(
    () =>
      scopedDeployments
        .filter((d) => !deploymentId || d.id === deploymentId)
        .sort((a, b) => a.name.localeCompare(b.name))
        .map((d) => ({ id: d.id, label: d.name, color: colors.get(d.id) ?? 'rgb(var(--muted-foreground))' })),
    [scopedDeployments, deploymentId, colors],
  );

  const header = (
    <PageHeader
      title="Inference"
      description="Sessions, throughput, time to first token, KV cache per session and cache hit tokens."
      actions={<LiveIndicator status={live.status} detail="2 s" />}
    />
  );
  const filters = (
    <RangeBar range={range.rangeId} onRangeChange={range.setRange} step={range.stepId} onStepChange={range.setStep} presets={RANGES}>
      <ScopeSelect
        value={params.deployment}
        onValueChange={(v) => setParams({ deployment: v, instance: '' })}
        placeholder="All deployments"
        options={scopedDeployments.map((d) => ({ value: d.id, label: d.external ? `${d.name} (external)` : d.name }))}
        ariaLabel="Deployment"
      />
      <ScopeSelect value={origin} onValueChange={(v) => setParams({ origin: v, deployment: '', instance: '' })} placeholder="Managed + external" options={ORIGIN_OPTIONS} ariaLabel="Origin" />
      <ScopeSelect value={params.node} onValueChange={(v) => setParams({ node: v, instance: '' })} placeholder="All nodes" options={(nodes.data ?? []).map((n) => ({ value: n.id, label: n.name }))} ariaLabel="Node" />
    </RangeBar>
  );

  if (instances.isPending) return <MetricsPageSkeleton charts={6} />;
  if (instances.isError) {
    return (
      <PageContainer>
        {header}
        <ErrorState title="Couldn't load instances" description="The instance list did not load. Try again in a moment." onRetry={() => void instances.refetch()} />
      </PageContainer>
    );
  }
  if (instanceList.length === 0) {
    return (
      <PageContainer>
        {header}
        {filters}
        <Card className="rounded-xl">
          <CardContent>
            <EmptyState icon={Activity} title="No model instances" description={deploymentId || nodeId ? 'Nothing runs in this scope. Clear the filters to see every instance.' : 'Deploy a model: its sessions, latency and KV cache chart here while it serves.'} />
          </CardContent>
        </Card>
      </PageContainer>
    );
  }

  // ── Live tiles ──
  const liveSum = (k: 'activeSessions' | 'waitingSessions' | 'generationTps' | 'promptTps' | 'slotsTotal') =>
    engineLive.length ? engineLive.reduce((s, e) => s + (e[k] ?? 0), 0) : null;
  const liveWeighted = (k: 'ttftMsP50' | 'ttftMsP95' | 'e2eMsP50' | 'e2eMsP95') => {
    const vals = engineLive.filter((e) => e[k] !== null);
    if (vals.length === 0) return null;
    const w = vals.reduce((s, e) => s + Math.max(1, e.activeSessions ?? 0), 0);
    return vals.reduce((s, e) => s + (e[k] ?? 0) * Math.max(1, e.activeSessions ?? 0), 0) / w;
  };
  const active = liveSum('activeSessions') ?? latestValue(derived.active);
  const waiting = liveSum('waitingSessions') ?? latestValue(derived.waiting);
  const slots = liveSum('slotsTotal');
  const tps = liveSum('generationTps') ?? latestValue(derived.tps);
  const promptTps = liveSum('promptTps') ?? latestValue(derived.promptTps);
  const rpm = ready.reduce((s, i) => s + (i.live.requestsPerMinute ?? 0), 0);
  const totalReq = sumValues(derived.requests);
  const totalErr = sumValues(derived.errors);
  const kvNow = engineLive.length ? engineLive.reduce((s, e) => s + (e.kvCacheUsagePct ?? 0), 0) / engineLive.length : latestValue(derived.kv);
  const cachedSum = sumValues(derived.cached);
  const computedSum = sumValues(derived.computed);
  const cachedShare = cachedSum + computedSum > 0 ? (cachedSum / (cachedSum + computedSum)) * 100 : null;
  const refreshing = metrics.isFetching && !metrics.isPending;
  const rangeLabel = range.preset.label.toLowerCase();

  return (
    <PageContainer>
      {header}
      {filters}

      <section aria-label="Live" className="grid gap-3 sm:grid-cols-3 xl:grid-cols-6">
        <StatTile label="Active sessions" icon={Users} value={formatCompact(active, 0)} unit={slots ? `/ ${slots} slots` : undefined} hint={`${formatCompact(waiting, 0)} waiting for a slot`} tone={(waiting ?? 0) > 0 ? 'warn' : 'default'} trend={toSpark(derived.active, 60)} />
        <StatTile label="Generation" icon={Gauge} value={formatCompact(tps, 0)} unit="tok/s" hint={`prompt ${formatCompact(promptTps, 0)} tok/s`} trend={toSpark(derived.tps, 60)} trendColor={SERIES_3} />
        <StatTile label="Requests" icon={Activity} value={formatCompact(rpm, 0)} unit="/ min" hint={`${formatCompact(totalReq, 1)} in range · ${formatPct(totalReq ? (totalErr / totalReq) * 100 : null, 2)} errors`} tone={totalReq && totalErr / totalReq >= 0.05 ? 'bad' : 'default'} trend={toSpark(derived.requests, 60)} />
        <StatTile label="TTFT p95" icon={Timer} value={formatMs(liveWeighted('ttftMsP95') ?? latestValue(derived.ttft95))} hint={`p50 ${formatMs(liveWeighted('ttftMsP50') ?? latestValue(derived.ttft50))}`} trend={toSpark(derived.ttft95, 60)} trendColor={SERIES_4} />
        <StatTile label="End-to-end p95" icon={TimerReset} value={formatMs(liveWeighted('e2eMsP95') ?? latestValue(derived.e2e95))} hint={`p50 ${formatMs(liveWeighted('e2eMsP50') ?? latestValue(derived.e2e50))}`} trend={toSpark(derived.e2e95, 60)} trendColor={SERIES_4} />
        <StatTile label="KV cache" icon={Database} value={formatPct(kvNow, 0)} unit="avg" hint={`${formatPct(cachedShare, 0)} prompt tokens cached`} tone={(kvNow ?? 0) >= 90 ? 'warn' : 'default'} trend={toSpark(derived.kv, 60)} trendDomain={[0, 100]} />
      </section>

      <section aria-label="Charts" className="grid gap-4 lg:grid-cols-2">
        <EntityMetricChart title="Active sessions" description="Requests holding an engine slot, per deployment" result={derived.perDeployment} metric="active_sessions" labelKey="deployment_id" entities={entities} fromMs={range.fromMs} toMs={range.toMs} kind="area" stacked unit="count" refreshing={refreshing} />
        <EntityMetricChart title="Waiting sessions" description="Requests queued for a free slot, per deployment" result={derived.perDeployment} metric="waiting_sessions" labelKey="deployment_id" entities={entities} fromMs={range.fromMs} toMs={range.toMs} unit="count" refreshing={refreshing} />
        <EntityMetricChart title="Generation throughput" description="Output tokens per second, per deployment" result={derived.perDeployment} metric="generation_tps" labelKey="deployment_id" entities={entities} fromMs={range.fromMs} toMs={range.toMs} kind="area" stacked unit="tok/s" refreshing={refreshing} />
        <MetricsChartCard
          title="Prompt tokens: cached vs computed"
          description={`Prefix-cache hits skip prefill · ${formatPct(cachedShare, 0)} cached over the ${rangeLabel}`}
          result={tokensResult}
          lines={[
            { metric: 'cached', label: 'Cached', color: SERIES_3, kind: (derived.cached?.points.length ?? 0) > 48 ? 'area' : 'bar' },
            { metric: 'computed', label: 'Computed', color: SERIES_2, kind: (derived.cached?.points.length ?? 0) > 48 ? 'area' : 'bar' },
          ]}
          stacked
          fromMs={range.fromMs}
          toMs={range.toMs}
          unit="tokens"
          height={220}
          refreshing={refreshing}
        />
        <MetricsChartCard
          title="Time to first token"
          description="Request-weighted across instances"
          result={latencyResult}
          lines={[
            { metric: 'ttft50', label: 'p50', color: SERIES_2 },
            { metric: 'ttft95', label: 'p95', color: SERIES_4, dashed: true },
          ]}
          fromMs={range.fromMs}
          toMs={range.toMs}
          unit="ms"
          height={220}
          refreshing={refreshing}
        />
        <MetricsChartCard
          title="End-to-end latency"
          description="Request start to last token, request-weighted"
          result={latencyResult}
          lines={[
            { metric: 'e2e50', label: 'p50', color: SERIES_2 },
            { metric: 'e2e95', label: 'p95', color: SERIES_4, dashed: true },
          ]}
          fromMs={range.fromMs}
          toMs={range.toMs}
          unit="ms"
          height={220}
          refreshing={refreshing}
        />
        <EntityMetricChart title="KV cache usage" description="Share of KV cache in use, per deployment" result={derived.perDeployment} metric="kv_cache_usage_pct" labelKey="deployment_id" entities={entities} fromMs={range.fromMs} toMs={range.toMs} unit="%" yDomain={[0, 100]} thresholds={[{ value: 90, label: '90%' }]} refreshing={refreshing} />
        <MetricsChartCard
          title="Error rate"
          description="Failed requests as a share of all requests"
          result={errorRate}
          lines={[{ metric: 'error_rate_pct', label: 'Error rate', color: 'rgb(var(--error-rgb, 229 50 31))' }]}
          fromMs={range.fromMs}
          toMs={range.toMs}
          unit="%"
          thresholds={[{ value: 5, label: '5% · auto-heal threshold' }]}
          height={220}
          refreshing={refreshing}
        />
      </section>

      <Card id="instances" className="scroll-mt-20 gap-0 rounded-xl py-0">
        <CardHeader className="border-b border-border px-5 py-4">
          <CardTitle className="text-sm font-semibold">Instances</CardTitle>
          <CardDescription>
            {ready.length} ready of {instanceList.length} · live every 2 s · select a row to inspect its slots
          </CardDescription>
        </CardHeader>
        <CardContent className="px-0 py-0">
          <InstanceTable instances={instanceList} live={engineLive} colors={colors} selectedId={selectedId} onSelect={(id) => setParams({ instance: id })} />
        </CardContent>
      </Card>

      <SessionsPanel
        instance={selected}
        snapshot={sessions.data}
        history={history.data}
        historyFromMs={range.toMs - SESSION_WINDOW_MS}
        historyToMs={range.toMs}
        isLoading={sessions.isPending && Boolean(selectedId)}
        picker={
          <ScopeSelect
            value={selectedId ?? ''}
            onValueChange={(v) => setParams({ instance: v })}
            placeholder="Busiest instance"
            options={ready.map((i) => ({ value: i.id, label: `${i.deploymentName} @ ${i.nodeName}` }))}
            width="w-[240px]"
            ariaLabel="Instance"
          />
        }
      />
    </PageContainer>
  );
}
