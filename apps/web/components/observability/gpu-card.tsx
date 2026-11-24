'use client';

import Link from 'next/link';
import { AlertTriangle, ArrowUpRight } from 'lucide-react';
import { cn } from '@/lib/utils';
import { activityLabel, effectiveActivity, outsideHeld } from '@/components/fleet/activity-badge';
import { StatusBadge, toBadgeVariant } from '@/components/shared/StatusBadge';
import { MiniChart, MiniStat } from '@/components/charts/mini-chart';
import { SegmentMeter } from '@/components/charts/inline-meter';
import { ENTITY_OTHER } from '@/components/charts/entity-colors';
import { filterSeries } from '@/components/charts/series-utils';
import { HEALTH_LABELS, VENDOR_LABELS, engineLabel, gpuLabel } from '@/components/observability/labels';
import type { LiveGpuSample } from '@/hooks/use-live-events';
import type { InstanceSummary, MetricsResult } from '@/types/metrics';
import type { DeviceSummary } from '@/types/topology';
import { formatGiB, formatGiBPair, formatPct, formatTemp, formatWatts } from '@/utils/metrics-format';

export const HOT_TEMP_C = 80;

interface GpuCardProps {
  device: DeviceSummary;
  live: LiveGpuSample | undefined;
  series: MetricsResult | undefined;
  instances: InstanceSummary[];
  deploymentColors: Map<string, string>;
  showIndex: boolean;
}

function values(result: MetricsResult | undefined, metric: string, deviceId: string): (number | null)[] {
  return filterSeries(result, metric, (l) => l.device_id === deviceId)[0]?.points.map((p) => p.v) ?? [];
}

/**
 * One GPU: live utilisation front and centre, VRAM / power / temperature with
 * their range trend, VRAM reservations by deployment and what runs on it.
 */
