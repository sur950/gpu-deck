'use client';

import Link from 'next/link';
import { AlertTriangle, Boxes, Server } from 'lucide-react';
import { cn } from '@/lib/utils';
import { activityLabel, effectiveActivity, outsideHeld } from '@/components/fleet/activity-badge';
import { isNodeConnected, nodeStateView } from '@/components/fleet/node-status';
import { SegmentMeter } from '@/components/charts/inline-meter';
import { ENTITY_OTHER } from '@/components/charts/entity-colors';
import { HEALTH_LABELS, engineLabel, gpuLabel, VENDOR_LABELS } from '@/components/observability/labels';
import { RelativeTime } from '@/components/shared/relative-time';
import type { LiveEngineSample, LiveGpuSample } from '@/hooks/use-live-events';
import type { TopologyCluster, TopologyDevice, TopologyInstance, TopologyNode } from '@/types/topology';
import { formatGiB, formatPct, formatTemp } from '@/utils/metrics-format';

export type LoadMetric = 'util' | 'vram' | 'temp';

export const LOAD_METRICS: { id: LoadMetric; label: string; legend: string }[] = [
  { id: 'util', label: 'Utilisation', legend: 'Fill level = GPU utilisation' },
  { id: 'vram', label: 'VRAM', legend: 'Fill level = VRAM in use' },
  { id: 'temp', label: 'Temperature', legend: 'Fill level = temperature, 30 → 90 °C' },
];

/** Above this fraction a tile is drawn as hot (status colour + icon). */
const HOT = 0.9;

export interface LiveIndex {
  gpu: Map<string, LiveGpuSample>;
  engine: Map<string, LiveEngineSample>;
}

interface Reading {
  fraction: number | null;
  label: string;
}

function reading(metric: LoadMetric, d: TopologyDevice, live: LiveGpuSample | undefined): Reading {
  const util = live?.utilizationPct ?? d.device.live.utilizationPct;
  const used = live?.memoryUsedBytes ?? d.device.live.memoryUsedBytes;
  const total = live?.memoryTotalBytes ?? d.device.vramTotalBytes;
  const temp = live?.temperatureC ?? d.device.live.temperatureC;
  if (metric === 'vram') {
    const f = used !== null && total ? used / total : null;
    return { fraction: f, label: f === null ? '—' : formatPct(f * 100, 0) };
  }
  if (metric === 'temp') {
    const f = temp === null ? null : Math.min(1, Math.max(0, (temp - 30) / 60));
    return { fraction: f, label: formatTemp(temp) };
  }
  return { fraction: util === null ? null : util / 100, label: formatPct(util, 0) };
}

interface MapProps {
  clusters: TopologyCluster[];
  live: LiveIndex;
  metric: LoadMetric;
  colors: Map<string, string>;
  /** Deployment to highlight (others dim). */
  focus: string | null;
}

/** Clusters → nodes → GPUs → slices → instances, laid out as a tree with a rail per cluster. */
export function TopologyMap({ clusters, live, metric, colors, focus }: MapProps) {
  return (
    <div className="space-y-8">
      {clusters.map(({ cluster, nodes }) => {
        const gpus = nodes.reduce((s, n) => s + n.devices.length, 0);
        const connected = nodes.filter((n) => isNodeConnected(n.node)).length;
        return (
          <section key={cluster.id} aria-label={`Cluster ${cluster.name}`}>
            <Link
              href={`/clusters/${cluster.id}`}
              className="relative z-10 inline-flex items-center gap-3 rounded-xl border border-border bg-card px-4 py-2.5 shadow-premium outline-none transition-colors hover:border-foreground/20 focus-visible:ring-2 focus-visible:ring-ring/30"
            >
              <span className="flex size-8 items-center justify-center rounded-lg border border-brand/25 bg-brand/10 text-brand">
                <Boxes className="size-4" aria-hidden />
              </span>
              <span>
                <span className="block text-sm font-semibold font-mono">{cluster.name}</span>
                <span className="block text-[11px] text-muted-foreground">
                  {cluster.type.replace(/_/g, ' ')}
                  {cluster.region ? ` · ${cluster.region}` : ''} · {connected}/{nodes.length} nodes connected · {gpus} GPU{gpus === 1 ? '' : 's'}
                </span>
              </span>
            </Link>

            {/* Rail: a vertical line from the cluster to the last node, a stub into each node. */}
            <div className="relative ml-[1.6rem] pl-8 pt-5">
              <ul className="space-y-5">
                {nodes.map((n, i) => (
                  <li key={n.node.id} className="relative">
                    <span aria-hidden className={cn('absolute -left-8 -top-5 w-px bg-border', i === nodes.length - 1 ? 'h-12' : 'bottom-0')} />
                    <span aria-hidden className="absolute -left-8 top-7 h-px w-8 bg-border" />
                    <NodeCard node={n} live={live} metric={metric} colors={colors} focus={focus} />
                  </li>
                ))}
              </ul>
            </div>
          </section>
        );
      })}
    </div>
  );
}

