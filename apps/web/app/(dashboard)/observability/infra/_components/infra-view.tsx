'use client';

import { useMemo } from 'react';
import { AlertOctagon, Cpu, Gauge, MemoryStick, ShieldCheck, Thermometer, Zap } from 'lucide-react';
import { Card, CardContent } from '@/components/ui/card';
import { PageContainer } from '@/components/shared/PageContainer';
import { PageHeader } from '@/components/shared/PageHeader';
import { EmptyState } from '@/components/shared/EmptyState';
import { ErrorState } from '@/components/shared/ErrorState';
import { RelativeTime } from '@/components/shared/relative-time';
import { RangeBar, ScopeSelect } from '@/components/charts/range-bar';
import { LiveIndicator } from '@/components/charts/live-indicator';
import { StatTile } from '@/components/charts/stat-tile';
import { combineSeries, filterSeries, maxValue, sumValues, toSpark } from '@/components/charts/series-utils';
import { EntityMetricChart } from '@/components/observability/entity-chart';
import type { ChartEntity } from '@/components/observability/entity-chart';
import { GpuCard, HOT_TEMP_C } from '@/components/observability/gpu-card';
import { FleetLatencyCard } from '@/components/observability/fleet-latency-card';
import { MetricsPageSkeleton } from '@/components/observability/page-skeletons';
import { gpuLabel } from '@/components/observability/labels';
import { NodeStatusBadge } from '@/components/fleet/node-status';
import { useDevices, useNodes } from '@/hooks/use-topology';
import { useGpuMetrics, useInstances } from '@/hooks/use-metrics';
import { readGpuSamples, useLiveEvents } from '@/hooks/use-live-events';
import { useTimeRange } from '@/hooks/use-time-range';
import { useDeploymentColors, useDeviceColors } from '@/hooks/use-entity-colors';
import { useUrlState } from '@/hooks/use-url-state';
import { formatCount, formatGiB, formatPct, formatTemp, formatWatts } from '@/utils/metrics-format';

const RANGES = ['15m', '1h', '6h', '24h', '7d'];
const GPU_METRICS = ['gpu_utilization_pct', 'gpu_memory_used_bytes', 'gpu_power_watts', 'gpu_temperature_c', 'gpu_error_count'];
const SCOPE_DEFAULTS = { node: '' };
const BUSY_PCT = 10;

