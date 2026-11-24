'use client';

import { Check, Loader2, Minus, X } from 'lucide-react';
import { cn } from '@/lib/utils';
import type { SwapStep } from '@/hooks/api/automation-api';
import { drainDetail, formatMs, STEP_LABELS, stepDurationMs, stepLabel } from '@/components/automation/swap-meta';

const NODE: Record<SwapStep['state'], string> = {
  completed: 'border-success/50 bg-success/15 text-success-dark dark:text-success-light',
  running: 'border-info/60 bg-info/15 text-info-dark dark:text-info-light',
  failed: 'border-error/60 bg-error/15 text-error-dark dark:text-error-light',
  skipped: 'border-border bg-muted text-muted-foreground/70',
  pending: 'border-border bg-card text-muted-foreground',
};

function StepIcon({ step, index }: { step: SwapStep; index: number }) {
  if (step.state === 'completed') return <Check className="size-3.5" />;
  if (step.state === 'running') return <Loader2 className="size-3.5 animate-spin" />;
  if (step.state === 'failed') return <X className="size-3.5" />;
  if (step.state === 'skipped') return <Minus className="size-3.5" />;
  return <span className="font-mono text-[10px] font-semibold">{index + 1}</span>;
}

interface SwapPipelineProps {
  steps: SwapStep[];
  now: number;
  size?: 'md' | 'lg';
  className?: string;
}

/**
 * The swap's steps as a connected pipeline: state node, step name, how long
 * it took (or has been running) and, for drain, the in-flight countdown.
 */
export function SwapPipeline({ steps, now, size = 'md', className }: SwapPipelineProps) {
  return (
    <ol className={cn('flex w-full items-start', className)} aria-label="Swap steps">
      {steps.map((step, i) => {
        const duration = stepDurationMs(step, now);
        const detail = drainDetail(step);
        const doneLink = step.state === 'completed';
        return (
          <li key={`${step.name}-${i}`} className="flex min-w-0 flex-1 flex-col items-center text-center" aria-current={step.state === 'running' ? 'step' : undefined}>
            <div className="flex w-full items-center">
              <span className={cn('h-px flex-1', i === 0 ? 'bg-transparent' : steps[i - 1].state === 'completed' ? 'bg-success/50' : 'bg-border')} />
              <span
                className={cn('relative flex shrink-0 items-center justify-center rounded-full border', size === 'lg' ? 'size-8' : 'size-6', NODE[step.state])}
                title={STEP_LABELS[step.name]?.hint ?? step.name}
              >
                {step.state === 'running' && <span className="absolute inset-0 animate-ping rounded-full bg-info/20" aria-hidden />}
                <StepIcon step={step} index={i} />
              </span>
              <span className={cn('h-px flex-1', i === steps.length - 1 ? 'bg-transparent' : doneLink ? 'bg-success/50' : 'bg-border')} />
            </div>
            <span className={cn('mt-1.5 max-w-full truncate px-1 font-medium', size === 'lg' ? 'text-xs' : 'text-[11px]', step.state === 'pending' || step.state === 'skipped' ? 'text-muted-foreground' : 'text-foreground')}>{stepLabel(step.name)}</span>
            <span className={cn('max-w-full truncate px-1 font-mono tabular-nums', size === 'lg' ? 'text-[11px]' : 'text-[10px]', step.state === 'failed' ? 'text-error-dark dark:text-error-light' : 'text-muted-foreground')}>
              {step.state === 'skipped' ? 'skipped' : duration !== null ? `${formatMs(duration)}${step.state === 'running' ? '…' : ''}` : '—'}
            </span>
            {detail && size === 'lg' && <span className="mt-0.5 max-w-full truncate px-1 text-[10px] text-info-dark dark:text-info-light">{detail}</span>}
          </li>
        );
      })}
    </ol>
  );
}

/** Tiny step strip for tables: one segment per step, coloured by state. */
export function SwapStepsMini({ steps }: { steps: SwapStep[] }) {
  const color: Record<SwapStep['state'], string> = {
    completed: 'bg-success/70',
    running: 'bg-info animate-pulse',
    failed: 'bg-error',
    skipped: 'bg-muted-foreground/25',
    pending: 'bg-muted',
  };
  return (
    <span className="flex items-center gap-0.5" aria-label={steps.map((s) => `${stepLabel(s.name)}: ${s.state}`).join(', ')}>
      {steps.map((s, i) => (
        <span key={`${s.name}-${i}`} title={`${stepLabel(s.name)} — ${s.state}`} className={cn('h-1.5 w-3.5 rounded-full', color[s.state])} />
      ))}
    </span>
  );
}

/** Where the time went: each step's share of the swap on one bar. */
export function SwapTimingBar({ steps, now }: { steps: SwapStep[]; now: number }) {
  const parts = steps.map((s) => ({ step: s, ms: stepDurationMs(s, now) ?? 0 })).filter((p) => p.ms > 0);
  const total = parts.reduce((sum, p) => sum + p.ms, 0);
  if (!total) return null;
  const tone: Record<SwapStep['state'], string> = {
    completed: 'bg-foreground/25',
    running: 'bg-info/60',
    failed: 'bg-error/70',
    skipped: 'bg-muted',
    pending: 'bg-muted',
  };
  return (
    <div className="space-y-1.5">
      <div className="flex h-6 overflow-hidden rounded-md border border-border">
        {parts.map(({ step, ms }, i) => (
          <div key={`${step.name}-${i}`} className={cn('flex h-full items-center justify-center overflow-hidden border-r border-background/60 px-1 text-[10px] font-medium text-foreground last:border-r-0', tone[step.state])} style={{ width: `${(ms / total) * 100}%` }} title={`${stepLabel(step.name)} · ${formatMs(ms)}`}>
            {ms / total > 0.09 && <span className="truncate">{stepLabel(step.name)}</span>}
          </div>
        ))}
      </div>
      <p className="text-[11px] text-muted-foreground">
        Longest step: <span className="font-medium text-foreground">{stepLabel(parts.reduce((a, b) => (b.ms > a.ms ? b : a)).step.name)}</span> ({formatMs(Math.max(...parts.map((p) => p.ms)))} of {formatMs(total)})
      </p>
    </div>
  );
}
