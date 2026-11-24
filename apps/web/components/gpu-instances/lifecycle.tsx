'use client';

import { Check, Loader2, X } from 'lucide-react';
import { cn } from '@/lib/utils';
import { formatDateTime } from '@/utils/format';
import type { GpuInstance } from '@/types/gpu-instances';

type StepState = 'completed' | 'running' | 'failed' | 'pending';

const NODE: Record<StepState, string> = {
  completed: 'border-success/50 bg-success/15 text-success-dark dark:text-success-light',
  running: 'border-info/60 bg-info/15 text-info-dark dark:text-info-light',
  failed: 'border-error/60 bg-error/15 text-error-dark dark:text-error-light',
  pending: 'border-border bg-card text-muted-foreground',
};

interface Step {
  label: string;
  detail: string;
  state: StepState;
  at?: string | null;
}

function time(at: string | null | undefined): string {
  return at ? new Date(at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '';
}

/** requested → placed → container started → sshd ready (→ stopped), from the instance timestamps. */
export function lifecycleSteps(i: GpuInstance): Step[] {
  const placed = !!i.node_id;
  const started = !!i.started_at || i.state === 'running' || i.state === 'stopping' || (i.state === 'stopped' && !!i.running_at);
  const ready = !!i.running_at;
  const failedAt = i.state === 'error' ? (placed ? (ready ? 3 : 2) : 1) : -1;

  const at = (index: number, done: boolean): StepState => {
    if (failedAt === index) return 'failed';
    if (done) return 'completed';
    const current = { requested: 0, placing: 1, starting: 2, running: 3, stopping: 4, stopped: 4, error: -1 }[i.state];
    return current === index ? 'running' : 'pending';
  };

  const steps: Step[] = [
    { label: 'Requested', detail: time(i.created_at), at: i.created_at, state: 'completed' },
    { label: 'Placed', detail: placed ? (i.node_name ?? 'node') : i.state === 'error' ? 'no capacity' : 'finding a GPU', state: at(1, placed) },
    { label: 'Container up', detail: i.started_at ? time(i.started_at) : 'image pull + start', at: i.started_at, state: at(2, started && (ready || i.state !== 'starting')) },
    { label: 'sshd ready', detail: ready ? time(i.running_at) : 'host port + host key', at: i.running_at, state: at(3, ready) },
  ];
  if (i.state === 'stopping' || i.state === 'stopped') {
    steps.push({ label: i.state === 'stopping' ? 'Stopping' : 'Stopped', detail: i.stopped_at ? time(i.stopped_at) : 'closing sessions', at: i.stopped_at, state: i.state === 'stopping' ? 'running' : 'completed' });
  }
  return steps;
}

function Icon({ state, index }: { state: StepState; index: number }) {
  if (state === 'completed') return <Check className="size-3.5" />;
  if (state === 'running') return <Loader2 className="size-3.5 animate-spin" />;
  if (state === 'failed') return <X className="size-3.5" />;
  return <span className="font-mono text-[10px] font-semibold">{index + 1}</span>;
}

export function InstanceLifecycle({ instance, className }: { instance: GpuInstance; className?: string }) {
  const steps = lifecycleSteps(instance);
  return (
    <ol className={cn('flex w-full items-start', className)} aria-label="Instance lifecycle">
      {steps.map((step, i) => (
        <li key={step.label} className="flex min-w-0 flex-1 flex-col items-center text-center" aria-current={step.state === 'running' ? 'step' : undefined}>
          <div className="flex w-full items-center">
            <span className={cn('h-px flex-1', i === 0 ? 'bg-transparent' : steps[i - 1].state === 'completed' ? 'bg-success/50' : 'bg-border')} />
            <span className={cn('relative flex size-8 shrink-0 items-center justify-center rounded-full border', NODE[step.state])} title={step.at ? formatDateTime(step.at) : undefined}>
              {step.state === 'running' && <span className="absolute inset-0 animate-ping rounded-full bg-info/20" aria-hidden />}
              <Icon state={step.state} index={i} />
            </span>
            <span className={cn('h-px flex-1', i === steps.length - 1 ? 'bg-transparent' : step.state === 'completed' ? 'bg-success/50' : 'bg-border')} />
          </div>
          <span className={cn('mt-1.5 max-w-full truncate px-1 text-xs font-medium', step.state === 'pending' ? 'text-muted-foreground' : 'text-foreground')}>{step.label}</span>
          <span className={cn('max-w-full truncate px-1 font-mono text-[11px] tabular-nums', step.state === 'failed' ? 'text-error-dark dark:text-error-light' : 'text-muted-foreground')}>{step.detail}</span>
        </li>
      ))}
    </ol>
  );
}