function NodeCard({ node: n, live, metric, colors, focus }: { node: TopologyNode; live: LiveIndex; metric: LoadMetric; colors: Map<string, string>; focus: string | null }) {
  const node = n.node;
  const state = nodeStateView(node);
  const hasFocus = focus !== null && n.devices.some((d) => d.instances.some((i) => i.deploymentId === focus));
  return (
    <article
      className={cn(
        'rounded-xl border bg-card/90 shadow-premium backdrop-blur-[2px] transition-opacity duration-200',
        state.detached || node.status === 'unhealthy' ? 'border-error/40' : node.status === 'draining' ? 'border-warning/40' : 'border-border',
        focus !== null && !hasFocus && 'opacity-45',
      )}
    >
      <header className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-4 py-3">
        <div className="flex min-w-0 items-center gap-2.5">
          <Server className="size-4 shrink-0 text-muted-foreground" aria-hidden />
          <Link href={`/nodes/${node.id}`} className="truncate text-sm font-semibold hover:underline font-mono">
            {node.name}
          </Link>
          <span className={cn('inline-flex items-center gap-1.5 text-[11px]', state.detached ? 'font-medium text-error-dark dark:text-error-light' : 'text-muted-foreground')} title={state.title}>
            <span aria-hidden className={cn('size-1.5 rounded-full', state.dot)} />
            {state.label}
          </span>
        </div>
        <p className="text-[11px] text-muted-foreground tabular-nums">
          {[node.os === 'macos' ? 'macOS' : node.os === 'linux' ? 'Linux' : node.os, node.arch, node.agentVersion ? `agent ${node.agentVersion.split('+')[0]}` : null].filter(Boolean).join(' · ')}
          {node.lastHeartbeatAt && (
            <>
              {' · '}
              <RelativeTime value={node.lastHeartbeatAt} tickMs={5_000} />
            </>
          )}
        </p>
      </header>
      {n.devices.length === 0 ? (
        <p className="px-4 py-5 text-xs text-muted-foreground">No GPUs reported by this node yet.</p>
      ) : (
        <div className="grid gap-3 p-3 sm:grid-cols-2 2xl:grid-cols-3">
          {n.devices.map((d) => (
            <GpuTile key={d.device.id} device={d} multi={n.devices.length > 1} live={live} metric={metric} colors={colors} focus={focus} />
          ))}
        </div>
      )}
    </article>
  );
}

