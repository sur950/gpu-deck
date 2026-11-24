'use client';

import { useMemo } from 'react';
import { cn } from '@/lib/utils';
import Link from 'next/link';
import { AlertTriangle, ArrowLeft, Cpu, Fan, Gauge, MemoryStick, Thermometer, Zap } from 'lucide-react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { PageContainer } from '@/components/shared/PageContainer';
import { PageHeader } from '@/components/shared/PageHeader';
import { EmptyState } from '@/components/shared/EmptyState';
import { ErrorState } from '@/components/shared/ErrorState';
import { StatusBadge, toBadgeVariant } from '@/components/shared/StatusBadge';
import { ActivityBadge, activityLabel, effectiveActivity, outsideHeld } from '@/components/fleet/activity-badge';
import { deviceExtraRows } from '@/components/fleet/device-properties';
import { DetailList } from '@/components/shared/detail-list';
import { RangeBar } from '@/components/charts/range-bar';
import { LiveIndicator } from '@/components/charts/live-indicator';
import { StatTile } from '@/components/charts/stat-tile';
import { SegmentMeter } from '@/components/charts/inline-meter';
import { ENTITY_OTHER } from '@/components/charts/entity-colors';
import { filterSeries, maxValue, sumValues, toSpark } from '@/components/charts/series-utils';
import { MetricsChartCard } from '@/components/observability/entity-chart';
import { HOT_TEMP_C } from '@/components/observability/gpu-card';
import { MetricsPageSkeleton } from '@/components/observability/page-skeletons';
import { HEALTH_LABELS, VENDOR_LABELS, engineLabel, gpuLabel, INSTANCE_STATE_LABELS } from '@/components/observability/labels';
import { useDevice, useDevices } from '@/hooks/use-topology';
import { useGpuMetrics, useInstances } from '@/hooks/use-metrics';
import { readGpuSamples, useLiveEvents } from '@/hooks/use-live-events';
import { useTimeRange } from '@/hooks/use-time-range';
import { useDeploymentColors } from '@/hooks/use-entity-colors';
import { ApiError } from '@/lib/api/client';
import { formatCount, formatGiB, formatGiBPair, formatMhz, formatPct, formatTemp, formatWatts } from '@/utils/metrics-format';

const RANGES = ['15m', '1h', '6h', '24h', '7d'];
const DETAIL_METRICS = [
  'gpu_utilization_pct',
  'gpu_memory_util_pct',
  'gpu_memory_used_bytes',
  'gpu_power_watts',
  'gpu_temperature_c',
  'gpu_sm_clock_mhz',
  'gpu_fan_pct',
  'gpu_error_count',
];