export function GpuCard({ device, live, series, instances, deploymentColors, showIndex }: GpuCardProps) {
  const util = live?.utilizationPct ?? device.live.utilizationPct;
  const used = live?.memoryUsedBytes ?? device.live.memoryUsedBytes;
  const total = live?.memoryTotalBytes ?? device.vramTotalBytes;
  const power = live?.powerWatts ?? device.live.powerWatts;
  const temp = live?.temperatureC ?? device.live.temperatureC;
  const hot = (temp ?? 0) >= HOT_TEMP_C;
  const throttled = (live?.throttleReasons ?? []).length > 0;
  const slices = device.slices.filter((s) => s.status !== 'released');
  const reserved = slices.reduce((s, x) => s + x.vramBytes, 0);
  const href = `/observability/infra/${device.id}`;
  const memory = { usedBytes: used, reservedBytes: reserved, unified: device.isUnifiedMemory };
  const activity = effectiveActivity(device.activity, memory);

  return (
    <article className="group/gpu flex flex-col rounded-xl border border-border bg-card shadow-premium transition-colors hover:border-foreground/15">
      <header className="flex items-start justify-between gap-3 px-4 pt-4">
        <div className="min-w-0">
          <Link href={href} className="flex items-center gap-1.5 rounded-sm text-sm font-semibold outline-none hover:underline focus-visible:ring-2 focus-visible:ring-ring/30">
            {gpuLabel(device, showIndex)}
            <ArrowUpRight className="size-3.5 text-muted-foreground opacity-0 transition-opacity group-hover/gpu:opacity-100" aria-hidden />
          </Link>
          <p className="mt-0.5 truncate text-[11px] text-muted-foreground">
            {VENDOR_LABELS[device.vendor]} · {formatGiB(device.vramTotalBytes, 0)}
            {device.isUnifiedMemory ? ' unified' : ''}
            {device.runtimeVersion ? ` · ${device.runtimeVersion}` : ''}
            {device.pciBusId ? ` · ${device.pciBusId.replace(/^0000:/, '')}` : ''}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
          {(hot || throttled) && (
            <span className="inline-flex items-center gap-1 rounded-md border border-warning/30 bg-warning/10 px-1.5 py-0.5 text-[10px] font-semibold text-warning-dark dark:text-warning-light">
              <AlertTriangle className="size-3" aria-hidden />
              {throttled ? 'Throttling' : 'Hot'}
            </span>
          )}
          <StatusBadge variant={toBadgeVariant(device.health)} className="px-1.5 py-0.5 text-[10px]">
            {HEALTH_LABELS[device.health]}
          </StatusBadge>
        </div>
      </header>

      <div className="grid grid-cols-[auto_minmax(0,1fr)] items-end gap-4 px-4 pt-3">
        <div>
          <p className="text-[32px] font-semibold leading-none tracking-tight">{formatPct(util, 0)}</p>
          <p className="mt-1 text-[11px] text-muted-foreground">utilisation</p>
        </div>
        <MiniChart values={values(series, 'gpu_utilization_pct', device.id)} domain={[0, 100]} height={44} color="rgb(var(--chart-2))" />
      </div>

      <div className="grid grid-cols-3 gap-4 px-4 pt-4">
        <MiniStat
          label="VRAM"
          value={formatGiB(used)}
          hint={`of ${formatGiB(total)}`}
          values={values(series, 'gpu_memory_used_bytes', device.id)}
          domain={[0, total || 1]}
          color="rgb(var(--chart-4))"
        />
        <MiniStat label="Power" value={formatWatts(power)} hint={device.maxPowerWatts ? `limit ${formatWatts(device.maxPowerWatts)}` : 'best effort'} values={values(series, 'gpu_power_watts', device.id)} color="rgb(var(--chart-3))" />
        <MiniStat
          label="Temp"
          value={formatTemp(temp)}
          hint={hot ? 'above 80 °C' : 'core'}
          values={values(series, 'gpu_temperature_c', device.id)}
          reference={HOT_TEMP_C}
          color={hot ? 'rgb(var(--warning-rgb, 201 138 23))' : 'rgb(var(--chart-5))'}
        />
      </div>

      <div className="mt-4 space-y-2 border-t border-border px-4 py-3">
        <div className="flex items-baseline justify-between text-[11px] text-muted-foreground">
          <span className="font-medium">Reserved by deployments</span>
          <span className="tabular-nums">{formatGiBPair(reserved, device.vramTotalBytes)}</span>
        </div>
        <SegmentMeter
          total={device.vramTotalBytes}
          label={`VRAM reserved ${formatGiBPair(reserved, device.vramTotalBytes)}`}
          segments={slices.map((s) => ({
            key: s.id,
            value: s.vramBytes,
            color: (s.deploymentId && deploymentColors.get(s.deploymentId)) || ENTITY_OTHER,
            label: `${s.name ?? 'slice'} · ${formatGiB(s.vramBytes)} · ${s.computePct}% compute`,
          }))}
        />
        {instances.length > 0 ? (
          <ul className="space-y-1 pt-1">
            {instances.map((inst) => (
              <li key={inst.id} className="flex items-center gap-2 text-xs">
                <span aria-hidden className="size-2 shrink-0 rounded-[3px]" style={{ backgroundColor: deploymentColors.get(inst.deploymentId) ?? ENTITY_OTHER }} />
                <Link href={`/observability/inference?instance=${inst.id}`} className={cn('min-w-0 flex-1 truncate font-medium hover:underline')}>
                  {inst.deploymentName}
                </Link>
                <span className="shrink-0 text-[11px] text-muted-foreground">{engineLabel(inst.engine)}</span>
                <span className="w-16 shrink-0 text-right text-[11px] tabular-nums text-muted-foreground">
                  {inst.state === 'ready' ? `${inst.live.activeSessions ?? 0}/${inst.slots ?? '—'} slots` : inst.state}
                </span>
              </li>
            ))}
          </ul>
        ) : (
          <p className={cn('pt-1 text-xs', activity === 'detached' ? 'text-error-dark dark:text-error-light' : activity === 'external' ? 'text-info-dark dark:text-info-light' : 'text-muted-foreground')}>
            {activity && activity !== 'idle' && activity !== 'gpu_deck' ? `${activityLabel(activity, device.outsideProcesses, device.live.ts ?? device.lastSeenAt, outsideHeld(memory))} — no GPU Deck instance on this GPU.` : 'Idle — no model instances placed on this GPU.'}
          </p>
        )}
      </div>
    </article>
  );
}
