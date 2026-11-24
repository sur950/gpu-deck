/**
 * DateRangeFilter — shared time-range picker
 * Configurable preset sets; supports custom date inputs when 'custom' preset included.
 * Used by: Usage Dashboard, Guardrail Monitor, Cache Analytics, Dashboard
 */

'use client';

import { useState } from 'react';
import { Clock, ChevronDown, CalendarDays } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover';
import { cn } from '@/lib/utils';

// ─── Types ────────────────────────────────────────────────────────────────────

export interface DateRangeValue {
  preset: string;
  from: string;   // ISO datetime
  to: string;     // ISO datetime
  label: string;
}

export interface DatePresetConfig {
  id: string;
  label: string;
  badge: string;
}

// ─── Preset sets ──────────────────────────────────────────────────────────────

/** Standard analytics presets — used on Usage Dashboard and /dashboard */
export const USAGE_PRESETS: DatePresetConfig[] = [
  { id: 'today',    label: 'Today',          badge: 'Today' },
  { id: 'last_7d',  label: 'Last 7 days',    badge: '7d'    },
  { id: 'last_30d', label: 'Last 30 days',   badge: '30d'   },
  { id: 'last_90d', label: 'Last 90 days',   badge: '90d'   },
  { id: 'mtd',      label: 'Month to date',  badge: 'MTD'   },
  { id: 'ytd',      label: 'Year to date',   badge: 'YTD'   },
  { id: 'custom',   label: 'Custom range',   badge: 'Custom'},
];

/** Short-window presets — used on Guardrail Monitor and Cache Analytics */
export const MONITOR_PRESETS: DatePresetConfig[] = [
  { id: 'last_1h',  label: 'Last hour',      badge: '1h'  },
  { id: 'last_6h',  label: 'Last 6 hours',   badge: '6h'  },
  { id: 'last_24h', label: 'Last 24 hours',  badge: '24h' },
  { id: 'last_7d',  label: 'Last 7 days',    badge: '7d'  },
];

// ─── Helpers ──────────────────────────────────────────────────────────────────

export function computeDateRange(preset: string): { from: string; to: string } {
  const now = new Date();
  const endOfDay = new Date(now);
  endOfDay.setHours(23, 59, 59, 999);
  const startOfDay = new Date(now);
  startOfDay.setHours(0, 0, 0, 0);

  switch (preset) {
    case 'today':
      return { from: startOfDay.toISOString(), to: endOfDay.toISOString() };
    case 'last_1h': {
      const from = new Date(now.getTime() - 60 * 60 * 1000);
      return { from: from.toISOString(), to: now.toISOString() };
    }
    case 'last_6h': {
      const from = new Date(now.getTime() - 6 * 60 * 60 * 1000);
      return { from: from.toISOString(), to: now.toISOString() };
    }
    case 'last_24h': {
      const from = new Date(now.getTime() - 24 * 60 * 60 * 1000);
      return { from: from.toISOString(), to: now.toISOString() };
    }
    case 'last_7d': {
      const from = new Date(startOfDay);
      from.setDate(from.getDate() - 7);
      return { from: from.toISOString(), to: endOfDay.toISOString() };
    }
    case 'last_30d': {
      const from = new Date(startOfDay);
      from.setDate(from.getDate() - 30);
      return { from: from.toISOString(), to: endOfDay.toISOString() };
    }
    case 'last_90d': {
      const from = new Date(startOfDay);
      from.setDate(from.getDate() - 90);
      return { from: from.toISOString(), to: endOfDay.toISOString() };
    }
    case 'mtd': {
      const from = new Date(now.getFullYear(), now.getMonth(), 1, 0, 0, 0, 0);
      return { from: from.toISOString(), to: endOfDay.toISOString() };
    }
    case 'ytd': {
      const from = new Date(now.getFullYear(), 0, 1, 0, 0, 0, 0);
      return { from: from.toISOString(), to: endOfDay.toISOString() };
    }
    default:
      return { from: startOfDay.toISOString(), to: endOfDay.toISOString() };
  }
}

export function buildDefaultDateRange(preset: string, presets: DatePresetConfig[]): DateRangeValue {
  const config = presets.find(p => p.id === preset);
  const { from, to } = computeDateRange(preset);
  return { preset, from, to, label: config?.label ?? preset };
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString('en-US', {
    month: 'short', day: 'numeric', year: 'numeric',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
    timeZone: 'UTC',
  });
}

function toDateInputValue(iso: string): string {
  return iso.slice(0, 10);
}

// ─── Component ────────────────────────────────────────────────────────────────

interface DateRangeFilterProps {
  value: DateRangeValue;
  onChange: (value: DateRangeValue) => void;
  presets?: DatePresetConfig[];
}

