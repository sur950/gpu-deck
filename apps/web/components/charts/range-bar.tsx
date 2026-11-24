'use client';

import { Clock } from 'lucide-react';
import { cn } from '@/lib/utils';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { RANGE_PRESETS, STEP_OPTIONS } from '@/components/charts/series-utils';

interface RangeBarProps {
  range: string;
  onRangeChange: (id: string) => void;
  step?: string;
  onStepChange?: (step: string) => void;
  /** Preset ids to offer (default: all). */
  presets?: string[];
  /** Extra scope controls (node / deployment selects). */
  children?: React.ReactNode;
  /** Right-aligned content (live indicator, actions). */
  right?: React.ReactNode;
  className?: string;
}

/**
 * The one filter row above a metrics page: time range first, then bucket
 * step, then scope controls. Everything below re-renders against this slice.
 */
export function RangeBar({ range, onRangeChange, step, onStepChange, presets, children, right, className }: RangeBarProps) {
  return (
    <div className={cn('flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between', className)}>
      <div className="flex flex-wrap items-center gap-2">
        <RangePills value={range} onChange={onRangeChange} presets={presets} />

        {onStepChange && (
          <Select value={step ?? 'auto'} items={STEP_OPTIONS} onValueChange={(v) => onStepChange(String(v ?? 'auto'))}>
            <SelectTrigger size="sm" className="h-9 w-[128px] rounded-full bg-background/60 text-xs" aria-label="Bucket step">
              <span className="text-muted-foreground">Step</span>
              <SelectValue />
            </SelectTrigger>
            <SelectContent align="start">
              {STEP_OPTIONS.map((o) => (
                <SelectItem key={o.value} value={o.value} className="text-xs">
                  {o.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}

        {children}
      </div>
      {right && <div className="flex items-center gap-2">{right}</div>}
    </div>
  );
}

/** Segmented time-range control (radio group of preset pills). */
export function RangePills({
  value,
  onChange,
  presets,
  className,
}: {
  value: string;
  onChange: (id: string) => void;
  presets?: string[];
  className?: string;
}) {
  const options = RANGE_PRESETS.filter((p) => !presets || presets.includes(p.id));
  return (
    <div
      role="radiogroup"
      aria-label="Time range"
      className={cn('inline-flex h-9 items-center gap-0.5 rounded-full border border-border bg-background/60 p-0.5 backdrop-blur', className)}
    >
      <Clock className="ml-2 mr-1 size-3.5 text-muted-foreground" aria-hidden />
      {options.map((p) => {
        const active = p.id === value;
        return (
          <button
            key={p.id}
            type="button"
            role="radio"
            aria-checked={active}
            title={p.label}
            onClick={() => onChange(p.id)}
            className={cn(
              'h-7 rounded-full px-2.5 text-xs font-medium tabular-nums outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring/30',
              active ? 'bg-foreground text-background shadow-premium' : 'text-muted-foreground hover:bg-accent hover:text-foreground',
            )}
          >
            {p.badge}
          </button>
        );
      })}
    </div>
  );
}

/** A rounded-full scope select that matches the range pills. */
export function ScopeSelect({
  value,
  onValueChange,
  placeholder,
  options,
  width = 'w-[180px]',
  ariaLabel,
}: {
  value: string;
  onValueChange: (v: string) => void;
  placeholder: string;
  options: { value: string; label: string }[];
  width?: string;
  ariaLabel?: string;
}) {
  return (
    <Select
      value={value || 'all'}
      items={[{ value: 'all', label: placeholder }, ...options]}
      onValueChange={(v) => onValueChange(v === 'all' || v === null ? '' : String(v))}
    >
      <SelectTrigger size="sm" className={cn('h-9 rounded-full bg-background/60 text-xs', width)} aria-label={ariaLabel ?? placeholder}>
        <SelectValue placeholder={placeholder} />
      </SelectTrigger>
      <SelectContent align="start">
        <SelectItem value="all" className="text-xs">
          {placeholder}
        </SelectItem>
        {options.map((o) => (
          <SelectItem key={o.value} value={o.value} className="text-xs">
            {o.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
