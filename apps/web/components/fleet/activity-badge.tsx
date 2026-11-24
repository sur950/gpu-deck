import { StatusBadge, type BadgeVariant } from '@/components/shared/StatusBadge';
import { formatBytes, formatRelativeTime } from '@/utils/format';

/** What a GPU / node really does, from live data (control plane `activity`). */
export type Activity = 'detached' | 'stale' | 'gpu_deck' | 'mixed' | 'external' | 'idle';

const VARIANT: Record<Activity, BadgeVariant> = {
  detached: 'error',
  stale: 'unknown',
  gpu_deck: 'running',
  mixed: 'running',
  external: 'info',
  idle: 'idle',
};

function plural(n: number, word: string): string {
  return `${n} ${word}${n === 1 ? '' : 'es'}`;
}

/** Memory in use beyond GPU Deck's reservations that still counts as idle (driver, display); same as the backend. */
export const IDLE_BASELINE_BYTES = 1024 ** 3;

export interface ActivityMemory {
  /** Driver-reported memory in use (latest fresh sample); null when unknown / stale. */
  usedBytes?: number | null;
  /** VRAM reserved by GPU Deck slices on this device (or node). */
  reservedBytes?: number | null;
  /** Unified memory (Apple): "used" includes the OS, so it never proves outside work. */
  unified?: boolean;
}

/**
 * The activity to show. The backend's value wins, except that a GPU never reads "Idle" while
 * the driver reports memory held beyond GPU Deck's reservations (e.g. an agent that sends an
 * empty per-process list): that is work outside GPU Deck.
 */
export function effectiveActivity(activity: string | null | undefined, memory: ActivityMemory = {}): Activity | null {
  const a = asActivity(activity);
  const { usedBytes, reservedBytes, unified } = memory;
  if ((a === 'idle' || a === null) && !unified && typeof usedBytes === 'number' && usedBytes > (reservedBytes ?? 0) + IDLE_BASELINE_BYTES) return 'external';
  return a;
}

/** Bytes held outside GPU Deck, for labels ("In use outside GPU Deck · 120 GiB"). */
export function outsideHeld(memory: ActivityMemory): number {
  if (typeof memory.usedBytes !== 'number') return 0;
  return Math.max(0, memory.usedBytes - (memory.reservedBytes ?? 0));
}

/**
 * Label for an activity: never "Idle" while processes outside GPU Deck run; red "Detached" when
 * the agent is gone (last known state may still be shown, marked stale).
 */
export function activityLabel(activity: Activity | null | undefined, outside = 0, lastSeenAt?: string | null, outsideBytes = 0): string {
  switch (activity) {
    case 'detached':
      return lastSeenAt ? `Detached · last seen ${formatRelativeTime(lastSeenAt)}` : 'Detached';
    case 'stale':
      return 'No fresh sample';
    case 'mixed':
      return outside ? `Running · ${plural(outside, 'external process')}` : 'Running · also used outside GPU Deck';
    case 'external':
      if (outside) return `Running (external) · ${plural(outside, 'process')}`;
      return outsideBytes >= IDLE_BASELINE_BYTES ? `Running (external) · ${formatBytes(outsideBytes)} held` : 'Running (external)';
    case 'gpu_deck':
      return 'Running';
    case 'idle':
      return 'Idle';
    default:
      return 'Unknown';
  }
}

/** Memory reading of a contract `Device` row (snake_case). */
export function deviceMemory(d: { vram_usage_stale?: boolean; vram_used_bytes?: number | null; live?: { memory_used_bytes?: number | null } | null; vram_reserved_bytes?: number; is_unified_memory?: boolean }): ActivityMemory {
  return { usedBytes: d.vram_usage_stale ? null : (d.vram_used_bytes ?? d.live?.memory_used_bytes ?? null), reservedBytes: d.vram_reserved_bytes ?? 0, unified: d.is_unified_memory };
}

/** A node's activity, upgraded from "idle" when one of its GPUs holds memory outside GPU Deck. */
export function nodeActivity(activity: string | null | undefined, devices: Parameters<typeof deviceMemory>[0][]): Activity | null {
  const a = asActivity(activity);
  if (a !== 'idle' && a !== null) return a;
  return devices.some((d) => effectiveActivity('idle', deviceMemory(d)) === 'external') ? 'external' : a;
}

export function asActivity(value: string | null | undefined): Activity | null {
  return value && value in VARIANT ? (value as Activity) : null;
}

export function activityVariant(activity: Activity): BadgeVariant {
  return VARIANT[activity];
}

interface ActivityBadgeProps extends ActivityMemory {
  activity: string | null | undefined;
  outside?: number;
  lastSeenAt?: string | null;
  className?: string;
}

export function ActivityBadge({ activity, outside = 0, lastSeenAt, className, ...memory }: ActivityBadgeProps) {
  const a = effectiveActivity(activity, memory);
  if (!a) return null;
  return (
    <StatusBadge variant={VARIANT[a]} className={className}>
      {activityLabel(a, outside, lastSeenAt, outsideHeld(memory))}
    </StatusBadge>
  );
}
