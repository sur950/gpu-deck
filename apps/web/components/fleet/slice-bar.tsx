'use client';

import { useMemo } from 'react';
import { useDeployments } from '@/hooks/api/models-deployments';
import { cn } from '@/lib/utils';
import { formatBytes } from '@/utils/format';
import type { Device, DeviceSlice } from '@/types/fleet';

/**
 * Stable slot colours for deployments on a GPU. chart-1 is the brand accent,
 * kept for single-series and headline charts, so slices start at chart-2
 * and the fifth deployment onwards shares the muted "other" tone.
 */
const SLICE_COLORS = ['rgb(var(--chart-2))', 'rgb(var(--chart-3))', 'rgb(var(--chart-4))', 'rgb(var(--chart-5))'];
const OTHER = 'rgb(var(--muted-foreground) / 0.7)';
/** Memory held by processes GPU Deck does not run: hatched neutral, never a slice colour. */
export const OUTSIDE_PATTERN = 'repeating-linear-gradient(135deg, rgb(var(--foreground) / 0.45) 0 2px, rgb(var(--foreground) / 0.18) 2px 5px)';

/**
 * Bytes used outside GPU Deck: the agent's per-process list when known, else what the driver
 * reports in use beyond GPU Deck's reservations (estimate).
 */
export function outsideBytes(device: Pick<Device, 'vram_used_outside_bytes' | 'vram_used_bytes'> & { live?: Device['live'] }, reserved: number): { bytes: number; estimated: boolean } {
  if (typeof device.vram_used_outside_bytes === 'number') return { bytes: Math.max(0, device.vram_used_outside_bytes), estimated: false };
  const used = device.vram_used_bytes ?? device.live?.memory_used_bytes ?? null;
  return { bytes: used === null ? 0 : Math.max(0, used - reserved), estimated: true };
}

/**
 * Colour per deployment, assigned by deployment name across the whole fleet
 * (same rule as the dashboards' entity colours), so a deployment keeps its
 * colour on every GPU, node and page.
 */
export function useDeploymentColors(): Map<string, string> {
  const deployments = useDeployments({ page_size: 200, sort: 'name' }, { live: false });
  return useMemo(() => {
    const sorted = [...(deployments.data?.items ?? [])].sort((a, b) => a.name.localeCompare(b.name));
    return new Map(sorted.map((d, i) => [d.id, SLICE_COLORS[i] ?? OTHER]));
  }, [deployments.data]);
}

/** Fallback colouring from the slices at hand (used before the deployment list has loaded). */
export function sliceColorMap(slices: Pick<DeviceSlice, 'deployment_id' | 'name' | 'id'>[]): Map<string, string> {
  const keys = [...new Set(slices.map((s) => s.deployment_id ?? s.id))].sort((a, b) => {
    const na = slices.find((s) => (s.deployment_id ?? s.id) === a)?.name ?? a;
    const nb = slices.find((s) => (s.deployment_id ?? s.id) === b)?.name ?? b;
    return na.localeCompare(nb);
  });
  return new Map(keys.map((k, i) => [k, SLICE_COLORS[i] ?? OTHER]));
}

interface SliceBarProps {
  device: Pick<Device, 'vram_total_bytes' | 'is_unified_memory'> &
    Partial<Pick<Device, 'vram_used_outside_bytes' | 'vram_used_bytes'>> & { slices?: DeviceSlice[] | null; live?: Device['live'] };
  /** Colours shared with a legend elsewhere on the page. */
  colors?: Map<string, string>;
  className?: string;
  heightClass?: string;
  /** Show the "used / total" readout and the usage tick. */
  showUsage?: boolean;
}

/**
 * VRAM map for one GPU: one segment per slice (reserved for a deployment), a
 * hatched segment for memory used outside GPU Deck (external servers and any
 * other process), a hairline tick for the memory the driver reports in use,
 * and the empty track as free capacity. Unified-memory devices shade the share
 * macOS keeps for the system (25 %).
 */
