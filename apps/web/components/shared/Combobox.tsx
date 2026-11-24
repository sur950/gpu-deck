'use client';

import { useRef, useState, type ReactNode } from 'react';
import { Check, ChevronsUpDown, Loader2 } from 'lucide-react';
import { buttonVariants } from '@/components/ui/button';
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from '@/components/ui/command';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { cn } from '@/lib/utils';

export interface ComboboxOption<T = unknown> {
  /** The value passed to onSelect and compared against `value` to determine selection. */
  value: string;
  /** Extra terms (id, provider, description, etc.) matched against the search box, in addition to the value. */
  searchTerms?: string[];
  /** The raw data for this option, handed back to renderOption/renderSelected. */
  data: T;
  /** When true, the option is shown but cannot be selected (e.g. the user's current role). */
  disabled?: boolean;
}

/**
 * A fixed choice listed below the searchable options, outside search filtering
 * (e.g. "None", "Custom (type manually)…"). The combobox closes when it is chosen.
 */
export interface ComboboxFooterItem {
  /** Search value for the item; must not collide with an option value. */
  value: string;
  label: ReactNode;
  /** This item is the current choice: shows the check mark (and `selectedLabel` in the trigger). */
  selected?: boolean;
  /** Trigger content while this item is the current choice. Omit to keep showing the placeholder. */
  selectedLabel?: ReactNode;
  /**
   * Set false when choosing this item reveals a field that takes focus itself
   * (e.g. an `autoFocus` input) — otherwise closing hands focus back to the trigger.
   */
  returnFocus?: boolean;
  onSelect: () => void;
}

export interface ComboboxProps<T = unknown> {
  options: ComboboxOption<T>[];
  value: string;
  onSelect: (value: string) => void;
  /** Full row content for one option (icon, label, badges, etc.) — a trailing check mark is added automatically. */
  renderOption: (data: T, option: ComboboxOption<T>) => ReactNode;
  /** Trigger display when a value is selected — falls back to renderOption's content if omitted. */
  renderSelected?: (data: T) => ReactNode;
  /** Fixed choices below the searchable list, outside search filtering (e.g. a "Custom…" escape hatch). */
  footerItems?: ComboboxFooterItem[];
  placeholder?: string;
  searchPlaceholder?: string;
  emptyText?: ReactNode;
  loading?: boolean;
  loadingText?: string;
  disabled?: boolean;
  className?: string;
  contentClassName?: string;
}

/** Generic searchable single-select combobox (Popover + Command), used anywhere a plain
 * <Select> would list an open-ended/org-scoped dataset (models, providers, guardrails,
 * credentials, users, teams, roles, etc.) that benefits from a search box. */
export function Combobox<T = unknown>({
  options,
  value,
  onSelect,
  renderOption,
  renderSelected,
  footerItems,
  placeholder = 'Select...',
  searchPlaceholder = 'Search...',
  emptyText = 'No results found.',
  loading = false,
  loadingText = 'Loading...',
  disabled,
  className,
  contentClassName,
}: ComboboxProps<T>) {
  const [open, setOpen] = useState(false);
  // Whether closing returns focus to the trigger; a footer item can opt out.
  const returnFocus = useRef(true);
  const selected = value ? options.find(o => o.value === value) : undefined;
  const selectedFooterLabel = footerItems?.find(item => item.selected)?.selectedLabel;

  function choose(onChoose: () => void, focusTriggerAfter = true) {
    returnFocus.current = focusTriggerAfter;
    onChoose();
    setOpen(false);
  }

  return (
    <Popover
      open={open}
      onOpenChange={(next) => {
        if (next) returnFocus.current = true;
        setOpen(next);
      }}
    >
      <PopoverTrigger
        role="combobox"
        aria-expanded={open}
        disabled={disabled ?? loading}
        className={cn(buttonVariants({ variant: 'outline' }), 'w-full justify-between font-normal', className)}
      >
        {loading ? (
          <span className="flex items-center gap-2 text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" />
            {loadingText}
          </span>
        ) : selected ? (
          <span className="flex min-w-0 flex-1 items-center gap-2 truncate">
            {renderSelected ? renderSelected(selected.data) : renderOption(selected.data, selected)}
          </span>
        ) : selectedFooterLabel ? (
          <span className="flex min-w-0 flex-1 items-center gap-2 truncate">{selectedFooterLabel}</span>
        ) : (
          <span className="text-muted-foreground">{placeholder}</span>
        )}
        <ChevronsUpDown className="ml-2 h-4 w-4 shrink-0 opacity-50" />
      </PopoverTrigger>
      <PopoverContent
        className={cn('p-0', contentClassName)}
        align="start"
        style={{ width: 'var(--anchor-width)' }}
        finalFocus={() => returnFocus.current}
      >
        <Command>
          <CommandInput placeholder={searchPlaceholder} />
          <CommandList className="max-h-72">
            {options.length === 0 ? (
              <div className="py-4 px-3 text-sm text-muted-foreground text-center">{emptyText}</div>
            ) : (
              <>
                <CommandEmpty>{emptyText}</CommandEmpty>
                <CommandGroup>
                  {options.map(opt => (
                    <CommandItem
                      key={opt.value}
                      value={[opt.value, ...(opt.searchTerms ?? [])].join(' ')}
                      onSelect={() => { if (opt.disabled) return; choose(() => onSelect(opt.value)); }}
                      aria-disabled={opt.disabled}
                      className={cn('flex items-center gap-2', opt.disabled && 'pointer-events-none opacity-50')}
                    >
                      <span className="flex min-w-0 flex-1 items-center gap-2">
                        {renderOption(opt.data, opt)}
                      </span>
                      {value === opt.value && <Check className="h-4 w-4 shrink-0" />}
                    </CommandItem>
                  ))}
                </CommandGroup>
              </>
            )}
            {footerItems && footerItems.length > 0 && (
              <CommandGroup>
                {footerItems.map(item => (
                  <CommandItem
                    key={item.value}
                    value={item.value}
                    onSelect={() => choose(item.onSelect, item.returnFocus ?? true)}
                  >
                    <span className="text-muted-foreground italic">{item.label}</span>
                    {item.selected && <Check className="ml-auto h-4 w-4 shrink-0" />}
                  </CommandItem>
                ))}
              </CommandGroup>
            )}
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}
