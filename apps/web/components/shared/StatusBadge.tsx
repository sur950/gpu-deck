/**
 * Reusable Status Badge component
 * Color-coded badges for different statuses — built only from theme tokens.
 *
 * Generic tones (success/warning/error/info/default) plus named states for
 * GPU Deck entities: nodes (online/offline/draining), model instances
 * (loading/running/unloading/stopped/failed/evicted), rules and swaps
 * (pending/scheduled/paused/dry_run) and health (healthy/degraded/down).
 */

import { cn } from '@/lib/utils';

export type BadgeVariant =
  // tones
  | 'success'
  | 'error'
  | 'warning'
  | 'info'
  | 'default'
  // generic states
  | 'active'
  | 'inactive'
  | 'expired'
  | 'revoked'
  | 'suspended'
  | 'unknown'
  // health
  | 'healthy'
  | 'degraded'
  | 'down'
  // nodes / GPUs
  | 'online'
  | 'offline'
  | 'draining'
  | 'idle'
  // model instances / deployments
  | 'pending'
  | 'scheduled'
  | 'loading'
  | 'running'
  | 'unloading'
  | 'stopped'
  | 'failed'
  | 'evicted'
  // rules / swaps / benchmarks
  | 'paused'
  | 'dry_run'
  | 'rolled_back'
  | 'completed';

interface StatusBadgeProps {
  variant: BadgeVariant;
  children: React.ReactNode;
  className?: string;
}

const SUCCESS = 'bg-success-light/10 text-success-dark border-success-light dark:bg-success-dark/40 dark:text-success-light dark:border-success-dark/40';
const WARNING = 'bg-warning-light/10 text-warning-dark border-warning-light dark:bg-warning-dark/40 dark:text-warning-light dark:border-warning-dark/40';
const ERROR   = 'bg-error-light/10 text-error-dark border-error-light dark:bg-error-dark/40 dark:text-error-light dark:border-error-dark/40';
const INFO    = 'bg-info-light/10 text-info-dark border-info-light dark:bg-info-dark/40 dark:text-info-light dark:border-info-dark/40';
const NEUTRAL = 'bg-muted text-muted-foreground border-border';

const variantStyles: Record<BadgeVariant, string> = {
  success: SUCCESS,
  error: ERROR,
  warning: WARNING,
  info: INFO,
  default: NEUTRAL,
  active: SUCCESS,
  inactive: NEUTRAL,
  expired: NEUTRAL,
  revoked: ERROR,
  suspended: WARNING,
  unknown: NEUTRAL,
  healthy: SUCCESS,
  degraded: WARNING,
  down: ERROR,
  online: SUCCESS,
  offline: ERROR,
  draining: WARNING,
  idle: NEUTRAL,
  pending: NEUTRAL,
  scheduled: INFO,
  loading: INFO,
  running: SUCCESS,
  unloading: WARNING,
  stopped: NEUTRAL,
  failed: ERROR,
  evicted: WARNING,
  paused: NEUTRAL,
  dry_run: INFO,
  rolled_back: WARNING,
  completed: SUCCESS,
};

/** Map an arbitrary backend status string to a badge variant (unknown → 'unknown'). */
export function toBadgeVariant(status: string | null | undefined): BadgeVariant {
  if (!status) return 'unknown';
  const key = status.toLowerCase().replace(/[\s-]+/g, '_');
  return key in variantStyles ? (key as BadgeVariant) : 'unknown';
}

export function StatusBadge({ variant, children, className }: StatusBadgeProps) {
  return (
    <span
      className={cn(
        'inline-flex items-center px-2 py-1 rounded-md border text-xs font-medium',
        variantStyles[variant],
        className
      )}
    >
      {children}
    </span>
  );
}