function GpuTile({
  device: d,
  multi,
  live,
  metric,
  colors,
  focus,
}: {
  device: TopologyDevice;
  multi: boolean;
  live: LiveIndex;
  metric: LoadMetric;
  colors: Map<string, string>;
  focus: string | null;
}) {
  const sample = live.gpu.get(d.device.id);
  const r = reading(metric, d, sample);
  const hot = (r.fraction ?? 0) >= HOT;
  const slices = d.device.slices.filter((s) => s.status !== 'released');
  const reserved = slices.reduce((s, x) => s + x.vramBytes, 0);
  const hosts = focus === null || d.instances.some((i) => i.deploymentId === focus);
  const unhealthy = d.device.health === 'unhealthy' || d.device.health === 'degraded';
  const fill = Math.round((r.fraction ?? 0) * 100);
  const memory = { usedBytes: sample?.memoryUsedBytes ?? d.device.live.memoryUsedBytes, reservedBytes: reserved, unified: d.device.isUnifiedMemory };
  // Never "Idle" while memory is held outside GPU Deck; an instance on the tile means GPU Deck runs here.
  const raw = effectiveActivity(d.device.activity, memory);
  const activity = raw === 'idle' && d.instances.length > 0 ? 'gpu_deck' : raw;
  // Memory held outside GPU Deck (drawn after the slices); only meaningful on discrete VRAM.
  const held = activity === 'external' || activity === 'mixed' ? outsideHeld(memory) : 0;

  return (
    <div
      className={cn(
        'group/tile relative isolate overflow-hidden rounded-lg border bg-background/60 transition-[opacity,border-color] duration-200',
        hot || unhealthy ? 'border-warning/50' : 'border-border hover:border-foreground/25',
        !hosts && 'opacity-40',
      )}
    >
      {/* Load level: the tile fills from the bottom; the top edge is a hairline. */}
      <div aria-hidden className="pointer-events-none absolute inset-x-0 bottom-0 -z-10 transition-[height] duration-700 ease-out" style={{ height: `${fill}%` }}>
        <div className={cn('absolute inset-0', hot ? 'bg-warning/15' : 'bg-gradient-to-t from-foreground/[0.09] to-foreground/[0.05] dark:from-foreground/[0.14] dark:to-foreground/[0.07]')} />
        <div className={cn('absolute inset-x-0 top-0 h-px', hot ? 'bg-warning' : 'bg-foreground/30 dark:bg-foreground/45')} />
      </div>

      <Link href={`/observability/infra/${d.device.id}`} className="block px-3 pb-2 pt-2.5 outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring/40">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <p className="truncate text-[13px] font-semibold">{gpuLabel(d.device, multi)}</p>
            <p className="truncate text-[10px] text-muted-foreground">
              {VENDOR_LABELS[d.device.vendor]} · {formatGiB(d.device.vramTotalBytes, 0)}
              {d.device.isUnifiedMemory ? ' unified' : ''}
              {unhealthy ? ` · ${HEALTH_LABELS[d.device.health]}` : ''}
            </p>
          </div>
          <p className={cn('flex items-center gap-1 text-lg font-semibold leading-none tabular-nums', hot && 'text-warning-dark dark:text-warning-light')}>
            {(hot || unhealthy) && <AlertTriangle className="size-3.5" aria-label={unhealthy ? HEALTH_LABELS[d.device.health] : 'High load'} />}
            {r.label}
          </p>
        </div>
        <div className="mt-2.5 space-y-1">
          <SegmentMeter
            total={d.device.vramTotalBytes}
            heightClass="h-1.5"
            label={`VRAM reserved ${formatGiB(reserved)} of ${formatGiB(d.device.vramTotalBytes)}${held ? `, ${formatGiB(held)} used outside GPU Deck` : ''}`}
            segments={[
              ...slices.map((s) => ({
                key: s.id,
                value: s.vramBytes,
                color: (s.deploymentId && colors.get(s.deploymentId)) || ENTITY_OTHER,
                label: `${s.name ?? 'slice'} · ${formatGiB(s.vramBytes)} · ${s.computePct}% compute`,
              })),
              ...(held ? [{ key: 'outside', value: held, color: 'rgb(var(--muted-foreground) / 0.45)', label: `Used outside GPU Deck · ${formatGiB(held)}` }] : []),
            ]}
          />
          <p className="text-[10px] text-muted-foreground tabular-nums">
            {formatGiB(reserved)} reserved · {slices.length} slice{slices.length === 1 ? '' : 's'}
            {held ? ` · ${formatGiB(held)} outside GPU Deck` : ''}
          </p>
        </div>
      </Link>

      <ul className="space-y-1 px-2 pb-2">
        {(d.instances.length === 0 || activity === 'mixed' || activity === 'detached') && (
          <li className={cn('px-1 pb-0.5 text-[11px]', activity === 'detached' ? 'text-error-dark dark:text-error-light' : activity === 'external' || activity === 'mixed' ? 'text-info-dark dark:text-info-light' : 'text-muted-foreground')}>
            {activity === 'idle' || activity === null
              ? 'Idle — nothing placed'
              : activity === 'mixed'
                ? `Also used outside GPU Deck${d.device.outsideProcesses ? ` · ${d.device.outsideProcesses} process${d.device.outsideProcesses === 1 ? '' : 'es'}` : ''}`
                : activityLabel(activity, d.device.outsideProcesses, d.device.live.ts ?? d.device.lastSeenAt, outsideHeld(memory))}
          </li>
        )}
        {d.instances.map((inst) => (
          <InstanceChip key={inst.id} instance={inst} sample={live.engine.get(inst.id)} color={colors.get(inst.deploymentId) ?? ENTITY_OTHER} dim={focus !== null && inst.deploymentId !== focus} />
        ))}
      </ul>
    </div>
  );
}

function InstanceChip({ instance: inst, sample, color, dim }: { instance: TopologyInstance; sample: LiveEngineSample | undefined; color: string; dim: boolean }) {
  const ready = inst.state === 'ready';
  const active = sample?.activeSessions ?? inst.activeSessions;
  const waiting = sample?.waitingSessions ?? inst.waitingSessions;
  const slots = sample?.slotsTotal;
  return (
    <li>
      <Link
        href={`/observability/inference?instance=${inst.id}`}
        className={cn(
          'flex items-center gap-2 rounded-md border border-border bg-card/40 px-2 py-1 text-[11px] outline-none transition-[opacity,background-color] hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring/30',
          dim && 'opacity-40',
        )}
      >
        <span aria-hidden className="size-2 shrink-0 rounded-[2px]" style={{ backgroundColor: color }} />
        <span className="min-w-0 flex-1 truncate font-medium">{inst.deploymentName}</span>
        <span className="hidden shrink-0 text-muted-foreground sm:inline">{engineLabel(inst.engine)}</span>
        <span className="shrink-0 tabular-nums text-muted-foreground">
          {ready ? (
            <>
              <span className="font-semibold text-foreground">{active ?? 0}</span>
              {slots ? `/${slots}` : ''}
              {waiting ? <span className="font-semibold text-warning-dark dark:text-warning-light"> +{waiting}</span> : null}
            </>
          ) : (
            inst.state
          )}
        </span>
        <span aria-hidden className={cn('size-1.5 shrink-0 rounded-full', ready ? (inst.health === 'unhealthy' ? 'bg-warning' : 'bg-success') : inst.state === 'error' ? 'bg-error' : 'bg-info animate-pulse-subtle')} />
      </Link>
    </li>
  );
}
