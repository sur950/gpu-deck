/**
 * Cloud VM labels, badge variants and price formatting.
 */

import type { BadgeVariant } from '@/components/shared/StatusBadge';
import type { CloudInstanceType, CloudNodeStatus } from '@/types/cloud';

export const CLOUD_BADGE: Record<CloudNodeStatus, BadgeVariant> = {
  pending: 'pending',
  provisioning: 'scheduled',
  booting: 'loading',
  running: 'running',
  draining: 'draining',
  stopping: 'unloading',
  stopped: 'stopped',
  starting: 'loading',
  terminating: 'unloading',
  terminated: 'inactive',
  error: 'failed',
};

/** "$1.86/h", "—" when the provider has no list price. */
export function formatHourly(usd: number | null | undefined): string {
  if (usd === null || usd === undefined) return '—';
  return usd >= 100 ? `$${usd.toFixed(0)}/h` : usd >= 10 ? `$${usd.toFixed(1)}/h` : `$${usd.toFixed(usd < 1 ? 3 : 2)}/h`;
}

/** List price per GPU-hour — the number to compare across instance shapes. */
export function perGpuHour(t: Pick<CloudInstanceType, 'price_per_hour_usd' | 'gpu_count'>): number | null {
  if (t.price_per_hour_usd === null || t.price_per_hour_usd === undefined || !t.gpu_count) return null;
  return t.price_per_hour_usd / t.gpu_count;
}

/** "8 × H100 · 80 GiB" */
export function gpuSummary(t: { gpu_count: number; gpu_model?: string | null; gpu_vram_bytes?: number | null }): string {
  if (!t.gpu_count) return 'No GPU';
  const vram = t.gpu_vram_bytes ? ` · ${Math.round(t.gpu_vram_bytes / 1024 ** 3)} GiB` : '';
  return `${t.gpu_count} × ${t.gpu_model ?? 'GPU'}${vram}`;
}
