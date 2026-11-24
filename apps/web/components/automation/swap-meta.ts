/**
 * Swap operation vocabulary: step labels (contract steps plan → route_off →
 * drain → unload → load → health → route_on, plus the aliases agents and
 * older records use), kinds, triggers, state tones and timing helpers.
 */

import { ArrowDownToLine, ArrowLeftRight, ArrowUpFromLine, LayoutTemplate, MoveRight, RotateCw, TrendingDown, TrendingUp } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import type { SwapKind, SwapOperation, SwapState, SwapStep, SwapTrigger } from '@/hooks/api/automation-api';
import type { BadgeVariant } from '@/components/shared/StatusBadge';

export const STEP_LABELS: Record<string, { label: string; hint: string }> = {
  plan: { label: 'Plan', hint: 'Pick GPUs, reserve VRAM, write the rollback' },
  reserve_slice: { label: 'Reserve', hint: 'Reserve the VRAM slice' },
  prefetch: { label: 'Prefetch', hint: 'Fetch model weights to the node' },
  route_off: { label: 'Stop routing', hint: 'Remove from the API layer routing table' },
  drain: { label: 'Drain', hint: 'Wait for running and waiting requests to reach 0' },
  unload: { label: 'Unload', hint: 'Stop the engine and free VRAM' },
  release_slice: { label: 'Release', hint: 'Release the VRAM slice' },
  load: { label: 'Load', hint: 'Start the engine and load weights' },
  health: { label: 'Health', hint: 'Agent reports healthy, then a probe through the proxy' },
  health_check: { label: 'Health', hint: 'Agent reports healthy, then a probe through the proxy' },
  route_on: { label: 'Route traffic', hint: 'Add to the routing table with its weight' },
  route: { label: 'Route traffic', hint: 'Add to the routing table with its weight' },
};

export function stepLabel(name: string): string {
  return STEP_LABELS[name]?.label ?? name.replace(/_/g, ' ');
}

export const KIND_META: Record<SwapKind, { label: string; icon: LucideIcon }> = {
  load: { label: 'Load', icon: ArrowUpFromLine },
  unload: { label: 'Unload', icon: ArrowDownToLine },
  swap: { label: 'Swap', icon: ArrowLeftRight },
  scale_up: { label: 'Scale up', icon: TrendingUp },
  scale_down: { label: 'Scale down', icon: TrendingDown },
  restart: { label: 'Restart', icon: RotateCw },
  move: { label: 'Move', icon: MoveRight },
  apply_layout: { label: 'Apply layout', icon: LayoutTemplate },
};

export const TRIGGER_LABELS: Record<SwapTrigger, string> = {
  manual: 'Manual',
  rule: 'Rule',
  layout: 'Layout',
  auto_heal: 'Auto heal',
  api: 'API',
  benchmark: 'Benchmark',
};

export const ACTIVE_STATES: SwapState[] = ['pending', 'draining', 'unloading', 'loading', 'health_check', 'rolling_back'];

export function isActive(state: SwapState): boolean {
  return ACTIVE_STATES.includes(state);
}

export const STATE_META: Record<SwapState, { label: string; badge: BadgeVariant }> = {
  pending: { label: 'Pending', badge: 'pending' },
  draining: { label: 'Draining', badge: 'draining' },
  unloading: { label: 'Unloading', badge: 'unloading' },
  loading: { label: 'Loading', badge: 'loading' },
  health_check: { label: 'Health check', badge: 'info' },
  completed: { label: 'Completed', badge: 'completed' },
  failed: { label: 'Failed', badge: 'failed' },
  rolling_back: { label: 'Rolling back', badge: 'warning' },
  rolled_back: { label: 'Rolled back', badge: 'rolled_back' },
  cancelled: { label: 'Cancelled', badge: 'default' },
};

export function stepDurationMs(step: SwapStep, now: number): number | null {
  if (!step.startedAt) return null;
  const end = step.finishedAt ? Date.parse(step.finishedAt) : now;
  return Math.max(0, end - Date.parse(step.startedAt));
}

export function swapDurationMs(swap: SwapOperation, now: number): number | null {
  if (!swap.startedAt) return null;
  const end = swap.finishedAt ? Date.parse(swap.finishedAt) : now;
  return Math.max(0, end - Date.parse(swap.startedAt));
}

/** "850 ms", "12.4 s", "2 min 05 s". */
export function formatMs(ms: number | null): string {
  if (ms === null) return '—';
  if (ms < 1000) return `${Math.round(ms)} ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(ms < 10_000 ? 1 : 0)} s`;
  const m = Math.floor(ms / 60_000);
  const s = Math.round((ms % 60_000) / 1000);
  return `${m} min ${String(s).padStart(2, '0')} s`;
}

/** Live drain detail: "3 → 0 in flight". */
export function drainDetail(step: SwapStep): string | null {
  if (step.name !== 'drain' || !step.details) return null;
  const d = step.details as { in_flight?: number; in_flight_start?: number; waiting?: number };
  if (typeof d.in_flight !== 'number') return null;
  const start = typeof d.in_flight_start === 'number' ? `${d.in_flight_start} → ` : '';
  return `${start}${d.in_flight} in flight${typeof d.waiting === 'number' && d.waiting > 0 ? `, ${d.waiting} waiting` : ''}`;
}
