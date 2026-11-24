'use client';

import { useRef } from 'react';
import type { LucideIcon } from 'lucide-react';
import { cn } from '@/lib/utils';

export interface SegmentOption<T extends string> {
  value: T;
  label: string;
  icon?: LucideIcon;
  /** Muted second line (block layout only). */
  hint?: string;
  disabled?: boolean;
}

interface SegmentedProps<T extends string> {
  value: T;
  onChange: (value: T) => void;
  options: SegmentOption<T>[];
  /** `inline` = pill track; `block` = equal-width tiles with hints. */
  variant?: 'inline' | 'block';
  size?: 'sm' | 'md';
  className?: string;
  'aria-label'?: string;
  id?: string;
}

/**
 * Radio group rendered as a segmented track (arrow keys move the choice).
 * The selected segment is a raised card on the muted track — the same
 * language as the tabs, but for form values.
 */
export function Segmented<T extends string>({ value, onChange, options, variant = 'inline', size = 'md', className, id, ...rest }: SegmentedProps<T>) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);

  function move(from: number, delta: number) {
    const enabled = options.map((o, i) => (o.disabled ? -1 : i)).filter((i) => i >= 0);
    const pos = enabled.indexOf(from);
    const next = enabled[(pos + delta + enabled.length) % enabled.length];
    if (next === undefined) return;
    onChange(options[next].value);
    refs.current[next]?.focus();
  }

  return (
    <div
      id={id}
      role="radiogroup"
      aria-label={rest['aria-label']}
      className={cn(
        variant === 'inline' ? 'inline-flex rounded-md bg-muted p-[3px]' : 'grid gap-2',
        variant === 'block' && (options.length === 2 ? 'sm:grid-cols-2' : options.length === 3 ? 'sm:grid-cols-3' : 'sm:grid-cols-2 lg:grid-cols-4'),
        className,
      )}
    >
      {options.map((option, i) => {
        const selected = option.value === value;
        const Icon = option.icon;
        return (
          <button
            key={option.value}
            ref={(el) => {
              refs.current[i] = el;
            }}
            type="button"
            role="radio"
            aria-checked={selected}
            tabIndex={selected ? 0 : -1}
            disabled={option.disabled}
            onClick={() => onChange(option.value)}
            onKeyDown={(e) => {
              if (e.key === 'ArrowRight' || e.key === 'ArrowDown') {
                e.preventDefault();
                move(i, 1);
              } else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') {
                e.preventDefault();
                move(i, -1);
              }
            }}
            className={cn(
              'outline-none transition-all focus-visible:ring-2 focus-visible:ring-ring/30 disabled:cursor-not-allowed disabled:opacity-50',
              variant === 'inline' &&
                cn(
                  'inline-flex items-center gap-1.5 rounded-[5px] font-medium whitespace-nowrap',
                  size === 'sm' ? 'h-6 px-2 text-[11px]' : 'h-7 px-3 text-xs',
                  selected ? 'bg-card text-foreground shadow-subtle ring-1 ring-foreground/10' : 'text-muted-foreground hover:text-foreground',
                ),
              variant === 'block' &&
                cn(
                  'flex items-start gap-2.5 rounded-lg border px-3 py-2.5 text-left',
                  selected ? 'border-foreground/25 bg-card shadow-subtle ring-1 ring-foreground/10' : 'border-border bg-transparent hover:border-foreground/20 hover:bg-muted/40',
                ),
            )}
          >
            {variant === 'block' && (
              <span
                aria-hidden
                className={cn('mt-0.5 flex size-3.5 shrink-0 items-center justify-center rounded-full border', selected ? 'border-foreground' : 'border-muted-foreground/50')}
              >
                {selected && <span className="size-1.5 rounded-full bg-foreground" />}
              </span>
            )}
            {Icon && <Icon className={cn('shrink-0', variant === 'block' ? 'mt-px size-4 text-muted-foreground' : 'size-3.5')} />}
            {variant === 'block' ? (
              <span className="min-w-0">
                <span className="block text-xs font-medium text-foreground">{option.label}</span>
                {option.hint && <span className="mt-0.5 block text-[11px] leading-snug text-muted-foreground">{option.hint}</span>}
              </span>
            ) : (
              option.label
            )}
          </button>
        );
      })}
    </div>
  );
}
