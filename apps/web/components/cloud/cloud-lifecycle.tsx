'use client';

import { Check, Loader2, X } from 'lucide-react';
import { cn } from '@/lib/utils';
import type { CloudNode } from '@/types/cloud';

type StepState = 'completed' | 'running' | 'failed' | 'pending';

const NODE: Record<StepState, string> = {
  completed: 'border-success/50 bg-success/15 text-success-dark dark:text-success-light',
  running: 'border-info/60 bg-info/15 text-info-dark dark:text-info-light',
  failed: 'border-error/60 bg-error/15 text-error-dark dark:text-error-light',
  pending: 'border-border bg-card text-muted-foreground',
};

const time = (at: string | null | undefined) => (at ? new Date(at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '');

interface Step {
  label: string;
  detail: string;
  state: StepState;
}

/** Requested → VM created → cloud-init → agent joined, then the stop / terminate branch. */
export function cloudSteps(vm: CloudNode): Step[] {
  const failed = vm.status === 'error';
  const created: StepState = vm.launched_at ? 'completed' : failed ? 'failed' : vm.status === 'pending' || vm.status === 'provisioning' ? 'running' : 'pending';
  const init: StepState = vm.joined_at ? 'completed' : vm.status === 'booting' ? 'running' : failed && vm.launched_at ? 'failed' : 'pending';
  const steps: Step[] = [
    { label: 'Requested', detail: time(vm.created_at), state: 'completed' },
    { label: 'VM created', detail: vm.launched_at ? time(vm.launched_at) : created === 'failed' ? 'provider refused' : 'create call', state: created },
    { label: 'cloud-init', detail: vm.joined_at ? 'agent installed' : init === 'running' ? 'installing agent' : 'download + join', state: init },
    { label: 'Agent joined', detail: vm.joined_at ? time(vm.joined_at) : 'mTLS to the hub', state: vm.joined_at ? 'completed' : 'pending' },
  ];
  if (['draining', 'stopping', 'stopped', 'starting'].includes(vm.status)) {
    steps.push({ label: vm.status === 'starting' ? 'Starting' : vm.status === 'stopped' ? 'Stopped' : vm.status === 'draining' ? 'Draining' : 'Stopping', detail: vm.stopped_at ? time(vm.stopped_at) : vm.status === 'draining' ? 'moving instances' : '', state: vm.status === 'stopped' ? 'completed' : 'running' });
  }
  if (['terminating', 'terminated'].includes(vm.status) || (vm.status === 'draining' && vm.pending_action === 'terminate')) {
    steps.push({ label: vm.status === 'terminated' ? 'Terminated' : 'Terminating', detail: vm.terminated_at ? time(vm.terminated_at) : 'deleting gpu-deck:* resources', state: vm.status === 'terminated' ? 'completed' : 'running' });
  }
  return steps;
}

function Icon({ state, index }: { state: StepState; index: number }) {
  if (state === 'completed') return <Check className="size-3.5" />;
  if (state === 'running') return <Loader2 className="size-3.5 animate-spin" />;
  if (state === 'failed') return <X className="size-3.5" />;
  return <span className="font-mono text-[10px] font-semibold">{index + 1}</span>;
}

export function CloudLifecycle({ vm, className }: { vm: CloudNode; className?: string }) {
  const steps = cloudSteps(vm);
  return (
    <ol className={cn('flex w-full items-start', className)} aria-label="VM lifecycle">
      {steps.map((step, i) => (
        <li key={step.label} className="flex min-w-0 flex-1 flex-col items-center text-center" aria-current={step.state === 'running' ? 'step' : undefined}>
          <div className="flex w-full items-center">
            <span className={cn('h-px flex-1', i === 0 ? 'bg-transparent' : steps[i - 1].state === 'completed' ? 'bg-success/50' : 'bg-border')} />
            <span className={cn('relative flex size-8 shrink-0 items-center justify-center rounded-full border', NODE[step.state])}>
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
