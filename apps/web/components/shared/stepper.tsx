'use client';

import { Check } from 'lucide-react';
import { cn } from '@/lib/utils';

export interface StepDefinition {
  id: string;
  title: string;
  description?: string;
}

interface StepperProps {
  steps: StepDefinition[];
  current: number;
  /** Highest step the user has reached — earlier steps are clickable. */
  reached?: number;
  onSelect?: (index: number) => void;
  className?: string;
}

/**
 * Horizontal wizard progress (numbered circles joined by hairlines). Completed
 * steps show a check and can be revisited; the active step carries the brand
 * accent like the sidebar marker.
 */
export function Stepper({ steps, current, reached = current, onSelect, className }: StepperProps) {
  return (
    <ol className={cn('flex w-full items-start gap-2 overflow-x-auto', className)} aria-label="Progress">
      {steps.map((step, index) => {
        const done = index < current;
        const active = index === current;
        const clickable = !!onSelect && index <= reached && !active;
        return (
          <li key={step.id} className={cn('flex min-w-0 items-start gap-2', index < steps.length - 1 && 'flex-1')}>
            <button
              type="button"
              disabled={!clickable}
              onClick={() => clickable && onSelect?.(index)}
              aria-current={active ? 'step' : undefined}
              className={cn(
                'group flex shrink-0 items-start gap-2.5 rounded-md text-left outline-none focus-visible:ring-2 focus-visible:ring-ring/30',
                clickable ? 'cursor-pointer' : 'cursor-default',
              )}
            >
              <span
                className={cn(
                  'flex size-7 shrink-0 items-center justify-center rounded-full border text-[11px] font-semibold tabular-nums transition-colors',
                  done && 'border-foreground bg-foreground text-background',
                  active && 'border-brand bg-brand/10 text-foreground ring-2 ring-brand/20',
                  !done && !active && 'border-border bg-card text-muted-foreground',
                )}
              >
                {done ? <Check className="size-3.5" /> : index + 1}
              </span>
              <span className="min-w-0 pt-1">
                <span
                  className={cn(
                    'block text-[13px] font-medium whitespace-nowrap',
                    active ? 'text-foreground' : done ? 'text-foreground/80' : 'text-muted-foreground',
                  )}
                >
                  {step.title}
                </span>
                {step.description && (
                  <span className="hidden text-[11px] whitespace-nowrap text-muted-foreground xl:block">{step.description}</span>
                )}
              </span>
            </button>
            {index < steps.length - 1 && (
              <span
                aria-hidden
                className={cn('mt-3.5 h-px min-w-4 flex-1 rounded-full', index < current ? 'bg-foreground/60' : 'bg-border')}
              />
            )}
          </li>
        );
      })}
    </ol>
  );
}