/** One GPU in depth: live tiles, every metric over the range, properties, slices and what runs on it. */
export function DeviceDetail({ deviceId }: { deviceId: string }) {
  const range = useTimeRange('1h');
  const device = useDevice(deviceId);
  const nodeId = device.data?.nodeId;
  const metrics = useGpuMetrics({ from: range.from, to: range.to, step: range.step, deviceIds: [deviceId], metrics: DETAIL_METRICS, aggregate: 'none' });
  const instances = useInstances({ nodeId });
  const siblings = useDevices({ nodeId });
  const live = useLiveEvents({ topics: ['metrics'], scope: { nodeId }, intervalSeconds: 2, enabled: Boolean(nodeId) });
  const sample = readGpuSamples(live.latest.metrics).find((g) => g.deviceId === deviceId);

  const onDevice = useMemo(() => (instances.data ?? []).filter((i) => i.deviceIds.includes(deviceId)), [instances.data, deviceId]);
  const deploymentColors = useDeploymentColors();

  const back = (
    <Link href="/observability/infra" className="inline-flex items-center gap-1.5 text-xs font-medium text-muted-foreground transition-colors hover:text-foreground">
      <ArrowLeft className="size-3.5" />
      GPUs / infra
    </Link>
  );

  if (device.isPending) return <MetricsPageSkeleton charts={6} />;
  if (device.isError || !device.data) {
    const missing = device.error instanceof ApiError && device.error.status === 404;
    return (
      <PageContainer>
        {back}
        {missing ? (
          <Card className="rounded-xl">
            <CardContent>
              <EmptyState icon={Cpu} title="GPU not found" description="It may have been removed with its node. Pick a GPU from the infra overview." />
            </CardContent>
          </Card>
        ) : (
          <ErrorState title="Couldn't load this GPU" description="The device did not load. Try again in a moment." onRetry={() => void device.refetch()} />
        )}
      </PageContainer>
    );
  }

  const d = device.data;
  const r = metrics.data;
  const pick = (metric: string) => filterSeries(r, metric, (l) => l.device_id === deviceId)[0];
  const util = sample?.utilizationPct ?? d.live.utilizationPct;
  const used = sample?.memoryUsedBytes ?? d.live.memoryUsedBytes;
  const total = sample?.memoryTotalBytes ?? d.vramTotalBytes;
  const power = sample?.powerWatts ?? d.live.powerWatts;
  const temp = sample?.temperatureC ?? d.live.temperatureC;
  const throttle = sample?.throttleReasons ?? [];
  const errors = sumValues(pick('gpu_error_count'));
  const hasClocks = (pick('gpu_sm_clock_mhz')?.points ?? []).some((p) => p.v !== null);
  const hasFan = (pick('gpu_fan_pct')?.points ?? []).some((p) => p.v !== null);
  const slices = d.slices.filter((s) => s.status !== 'released');
  const reserved = slices.reduce((s, x) => s + x.vramBytes, 0);
  const refreshing = metrics.isFetching && !metrics.isPending;
  const activity = effectiveActivity(d.activity, { usedBytes: used, reservedBytes: reserved, unified: d.isUnifiedMemory });
  const match = (l: Record<string, string>) => l.device_id === deviceId;

  return (
    <PageContainer>
      {back}
      <PageHeader
        title={`${gpuLabel(d, (siblings.data?.length ?? 1) > 1)} · ${d.nodeName}`}
        description={[VENDOR_LABELS[d.vendor], `${formatGiB(d.vramTotalBytes, 0)}${d.isUnifiedMemory ? ' unified memory' : ' VRAM'}`, d.runtimeVersion, d.driverVersion ? `driver ${d.driverVersion}` : null]
          .filter(Boolean)
          .join(' · ')}
        actions={
          <>
            <StatusBadge variant={toBadgeVariant(d.health)}>{HEALTH_LABELS[d.health]}</StatusBadge>
            <ActivityBadge activity={d.activity} outside={d.outsideProcesses} lastSeenAt={d.live.ts ?? d.lastSeenAt} usedBytes={used} reservedBytes={reserved} unified={d.isUnifiedMemory} />
            <LiveIndicator status={live.status} detail="2 s" />
          </>
        }
      />
      {d.healthReason && (
        <p className="flex items-center gap-2 rounded-md border border-warning/30 bg-warning/10 px-3 py-2 text-sm text-warning-dark dark:text-warning-light">
          <AlertTriangle className="size-4" /> {d.healthReason}
        </p>
      )}

      <RangeBar range={range.rangeId} onRangeChange={range.setRange} step={range.stepId} onStepChange={range.setStep} presets={RANGES} />

      <section aria-label="Now" className="grid gap-3 sm:grid-cols-3 xl:grid-cols-6">
        <StatTile label="Utilisation" icon={Gauge} value={formatPct(util, 0)} hint={`peak ${formatPct(maxValue(pick('gpu_utilization_pct')), 0)} in range`} trend={toSpark(pick('gpu_utilization_pct'), 60)} trendDomain={[0, 100]} />
        <StatTile label="VRAM" icon={MemoryStick} value={formatGiB(used)} unit={`/ ${formatGiB(total, 0)}`} hint={`${formatPct(total ? ((used ?? 0) / total) * 100 : null, 0)} used · ${formatGiB(reserved)} reserved`} trend={toSpark(pick('gpu_memory_used_bytes'), 60)} trendColor="rgb(var(--chart-4))" />
        <StatTile label="Power" icon={Zap} value={formatWatts(power)} unit={d.maxPowerWatts ? `/ ${formatWatts(d.maxPowerWatts)}` : undefined} hint={d.maxPowerWatts ? `${formatPct(((power ?? 0) / d.maxPowerWatts) * 100, 0)} of limit` : 'best-effort reading'} trend={toSpark(pick('gpu_power_watts'), 60)} trendColor="rgb(var(--chart-3))" />
        <StatTile label="Temperature" icon={Thermometer} value={formatTemp(temp)} hint={throttle.length > 0 ? `throttling: ${throttle.join(', ')}` : `max ${formatTemp(maxValue(pick('gpu_temperature_c')))} in range`} tone={(temp ?? 0) >= HOT_TEMP_C || throttle.length > 0 ? 'warn' : 'default'} trend={toSpark(pick('gpu_temperature_c'), 60)} trendColor="rgb(var(--chart-5))" />
        {hasClocks ? (
          <StatTile label="SM clock" icon={Cpu} value={formatMhz(sample?.smClockMhz ?? null)} hint={`max ${formatMhz(maxValue(pick('gpu_sm_clock_mhz')))} in range`} trend={toSpark(pick('gpu_sm_clock_mhz'), 60)} />
        ) : (
          <StatTile label="GPU cores" icon={Cpu} value={d.coreCount ? formatCount(d.coreCount) : '—'} hint={d.computeCapability ? `compute ${d.computeCapability}` : 'clock not reported'} />
        )}
        {hasFan ? (
          <StatTile label="Fan" icon={Fan} value={formatPct(sample?.fanPct ?? null, 0)} hint={`${formatCount(errors)} error events in range`} tone={errors > 0 ? 'bad' : 'default'} trend={toSpark(pick('gpu_fan_pct'), 60)} trendDomain={[0, 100]} />
        ) : (
          <StatTile label="Errors" icon={AlertTriangle} value={formatCount(errors)} unit="in range" hint="XID / ECC / driver events" tone={errors > 0 ? 'bad' : 'good'} />
        )}
      </section>

      <div className="grid gap-4 xl:grid-cols-3">
        <div className="grid content-start gap-4 md:grid-cols-2 xl:col-span-2">
          <MetricsChartCard
            title="Utilisation"
            description="SM busy and memory controller busy"
            result={r}
            lines={[
              { metric: 'gpu_utilization_pct', label: 'SM', color: 'rgb(var(--chart-2))', match },
              { metric: 'gpu_memory_util_pct', label: 'Memory controller', color: 'rgb(var(--chart-4))', dashed: true, match },
            ]}
            fromMs={range.fromMs}
            toMs={range.toMs}
            yDomain={[0, 100]}
            refreshing={refreshing}
          />
          <MetricsChartCard
            title="VRAM used"
            description={`Of ${formatGiB(total)}`}
            result={r}
            lines={[{ metric: 'gpu_memory_used_bytes', label: 'VRAM used', color: 'rgb(var(--chart-4))', match }]}
            fromMs={range.fromMs}
            toMs={range.toMs}
            yDomain={[0, total || 'auto']}
            refreshing={refreshing}
          />
          <MetricsChartCard
            title="Power"
            description={d.maxPowerWatts ? `Limit ${formatWatts(d.maxPowerWatts)}` : 'Best-effort reading without root'}
            result={r}
            lines={[{ metric: 'gpu_power_watts', label: 'Power', color: 'rgb(var(--chart-3))', match }]}
            fromMs={range.fromMs}
            toMs={range.toMs}
            thresholds={d.maxPowerWatts ? [{ value: d.maxPowerWatts, label: `limit ${formatWatts(d.maxPowerWatts)}`, color: 'rgb(var(--muted-foreground))' }] : undefined}
            refreshing={refreshing}
          />
          <MetricsChartCard
            title="Temperature"
            description="Core; throttling starts in the 80s on most cards"
            result={r}
            lines={[{ metric: 'gpu_temperature_c', label: 'Core', color: 'rgb(var(--chart-5))', match }]}
            fromMs={range.fromMs}
            toMs={range.toMs}
            thresholds={[{ value: HOT_TEMP_C, label: `${HOT_TEMP_C} °C` }]}
            refreshing={refreshing}
          />
          {hasClocks && (
            <MetricsChartCard
              title="SM clock"
              description="Boost tracks load and temperature"
              result={r}
              lines={[{ metric: 'gpu_sm_clock_mhz', label: 'SM clock', color: 'rgb(var(--chart-2))', match }]}
              fromMs={range.fromMs}
              toMs={range.toMs}
              refreshing={refreshing}
            />
          )}
          <MetricsChartCard
            title="Error events"
            description="XID, ECC and driver errors per bucket"
            result={r}
            lines={[{ metric: 'gpu_error_count', label: 'Errors', color: 'rgb(var(--error-rgb, 229 50 31))', kind: 'bar', match }]}
            fromMs={range.fromMs}
            toMs={range.toMs}
            refreshing={refreshing}
            zeroIsEmpty
            emptyTitle="No error events"
            emptyDescription="This GPU reported clean in the range."
          />
        </div>

        <aside className="space-y-4">
          <Card className="gap-0 rounded-xl py-0">
            <CardHeader className="border-b border-border px-5 py-4">
              <CardTitle className="text-sm font-semibold">Placement</CardTitle>
              <CardDescription>VRAM reserved by deployments · {formatGiBPair(reserved, d.vramTotalBytes)}</CardDescription>
            </CardHeader>
            <CardContent className="space-y-3 px-5 py-4">
              <SegmentMeter
                total={d.vramTotalBytes}
                label={`Reserved ${formatGiBPair(reserved, d.vramTotalBytes)}`}
                heightClass="h-2.5"
                segments={slices.map((s) => ({ key: s.id, value: s.vramBytes, color: (s.deploymentId && deploymentColors.get(s.deploymentId)) || ENTITY_OTHER, label: `${s.name ?? 'slice'} · ${formatGiB(s.vramBytes)}` }))}
              />
              {onDevice.length === 0 ? (
                <p className={cn('text-xs', activity === 'detached' ? 'text-error-dark dark:text-error-light' : activity === 'external' ? 'text-info-dark dark:text-info-light' : 'text-muted-foreground')}>
                  {activity && activity !== 'idle' && activity !== 'gpu_deck'
                    ? `${activityLabel(activity, d.outsideProcesses, d.live.ts ?? d.lastSeenAt, outsideHeld({ usedBytes: used, reservedBytes: reserved }))} — no GPU Deck instance on this GPU.`
                    : 'No model instances on this GPU.'}
                </p>
              ) : (
                <ul className="divide-y divide-border">
                  {onDevice.map((inst) => {
                    const slice = slices.find((s) => s.instanceId === inst.id);
                    return (
                      <li key={inst.id} className="py-2.5 first:pt-0 last:pb-0">
                        <div className="flex items-center gap-2">
                          <span aria-hidden className="size-2.5 shrink-0 rounded-[3px]" style={{ backgroundColor: deploymentColors.get(inst.deploymentId) ?? ENTITY_OTHER }} />
                          <Link href={`/observability/inference?instance=${inst.id}`} className="min-w-0 flex-1 truncate text-[13px] font-semibold hover:underline">
                            {inst.deploymentName}
                          </Link>
                          <StatusBadge variant={inst.state === 'ready' ? 'running' : toBadgeVariant(inst.state)} className="px-1.5 py-0 text-[10px]">
                            {INSTANCE_STATE_LABELS[inst.state]}
                          </StatusBadge>
                        </div>
                        <p className="mt-1 pl-[18px] text-[11px] text-muted-foreground tabular-nums">
                          {engineLabel(inst.engine)} · {inst.servedModelName ?? '—'}
                          {slice ? ` · ${formatGiB(slice.vramBytes)} · ${slice.computePct}% compute` : ''}
                        </p>
                        {inst.state === 'ready' && (
                          <p className="mt-0.5 pl-[18px] text-[11px] text-muted-foreground tabular-nums">
                            {inst.live.activeSessions ?? 0}/{inst.slots ?? '—'} slots busy
                            {inst.live.waitingSessions ? ` · ${inst.live.waitingSessions} waiting` : ''} · KV {formatPct(inst.live.kvCacheUsagePct, 0)}
                          </p>
                        )}
                      </li>
                    );
                  })}
                </ul>
              )}
            </CardContent>
          </Card>

          <Card className="gap-0 rounded-xl py-0">
            <CardHeader className="border-b border-border px-5 py-4">
              <CardTitle className="text-sm font-semibold">Properties</CardTitle>
              <CardDescription>As the node agent reports them</CardDescription>
            </CardHeader>
            <CardContent className="px-5 py-4">
              <DetailList
                items={[
                  { label: 'Node', value: <Link href={`/nodes/${d.nodeId}`} className="hover:underline font-mono">{d.nodeName}</Link> },
                  { label: 'Index', value: String(d.index) },
                  { label: 'Vendor', value: VENDOR_LABELS[d.vendor] },
                  { label: 'Model', value: d.name },
                  { label: 'UUID', value: d.uuid ? <span className="text-[11px] font-mono">{d.uuid}</span> : null },
                  { label: 'Memory', value: `${formatGiB(d.vramTotalBytes)}${d.isUnifiedMemory ? ' (unified)' : ''}` },
                  { label: 'Driver', value: d.driverVersion },
                  { label: 'Runtime', value: d.runtimeVersion },
                  { label: 'Compute', value: d.computeCapability },
                  { label: 'Cores', value: d.coreCount ? formatCount(d.coreCount) : null },
                  { label: 'Power limit', value: d.maxPowerWatts ? formatWatts(d.maxPowerWatts) : null },
                  { label: 'PCI bus', value: d.pciBusId ? <span className="text-[11px] font-mono">{d.pciBusId}</span> : null },
                  ...deviceExtraRows(d.extra),
                ]}
              />
            </CardContent>
          </Card>
        </aside>
      </div>
    </PageContainer>
  );
}