export function SliceBar({ device, colors, className, heightClass = 'h-3', showUsage = true }: SliceBarProps) {
  const total = device.vram_total_bytes || 1;
  const slices = (device.slices ?? []).filter((s) => s.status !== 'released');
  const palette = colors ?? sliceColorMap(slices);
  const reserved = slices.reduce((sum, s) => sum + s.vram_bytes, 0);
  const used = device.vram_used_bytes ?? device.live?.memory_used_bytes ?? null;
  const outside = outsideBytes(device, reserved);
  const systemShare = device.is_unified_memory ? 0.25 : 0;
  return (
    <div className={cn('space-y-1.5', className)}>
      <div
        role="meter"
        aria-label={`VRAM: ${formatBytes(reserved)} reserved by ${slices.length} slice(s)${outside.bytes > 0 ? `, ${formatBytes(outside.bytes)} used outside GPU Deck` : ''} of ${formatBytes(total)}`}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round((reserved / total) * 100)}
        className={cn('relative flex gap-[2px] overflow-hidden rounded-[5px] bg-muted', heightClass)}
      >
        {slices.map((s) => (
          <div
            key={s.id}
            title={`${s.name ?? 'slice'} · ${formatBytes(s.vram_bytes)} · ${s.compute_pct}% compute${s.status === 'reserved' ? ' (loading)' : ''}`}
            className={cn('h-full shrink-0 transition-[width] duration-500', s.status === 'reserved' && 'animate-pulse')}
            style={{ width: `${Math.max(0.8, (s.vram_bytes / total) * 100)}%`, backgroundColor: palette.get(s.deployment_id ?? s.id) ?? OTHER }}
          />
        ))}
        {outside.bytes > 0 && (
          <div
            title={`Used outside GPU Deck · ${formatBytes(outside.bytes)}${outside.estimated ? ' (in use beyond GPU Deck reservations)' : ' (processes GPU Deck does not run)'}`}
            className="h-full shrink-0 transition-[width] duration-500"
            style={{ width: `${Math.max(0.8, (outside.bytes / total) * 100)}%`, backgroundImage: OUTSIDE_PATTERN }}
          />
        )}
        {systemShare > 0 && (
          <div
            aria-hidden
            title="Kept for macOS (unified memory)"
            className="absolute inset-y-0 right-0 bg-[repeating-linear-gradient(135deg,rgb(var(--foreground)/0.10)_0_3px,transparent_3px_6px)]"
            style={{ width: `${systemShare * 100}%` }}
          />
        )}
        {showUsage && used !== null && (
          <div aria-hidden title={`Driver reports ${formatBytes(used)} in use`} className="absolute inset-y-0 w-[2px] bg-foreground/80" style={{ left: `calc(${Math.min(100, (used / total) * 100)}% - 1px)` }} />
        )}
      </div>
      {showUsage && (
        <div className="flex items-center justify-between gap-3 text-[11px] tabular-nums text-muted-foreground">
          <span>
            <span className="font-medium text-foreground">{formatBytes(reserved)}</span> reserved · {slices.length} slice{slices.length === 1 ? '' : 's'}
            {outside.bytes > 0 && (
              <>
                {' · '}
                <span className="font-medium text-foreground">{formatBytes(outside.bytes)}</span> used outside GPU Deck
              </>
            )}
          </span>
          <span>
            {used !== null ? `${formatBytes(used)} in use · ` : ''}
            {formatBytes(total)}
          </span>
        </div>
      )}
    </div>
  );
}

/** Colour key for the slices drawn by `SliceBar` (plus the "used outside GPU Deck" segment). */
export function SliceLegend({ slices, colors, outside = 0, className }: { slices: DeviceSlice[]; colors: Map<string, string>; outside?: number; className?: string }) {
  const seen = new Map<string, DeviceSlice>();
  for (const s of slices) if (!seen.has(s.deployment_id ?? s.id)) seen.set(s.deployment_id ?? s.id, s);
  if (seen.size === 0 && outside <= 0) return null;
  return (
    <ul className={cn('flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-muted-foreground', className)}>
      {[...seen.entries()].map(([key, s]) => (
        <li key={key} className="inline-flex items-center gap-1.5">
          <span aria-hidden className="size-2 rounded-[2px]" style={{ backgroundColor: colors.get(key) }} />
          <span className="text-foreground">{s.name ?? 'slice'}</span>
        </li>
      ))}
      {outside > 0 && (
        <li className="inline-flex items-center gap-1.5">
          <span aria-hidden className="size-2 rounded-[2px]" style={{ backgroundImage: OUTSIDE_PATTERN }} />
          <span className="text-foreground">Used outside GPU Deck</span>
          <span className="tabular-nums">{formatBytes(outside)}</span>
        </li>
      )}
    </ul>
  );
}
