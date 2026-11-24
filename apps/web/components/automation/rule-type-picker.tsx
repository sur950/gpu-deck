'use client';

import { Lock } from 'lucide-react';
import { cn } from '@/lib/utils';
import { RULE_TYPES, type RuleType } from '@/hooks/api/automation-api';
import { RULE_TYPE_META, RuleGlyph } from '@/components/automation/rule-meta';

interface RuleTypePickerProps {
  value: RuleType | null;
  onChange: (type: RuleType) => void;
  /** Editing an existing rule: the type is fixed. */
  locked?: boolean;
}

/**
 * The six rule types as cards: icon tile, name, one-line promise, a small
 * glyph of the trigger shape and "when → then" footer. Arrow keys move
 * between cards (radio group semantics).
 */
export function RuleTypePicker({ value, onChange, locked }: RuleTypePickerProps) {
  const types = locked && value ? [value] : RULE_TYPES;
  return (
    <div role="radiogroup" aria-label="Rule type" className={cn('grid gap-3', locked ? 'grid-cols-1' : 'sm:grid-cols-2 xl:grid-cols-3')}>
      {types.map((type, i) => {
        const meta = RULE_TYPE_META[type];
        const Icon = meta.icon;
        const selected = value === type;
        return (
          <button
            key={type}
            type="button"
            role="radio"
            aria-checked={selected}
            disabled={locked}
            tabIndex={selected || (!value && i === 0) ? 0 : -1}
            onClick={() => onChange(type)}
            onKeyDown={(e) => {
              const delta = e.key === 'ArrowRight' || e.key === 'ArrowDown' ? 1 : e.key === 'ArrowLeft' || e.key === 'ArrowUp' ? -1 : 0;
              if (!delta) return;
              e.preventDefault();
              const next = RULE_TYPES[(RULE_TYPES.indexOf(type) + delta + RULE_TYPES.length) % RULE_TYPES.length];
              onChange(next);
              (e.currentTarget.parentElement?.querySelector(`[data-type="${next}"]`) as HTMLButtonElement | null)?.focus();
            }}
            data-type={type}
            className={cn(
              'group/type relative flex flex-col overflow-hidden rounded-xl border bg-card p-4 text-left outline-none transition-all duration-200 focus-visible:ring-2 focus-visible:ring-ring/30 disabled:cursor-default',
              selected
                ? 'border-foreground/30 shadow-premium-lg ring-1 ring-foreground/15'
                : 'border-border shadow-premium hover:-translate-y-0.5 hover:border-foreground/15 hover:shadow-premium-lg',
            )}
          >
            <span
              aria-hidden
              className={cn(
                'pointer-events-none absolute inset-x-6 top-0 h-px bg-gradient-to-r from-transparent via-brand/60 to-transparent transition-opacity',
                selected ? 'opacity-100' : 'opacity-0 group-hover/type:opacity-60',
              )}
            />
            <div className="flex items-start justify-between gap-3">
              <span className={cn('flex size-9 items-center justify-center rounded-lg border transition-colors', selected ? 'border-foreground/20 bg-foreground text-background' : 'border-border bg-muted/60 text-foreground/80')}>
                <Icon className="size-4" />
              </span>
              <RuleGlyph type={type} className={cn('h-7 w-16 transition-colors', selected ? 'text-brand' : 'text-muted-foreground/60 group-hover/type:text-foreground/50')} />
            </div>
            <p className="mt-3 flex items-center gap-2 text-sm font-semibold tracking-tight">
              {meta.label}
              {locked && <Lock className="size-3 text-muted-foreground" aria-label="Type cannot change" />}
            </p>
            <p className="mb-3 mt-0.5 text-xs leading-relaxed text-muted-foreground">{locked ? meta.description : meta.tagline}</p>
            <dl className="mt-auto grid grid-cols-[auto_1fr] items-baseline gap-x-3 gap-y-1 border-t border-border/70 pt-2.5 text-[11px]">
              <dt className="text-[10px] font-semibold uppercase tracking-[0.08em] text-muted-foreground/70">When</dt>
              <dd className="text-foreground/80">{meta.when}</dd>
              <dt className="text-[10px] font-semibold uppercase tracking-[0.08em] text-muted-foreground/70">Then</dt>
              <dd className="text-foreground/80">{meta.then}</dd>
            </dl>
          </button>
        );
      })}
    </div>
  );
}