/** Observability › GPUs / infra: fleet tiles, per-GPU comparison charts and a card per GPU grouped by node. */
export function InfraView() {
  const range = useTimeRange('1h');
  const { params, setParams } = useUrlState(SCOPE_DEFAULTS);
  const nodeId = params.node || undefined;

  const nodes = useNodes();
  const devices = useDevices({ nodeId });
  const instances = useInstances({ nodeId });
  const metrics = useGpuMetrics({ from: range.from, to: range.to, step: range.step, nodeId, metrics: GPU_METRICS, aggregate: 'none' });
  const live = useLiveEvents({ topics: ['metrics'], scope: { nodeId }, intervalSeconds: 2 });
  const gpuLive = readGpuSamples(live.latest.metrics);

  const deviceList = useMemo(() => devices.data ?? [], [devices.data]);
  const liveById = useMemo(() => new Map(gpuLive.map((g) => [g.deviceId, g])), [gpuLive]);

  const deviceColors = useDeviceColors();
  const deploymentColors = useDeploymentColors();
  const multiByNode = useMemo(() => {
    const counts = new Map<string, number>();
    for (const d of deviceList) counts.set(d.nodeId, (counts.get(d.nodeId) ?? 0) + 1);
    return counts;
  }, [deviceList]);
  const entities: ChartEntity[] = useMemo(
    () =>
      [...deviceList]
        .sort((a, b) => `${a.nodeName}#${a.index}`.localeCompare(`${b.nodeName}#${b.index}`))
        .map((d) => ({
          id: d.id,
          label: (multiByNode.get(d.nodeId) ?? 0) > 1 || deviceList.length > 1 ? `${d.nodeName} · ${gpuLabel(d, (multiByNode.get(d.nodeId) ?? 0) > 1)}` : gpuLabel(d, false),
          color: deviceColors.get(d.id) ?? 'rgb(var(--muted-foreground))',
        })),
    [deviceList, deviceColors, multiByNode],
  );

  const nodeOptions = (nodes.data ?? []).map((n) => ({ value: n.id, label: n.name }));
  const header = (
    <PageHeader
      title="GPUs / infra"
      description="Utilisation, VRAM, power, temperature and errors per GPU and node."
      actions={<LiveIndicator status={live.status} detail="2 s" />}
    />
  );
  const filters = (
    <RangeBar range={range.rangeId} onRangeChange={range.setRange} step={range.stepId} onStepChange={range.setStep} presets={RANGES}>
      <ScopeSelect value={params.node} onValueChange={(v) => setParams({ node: v })} placeholder="All nodes" options={nodeOptions} ariaLabel="Node" />
    </RangeBar>
  );

  if (devices.isPending) return <MetricsPageSkeleton />;
  if (devices.isError) {
    return (
      <PageContainer>
        {header}
        <ErrorState title="Couldn't load GPUs" description="The device inventory did not load. Try again in a moment." onRetry={() => void devices.refetch()} />
      </PageContainer>
    );
  }
  if (deviceList.length === 0) {
    return (
      <PageContainer>
        {header}
        {filters}
        <Card className="rounded-xl">
          <CardContent>
            <EmptyState icon={Cpu} title="No GPU samples" description="The node agent reports utilisation, VRAM, power and heat every second or two once a node joins." />
          </CardContent>
        </Card>
      </PageContainer>
    );
  }

  // ── Fleet tiles ──
  const r = metrics.data;
  const now = deviceList.map((d) => ({ d, l: liveById.get(d.id) }));
  const utilNow = now.map(({ d, l }) => l?.utilizationPct ?? d.live.utilizationPct ?? 0);
  const avgUtil = utilNow.reduce((a, b) => a + b, 0) / Math.max(1, utilNow.length);
  const busy = utilNow.filter((u) => u >= BUSY_PCT).length;
  const vramUsed = now.reduce((s, { d, l }) => s + (l?.memoryUsedBytes ?? d.live.memoryUsedBytes ?? 0), 0);
  const vramTotal = deviceList.reduce((s, d) => s + d.vramTotalBytes, 0);
  const powerNow = now.reduce((s, { d, l }) => s + (l?.powerWatts ?? d.live.powerWatts ?? 0), 0);
  const hottest = now.reduce<{ name: string; temp: number } | null>((best, { d, l }) => {
    const t = l?.temperatureC ?? d.live.temperatureC;
    if (t === null || t === undefined) return best;
    return !best || t > best.temp ? { name: `${d.nodeName} · ${gpuLabel(d, (multiByNode.get(d.nodeId) ?? 0) > 1)}`, temp: t } : best;
  }, null);
  const healthy = deviceList.filter((d) => d.health === 'healthy').length;
  const errorsInRange = filterSeries(r, 'gpu_error_count').reduce((s, x) => s + sumValues(x), 0);
  const utilSeries = combineSeries(filterSeries(r, 'gpu_utilization_pct'), 'avg');
  const vramSeries = combineSeries(filterSeries(r, 'gpu_memory_used_bytes'), 'sum');
  const powerSeries = combineSeries(filterSeries(r, 'gpu_power_watts'), 'sum');
  const tempSeries = combineSeries(filterSeries(r, 'gpu_temperature_c'), 'max');
  const stepSeconds = (range.toMs - range.fromMs) / 1000 / Math.max(1, powerSeries?.points.length ?? 1);
  const energyKwh = powerSeries ? (sumValues(powerSeries) * stepSeconds) / 3600 / 1000 : null;
  const refreshing = metrics.isFetching && !metrics.isPending;

  // ── Node groups ──
  const nodeIds = [...new Set(deviceList.map((d) => d.nodeId))].sort((a, b) =>
    (deviceList.find((d) => d.nodeId === a)?.nodeName ?? '').localeCompare(deviceList.find((d) => d.nodeId === b)?.nodeName ?? ''),
  );

  return (
    <PageContainer>
      {header}
      {filters}

      <section aria-label="Fleet now" className="grid gap-3 sm:grid-cols-3 xl:grid-cols-6">
        <StatTile label="GPUs" icon={Cpu} value={String(deviceList.length)} unit={`· ${healthy} healthy`} hint={`${busy} busy above ${BUSY_PCT}% · ${nodeIds.length} node${nodeIds.length === 1 ? '' : 's'}`} tone={healthy < deviceList.length ? 'warn' : 'default'} />
        <StatTile label="Utilisation" icon={Gauge} value={formatPct(avgUtil, 0)} unit="avg" hint={`peak ${formatPct(maxValue(utilSeries), 0)} in range`} trend={toSpark(utilSeries, 60)} trendDomain={[0, 100]} />
        <StatTile label="VRAM used" icon={MemoryStick} value={formatGiB(vramUsed)} unit={`/ ${formatGiB(vramTotal, 0)}`} hint={`${formatPct(vramTotal ? (vramUsed / vramTotal) * 100 : null, 0)} of fleet memory`} trend={toSpark(vramSeries, 60)} trendColor="rgb(var(--chart-4))" />
        <StatTile label="Power draw" icon={Zap} value={formatWatts(powerNow)} hint={energyKwh !== null ? `${energyKwh.toFixed(energyKwh < 10 ? 2 : 1)} kWh in range` : undefined} trend={toSpark(powerSeries, 60)} trendColor="rgb(var(--chart-3))" />
        <StatTile label="Hottest GPU" icon={Thermometer} value={formatTemp(hottest?.temp)} hint={hottest?.name} tone={(hottest?.temp ?? 0) >= HOT_TEMP_C ? 'warn' : 'default'} trend={toSpark(tempSeries, 60)} trendColor="rgb(var(--chart-5))" />
        <StatTile label="GPU errors" icon={AlertOctagon} value={formatCount(errorsInRange)} unit="in range" hint="XID / ECC / driver events" tone={errorsInRange > 0 ? 'bad' : 'good'} />
      </section>

      <section aria-label="Per-GPU charts" className="grid gap-4 lg:grid-cols-2">
        <EntityMetricChart title="Utilisation" description="SM busy, per GPU" result={r} metric="gpu_utilization_pct" labelKey="device_id" entities={entities} fromMs={range.fromMs} toMs={range.toMs} yDomain={[0, 100]} refreshing={refreshing} />
        <EntityMetricChart title="VRAM used" description="Weights, KV cache and activations, per GPU" result={r} metric="gpu_memory_used_bytes" labelKey="device_id" entities={entities} fromMs={range.fromMs} toMs={range.toMs} refreshing={refreshing} />
        <EntityMetricChart title="Power" description="Board power draw, per GPU" result={r} metric="gpu_power_watts" labelKey="device_id" entities={entities} fromMs={range.fromMs} toMs={range.toMs} refreshing={refreshing} />
        <EntityMetricChart
          title="Temperature"
          description="Core temperature, per GPU"
          result={r}
          metric="gpu_temperature_c"
          labelKey="device_id"
          entities={entities}
          fromMs={range.fromMs}
          toMs={range.toMs}
          thresholds={[{ value: HOT_TEMP_C, label: `${HOT_TEMP_C} °C` }]}
          refreshing={refreshing}
        />
      </section>

      <FleetLatencyCard nodeId={nodeId} />

      {nodeIds.map((id) => {
        const node = nodes.data?.find((n) => n.id === id);
        const group = deviceList.filter((d) => d.nodeId === id).sort((a, b) => a.index - b.index);
        const name = group[0]?.nodeName ?? node?.name ?? id;
        return (
          <section key={id} aria-label={`Node ${name}`} className="space-y-3">
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
              <h2 className="text-sm font-semibold font-mono">{name}</h2>
              {node && (
<NodeStatusBadge node={node} className="px-1.5 py-0 text-[10px]" />
              )}
              {node && (
                <span className="text-xs text-muted-foreground">
                  {[node.os === 'macos' ? 'macOS' : node.os === 'linux' ? 'Linux' : node.os, node.osVersion, node.arch, node.agentVersion ? `agent ${node.agentVersion}` : null].filter(Boolean).join(' · ')}
                </span>
              )}
              {node?.lastHeartbeatAt && (
                <span className="text-xs text-muted-foreground">
                  · heartbeat <RelativeTime value={node.lastHeartbeatAt} tickMs={5_000} />
                </span>
              )}
            </div>
            <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
              {group.map((d) => (
                <GpuCard
                  key={d.id}
                  device={d}
                  live={liveById.get(d.id)}
                  series={r}
                  instances={(instances.data ?? []).filter((i) => i.deviceIds.includes(d.id))}
                  deploymentColors={deploymentColors}
                  showIndex={group.length > 1}
                />
              ))}
            </div>
          </section>
        );
      })}

      {errorsInRange > 0 ? (
        <EntityMetricChart
          title="GPU errors"
          description="XID, ECC and driver error events per bucket"
          result={r}
          metric="gpu_error_count"
          labelKey="device_id"
          entities={entities}
          fromMs={range.fromMs}
          toMs={range.toMs}
          kind="bar"
          stacked
          height={150}
          refreshing={refreshing}
        />
      ) : (
        <div className="flex items-center gap-3 rounded-xl border border-border bg-card px-5 py-4 shadow-premium">
          <span className="flex size-8 items-center justify-center rounded-lg border border-success/25 bg-success/10 text-success-dark dark:text-success-light">
            <ShieldCheck className="size-4" aria-hidden />
          </span>
          <div>
            <p className="text-sm font-semibold">No GPU error events in this range</p>
            <p className="text-xs text-muted-foreground">XID, ECC and driver errors chart here per GPU when an agent reports them.</p>
          </div>
        </div>
      )}
    </PageContainer>
  );
}
