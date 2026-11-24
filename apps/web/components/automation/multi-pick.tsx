'use client';

import { useState } from 'react';
import { Check, ChevronsUpDown, X } from 'lucide-react';
import { Command, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList } from '@/components/ui/command';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { buttonVariants } from '@/components/ui/button';
import { cn } from '@/lib/utils';

export interface PickOption {
  value: string;
  label: string;
  /** Muted text after the label (engine, node…). */
  hint?: string;
  /** Small coloured dot before the label (theme class, e.g. bg-success). */
  dot?: string;
}

interface MultiPickProps {
  options: PickOption[];
  value: string[];
  onChange: (value: string[]) => void;
  placeholder?: string;
  searchPlaceholder?: string;
  emptyText?: string;
  className?: string;
  'aria-label'?: string;
}

/**
 * Searchable multi-select: selected values sit as removable chips inside the
 * trigger, the popover lists every option with a check mark.
 */
export function MultiPick({ options, value, onChange, placeholder = 'Choose…', searchPlaceholder = 'Search…', emptyText = 'Nothing found.', className, ...rest }: MultiPickProps) {
  const [open, setOpen] = useState(false);
  const selected = new Set(value);
  const byValue = new Map(options.map((o) => [o.value, o]));

  function toggle(v: string) {
    const next = new Set(selected);
    if (next.has(v)) next.delete(v);
    else next.add(v);
    onChange(options.map((o) => o.value).filter((o) => next.has(o)).concat(value.filter((v2) => !byValue.has(v2) && next.has(v2))));
  }

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger
        aria-label={rest['aria-label']}
        className={cn(
          buttonVariants({ variant: 'outline' }),
          'h-auto min-h-9 w-full justify-between gap-2 px-2 py-1 font-normal sm:w-[26rem]',
          className,
        )}
      >
        <span className="flex min-w-0 flex-1 flex-wrap items-center gap-1">
          {value.length === 0 && <span className="px-1 text-xs text-muted-foreground">{placeholder}</span>}
          {value.map((v) => {
            const o = byValue.get(v);
            return (
              <span key={v} className="inline-flex h-6 items-center gap-1 rounded-[5px] bg-muted pl-2 pr-1 text-[11px] font-medium text-foreground">
                {o?.dot && <span className={cn('size-1.5 rounded-full', o.dot)} />}
                {o?.label ?? `${v.slice(0, 8)}…`}
                <span
                  role="button"
                  tabIndex={-1}
                  aria-label={`Remove ${o?.label ?? v}`}
                  onPointerDown={(e) => e.stopPropagation()}
                  onClick={(e) => {
                    e.stopPropagation();
                    e.preventDefault();
                    toggle(v);
                  }}
                  className="rounded-[3px] p-0.5 text-muted-foreground hover:bg-foreground/10 hover:text-foreground"
                >
                  <X className="size-3" />
                </span>
              </span>
            );
          })}
        </span>
        <ChevronsUpDown className="size-3.5 shrink-0 text-muted-foreground" />
      </PopoverTrigger>
      <PopoverContent align="start" className="w-[var(--anchor-width)] min-w-72 p-0">
        <Command>
          <CommandInput placeholder={searchPlaceholder} />
          <CommandList>
            <CommandEmpty>{emptyText}</CommandEmpty>
            <CommandGroup>
              {options.map((o) => (
                <CommandItem key={o.value} value={`${o.label} ${o.hint ?? ''} ${o.value}`} onSelect={() => toggle(o.value)} className="gap-2 text-xs">
                  <span className={cn('flex size-4 items-center justify-center rounded-[4px] border', selected.has(o.value) ? 'border-foreground bg-foreground text-background' : 'border-border')}>
                    {selected.has(o.value) && <Check className="size-3" />}
                  </span>
                  {o.dot && <span className={cn('size-1.5 rounded-full', o.dot)} />}
                  <span className="truncate">{o.label}</span>
                  {o.hint && <span className="ml-auto truncate text-[11px] text-muted-foreground">{o.hint}</span>}
                </CommandItem>
              ))}
            </CommandGroup>
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}
