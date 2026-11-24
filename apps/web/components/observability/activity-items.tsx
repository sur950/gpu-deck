'use client';

import {
  AlertOctagon,
  AlertTriangle,
  ArrowRightLeft,
  CircleDashed,
  FlaskConical,
  Info,
  PauseCircle,
  RotateCcw,
  SkipForward,
  Zap,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { cn } from '@/lib/utils';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import type { BadgeVariant } from '@/components/shared/StatusBadge';
import type { AlertSeverity, AlertStatus, RuleRunOutcome, SwapState, SwapStep } from '@/types/overview';
import { formatMs } from '@/utils/metrics-format';

// ─── Rule run outcomes ────────────────────────────────────────────────────────

const OUTCOME_ICON: Record<RuleRunOutcome, { icon: LucideIcon; className: string }> = {
  fired: { icon: Zap, className: 'text-brand border-brand/25 bg-brand/10' },
  dry_run: { icon: FlaskConical, className: 'text-info-dark dark:text-info-light border-info/25 bg-info/10' },
  cooldown: { icon: PauseCircle, className: 'text-muted-foreground border-border bg-muted/60' },
  no_match: { icon: CircleDashed, className: 'text-muted-foreground border-border bg-muted/60' },
  skipped: { icon: SkipForward, className: 'text-muted-foreground border-border bg-muted/60' },
  error: { icon: AlertOctagon, className: 'text-error-dark dark:text-error-light border-error/25 bg-error/10' },
};

export const OUTCOME_BADGE: Record<RuleRunOutcome, BadgeVariant> = {
  fired: 'active',
  dry_run: 'dry_run',
  cooldown: 'paused',
  no_match: 'default',
  skipped: 'inactive',
  error: 'failed',
};

/** Square icon tile for a rule outcome (icon + colour; the label is always next to it). */
export function OutcomeMark({ outcome }: { outcome: RuleRunOutcome }) {
  const cfg = OUTCOME_ICON[outcome];
  const Icon = cfg.icon;
  return (
    <span className={cn('flex size-7 shrink-0 items-center justify-center rounded-md border', cfg.className)}>
      <Icon className="size-3.5" aria-hidden />
    </span>
  );
}

// ─── Swaps ────────────────────────────────────────────────────────────────────

const TERMINAL: SwapState[] = ['completed', 'failed', 'rolled_back', 'cancelled'];

export function isSwapActive(state: SwapState): boolean {
  return !TERMINAL.includes(state);
}

export const SWAP_BADGE: Record<SwapState, BadgeVariant> = {
  pending: 'pending',
  draining: 'draining',
  unloading: 'unloading',
  loading: 'loading',
  health_check: 'loading',
  completed: 'completed',
  failed: 'failed',
  rolling_back: 'warning',
  rolled_back: 'rolled_back',
  cancelled: 'inactive',
};

/** Square icon tile for a swap: rollback / failure / in-flight / done. */
export function SwapMark({ state }: { state: SwapState }) {
  const failed = state === 'failed';
  const rolled = state === 'rolled_back' || state === 'rolling_back';
  const Icon = failed ? AlertOctagon : rolled ? RotateCcw : ArrowRightLeft;
  return (
    <span
      className={cn(
        'flex size-7 shrink-0 items-center justify-center rounded-md border',
        failed
          ? 'border-error/25 bg-error/10 text-error-dark dark:text-error-light'
          : rolled
            ? 'border-warning/25 bg-warning/10 text-warning-dark dark:text-warning-light'
            : isSwapActive(state)
              ? 'border-info/25 bg-info/10 text-info-dark dark:text-info-light'
              : 'border-border bg-muted/60 text-muted-foreground',
      )}
    >
      <Icon className="size-3.5" aria-hidden />
    </span>
  );
}

const STEP_LABELS: Record<string, string> = {
  drain: 'Drain',
  wait: 'Wait for idle',
  unload: 'Unload',
  load: 'Load',
  health_check: 'Health check',
  route: 'Re-enable routing',
  reserve_slice: 'Reserve slice',
  release_slice: 'Release slice',
  prefetch: 'Prefetch weights',
};

export function stepLabel(name: string): string {
  return STEP_LABELS[name] ?? name.replace(/_/g, ' ').replace(/^\w/, (c) => c.toUpperCase());
}

function stepDuration(step: SwapStep): number | null {
  if (!step.startedAt) return null;
  const end = step.finishedAt ? new Date(step.finishedAt).getTime() : Date.now();
  return end - new Date(step.startedAt).getTime();
}

const STEP_CLASS: Record<SwapStep['state'], string> = {
  completed: 'bg-success',
  running: 'bg-info animate-pulse-subtle',
  failed: 'bg-error',
  skipped: 'bg-muted-foreground/25',
  pending: 'bg-muted',
};

/**
 * One segment per swap step (drain → wait → unload → load → health …),
 * coloured by state. Each segment has a tooltip with the step name and its
 * duration, and the whole track has a text summary for assistive tech.
 */
export function SwapStepTrack({ steps, className }: { steps: SwapStep[]; className?: string }) {
  const done = steps.filter((s) => s.state === 'completed').length;
  const failed = steps.find((s) => s.state === 'failed');
  const summary = failed ? `Failed at ${stepLabel(failed.name)}` : `${done} of ${steps.length} steps done`;
  return (
    <TooltipProvider>
      <div className={cn('flex items-center gap-[3px]', className)} role="img" aria-label={summary}>
        {steps.map((step, i) => {
          const ms = stepDuration(step);
          return (
            <Tooltip key={`${step.name}-${i}`}>
              <TooltipTrigger render={<span className={cn('h-1.5 min-w-3 flex-1 rounded-full', STEP_CLASS[step.state] ?? 'bg-muted')} />} />
              <TooltipContent side="top" className="text-xs">
                <p className="font-semibold">{stepLabel(step.name)}</p>
                <p className="text-muted-foreground">
                  {step.state}
                  {ms !== null && ` · ${formatMs(ms)}`}
                </p>
                {step.error && <p className="max-w-64 text-error-light">{step.error}</p>}
              </TooltipContent>
            </Tooltip>
          );
        })}
      </div>
    </TooltipProvider>
  );
}

// ─── Alerts ───────────────────────────────────────────────────────────────────

const SEVERITY: Record<AlertSeverity, { icon: LucideIcon; label: string; className: string }> = {
  critical: { icon: AlertOctagon, label: 'Critical', className: 'border-error/25 bg-error/10 text-error-dark dark:text-error-light' },
  warning: { icon: AlertTriangle, label: 'Warning', className: 'border-warning/25 bg-warning/10 text-warning-dark dark:text-warning-light' },
  info: { icon: Info, label: 'Info', className: 'border-info/25 bg-info/10 text-info-dark dark:text-info-light' },
};

export function SeverityMark({ severity }: { severity: AlertSeverity }) {
  const cfg = SEVERITY[severity];
  const Icon = cfg.icon;
  return (
    <span className={cn('flex size-7 shrink-0 items-center justify-center rounded-md border', cfg.className)} title={cfg.label}>
      <Icon className="size-3.5" aria-label={cfg.label} />
    </span>
  );
}

export function severityLabel(severity: AlertSeverity): string {
  return SEVERITY[severity].label;
}

export const ALERT_STATUS_BADGE: Record<AlertStatus, BadgeVariant> = {
  open: 'warning',
  acknowledged: 'info',
  resolved: 'completed',
};