export function DateRangeFilter({
  value,
  onChange,
  presets = USAGE_PRESETS,
}: DateRangeFilterProps) {
  const [open, setOpen] = useState(false);
  const [draftPreset, setDraftPreset] = useState(value.preset);
  const [draftFrom, setDraftFrom] = useState(value.from);
  const [draftTo, setDraftTo] = useState(value.to);

  const hasCustom = presets.some(p => p.id === 'custom');

  function handlePresetClick(preset: string) {
    setDraftPreset(preset);
    if (preset !== 'custom') {
      const { from, to } = computeDateRange(preset);
      setDraftFrom(from);
      setDraftTo(to);
    }
  }

  function handleApply() {
    const presetConfig = presets.find(p => p.id === draftPreset);
    const label =
      draftPreset === 'custom'
        ? `${new Date(draftFrom).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })} – ${new Date(draftTo).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}`
        : (presetConfig?.label ?? draftPreset);
    onChange({ preset: draftPreset, from: draftFrom, to: draftTo, label });
    setOpen(false);
  }

  function handleCancel() {
    setDraftPreset(value.preset);
    setDraftFrom(value.from);
    setDraftTo(value.to);
    setOpen(false);
  }

  const triggerLabel = value.preset !== 'custom'
    ? (presets.find(p => p.id === value.preset)?.label ?? value.label)
    : value.label;

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger render={<Button variant="outline" className="rounded-full gap-1.5 font-normal" />}>
        <Clock className="size-3.5 text-muted-foreground" />
        <span>{triggerLabel}</span>
        <ChevronDown className="size-3.5 text-muted-foreground" />
      </PopoverTrigger>
      <PopoverContent
        className={cn('w-auto p-0', hasCustom ? 'min-w-[560px]' : 'min-w-[220px]')}
        align="start"
        side="bottom"
      >
        <div className="flex">
          {/* Preset list */}
          <div className={cn(
            'border-r border-border p-3 flex flex-col gap-1',
            hasCustom ? 'w-60' : 'w-full'
          )}>
            <p className="text-xs font-semibold text-muted-foreground mb-2 px-1">
              Relative time
            </p>
            {presets.map((preset) => {
              const isActive = draftPreset === preset.id;
              return (
                <Button
                  key={preset.id}
                  variant="ghost"
                  onClick={() => handlePresetClick(preset.id)}
                  className={cn(
                    'flex items-center justify-between w-full rounded-md px-2 py-1.5 h-auto text-xs cursor-pointer transition-colors',
                    isActive
                      ? 'bg-brand/10 text-brand-text font-semibold hover:bg-brand/10'
                      : 'hover:bg-muted text-foreground'
                  )}
                >
                  <span>{preset.label}</span>
                  <Badge
                    variant={isActive ? 'default' : 'secondary'}
                    className={cn(
                      'rounded px-1.5 py-0.5 text-[10px] font-medium',
                      isActive
                        ? 'bg-brand/20 text-brand-text hover:bg-brand/20'
                        : 'bg-muted text-muted-foreground'
                    )}
                  >
                    {preset.badge}
                  </Badge>
                </Button>
              );
            })}
            {/* Apply/Cancel inline when no custom date panel */}
            {!hasCustom && (
              <div className="flex gap-2 pt-2 border-t border-border mt-1">
                <Button variant="outline" size="sm" className="flex-1" onClick={handleCancel}>
                  Cancel
                </Button>
                <Button size="sm" className="flex-1" onClick={handleApply}>
                  Apply
                </Button>
              </div>
            )}
          </div>

          {/* Custom date inputs — only shown when 'custom' preset is available */}
          {hasCustom && (
            <div className="flex-1 p-4 flex flex-col gap-4">
              <div>
                <p className="text-xs font-semibold mb-3 flex items-center gap-1.5">
                  <CalendarDays className="size-3.5 text-muted-foreground" />
                  Start and end dates
                </p>
                <div className="grid grid-cols-2 gap-3">
                  <div className="flex flex-col gap-1">
                    <Label className="text-xs text-muted-foreground">Start date (max 90-day range)</Label>
                    <Input
                      type="date"
                      value={toDateInputValue(draftFrom)}
                      max={toDateInputValue(draftTo) || toDateInputValue(new Date().toISOString())}
                      onChange={(e) => {
                        const val = e.target.value;
                        if (!val) return;
                        setDraftPreset('custom');
                        // Ensure range doesn't exceed 90 days
                        const from = new Date(val + 'T00:00:00.000Z');
                        const to = new Date(draftTo);
                        const maxFrom = new Date(to); maxFrom.setDate(maxFrom.getDate() - 90);
                        setDraftFrom((from < maxFrom ? maxFrom : from).toISOString().replace(/T.*/, 'T00:00:00.000Z'));
                      }}
                      className="h-8 text-xs"
                    />
                  </div>
                  <div className="flex flex-col gap-1">
                    <Label className="text-xs text-muted-foreground">End date</Label>
                    <Input
                      type="date"
                      value={toDateInputValue(draftTo)}
                      min={toDateInputValue(draftFrom)}
                      max={(() => { const d = new Date(draftFrom); d.setDate(d.getDate() + 90); return toDateInputValue(d.toISOString()); })()}
                      onChange={(e) => {
                        const val = e.target.value;
                        if (!val) return;
                        setDraftPreset('custom');
                        const from = new Date(draftFrom);
                        const maxTo = new Date(from); maxTo.setDate(maxTo.getDate() + 90);
                        const chosen = new Date(val + 'T23:59:59.000Z');
                        setDraftTo((chosen > maxTo ? maxTo : chosen).toISOString().replace(/T.*/, 'T23:59:59.000Z'));
                      }}
                      className="h-8 text-xs"
                    />
                  </div>
                </div>
              </div>
              <div className="bg-brand/5 text-brand-text text-xs rounded-md p-3 leading-relaxed whitespace-pre-line">
                {`From: ${formatDate(draftFrom)}\nTo:   ${formatDate(draftTo)}`}
              </div>
              <div className="flex justify-end gap-2 pt-1">
                <Button variant="outline" size="sm" onClick={handleCancel}>Cancel</Button>
                <Button size="sm" onClick={handleApply}>Apply</Button>
              </div>
            </div>
          )}
        </div>
      </PopoverContent>
    </Popover>
  );
}
