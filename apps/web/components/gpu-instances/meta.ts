/**
 * Labels, badge variants and time formatting for SSH GPU instances.
 */

import type { BadgeVariant } from '@/components/shared/StatusBadge';
import type { GpuInstance, GpuInstanceState } from '@/types/gpu-instances';
import { formatBytes } from '@/utils/format';

export const GPU_INSTANCE_BADGE: Record<GpuInstanceState, BadgeVariant> = {
  requested: 'pending',
  placing: 'scheduled',
  starting: 'loading',
  running: 'running',
  stopping: 'unloading',
  stopped: 'stopped',
  error: 'failed',
};

export const TTL_OPTIONS: { value: number; label: string }[] = [
  { value: 3600, label: '1 hour' },
  { value: 4 * 3600, label: '4 hours' },
  { value: 8 * 3600, label: '8 hours (default)' },
  { value: 24 * 3600, label: '1 day' },
  { value: 3 * 86_400, label: '3 days' },
  { value: 7 * 86_400, label: '7 days (max)' },
  { value: 0, label: 'No TTL — runs until stopped' },
];

export const IDLE_OPTIONS: { value: number; label: string }[] = [
  { value: 0, label: 'Never' },
  { value: 30 * 60, label: '30 min without an SSH session' },
  { value: 3600, label: '1 hour without an SSH session' },
  { value: 4 * 3600, label: '4 hours without an SSH session' },
];

/** "45 min", "8 h", "1 h 30 min", "3 days". */
export function formatSpan(seconds: number | null | undefined): string {
  if (!seconds || seconds <= 0) return '—';
  const m = Math.round(seconds / 60);
  if (m < 60) return `${m} min`;
  const h = Math.floor(m / 60);
  const rest = m % 60;
  if (h < 48) return rest ? `${h} h ${rest} min` : `${h} h`;
  const d = Math.floor(h / 24);
  const hr = h % 24;
  return hr ? `${d} days ${hr} h` : `${d} days`;
}

/** Time left on a TTL, coarse ("5 h 42 min", "38 min", "40 s"), or null without one. */
export function ttlLeft(expiresAt: string | null | undefined, now: number): { ms: number; label: string } | null {
  if (!expiresAt) return null;
  const ms = Date.parse(expiresAt) - now;
  if (ms <= 0) return { ms, label: 'expired' };
  if (ms < 60_000) return { ms, label: `${Math.ceil(ms / 1000)} s` };
  return { ms, label: formatSpan(ms / 1000) };
}

/** What the instance gets: "1 × GPU", "16 GiB slice · 50 %", "CPU only". */
export function gpuShape(i: Pick<GpuInstance, 'gpu_count' | 'slice_vram_bytes' | 'slice_compute_pct'>): string {
  if (i.gpu_count === 0) return 'CPU only';
  if (i.slice_vram_bytes) return `${formatBytes(i.slice_vram_bytes)} slice${i.slice_compute_pct && i.slice_compute_pct < 100 ? ` · ${i.slice_compute_pct}%` : ''}`;
  return `${i.gpu_count} × GPU`;
}

export function gpuHours(seconds: number): string {
  const h = seconds / 3600;
  return h >= 100 ? h.toFixed(0) : h.toFixed(1);
}

/**
 * TTL in seconds that pushes the current expiry out by `hours` — the API
 * counts `ttl_seconds` from now, so this adds to what is left right now.
 * Call from event handlers only (reads the clock).
 */
export function extendedTtl(expiresAt: string | null | undefined, hours: number): number {
  const left = expiresAt ? Math.max(0, Math.round((Date.parse(expiresAt) - Date.now()) / 1000)) : 0;
  return left + hours * 3600;
}
