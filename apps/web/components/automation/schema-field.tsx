'use client';

import { useId } from 'react';
import Link from 'next/link';
import { Clock, Lock, Plus, ShieldCheck, X } from 'lucide-react';
import { Input } from '@/components/ui/input';
import { Switch } from '@/components/ui/switch';
import { Button } from '@/components/ui/button';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Combobox } from '@/components/shared/Combobox';
import { cn } from '@/lib/utils';
import type { JsonSchema } from '@/hooks/api/automation-api';
import { Segmented } from '@/components/automation/segmented';
import { FIELD_COPY, fieldKind, parseTitle } from '@/components/automation/schema-utils';
import { allTimeZones, browserTimeZone, DAY_KEYS, DAY_LABELS, zoneAbbreviation } from '@/components/automation/schedule';
import { formatMinutes, GiB } from '@/components/automation/rule-meta';

export interface SchemaFieldContext {
  layouts: { id: string; name: string; isActive: boolean }[];
}

interface SchemaFieldProps {
  name: string;
  schema: JsonSchema;
  value: unknown;
  onChange: (value: unknown) => void;
  error?: string;
  required?: boolean;
  active?: boolean;
  onFocusField?: (name: string) => void;
  context: SchemaFieldContext;
}

/** The variable letter from the contract titles (N / M / X) as a tiny mono chip. */
function VariableChip({ letter }: { letter: string }) {
  return (
    <span className="inline-flex size-[18px] items-center justify-center rounded-[4px] bg-foreground/[0.07] font-mono text-[10px] font-semibold text-foreground/80 ring-1 ring-inset ring-foreground/10">
      {letter}
    </span>
  );
}

function rangeHint(schema: JsonSchema, bytes: boolean): string | null {
  const conv = (v: number) => (bytes ? `${v / GiB} GiB` : v.toLocaleString());
  if (schema.minimum !== undefined && schema.maximum !== undefined) return `${conv(schema.minimum)}–${conv(schema.maximum)}`;
  if (schema.minimum !== undefined) return `≥ ${conv(schema.minimum)}`;
  return null;
}

/**
 * One params field rendered from its JSON Schema: label (with the N / M / X
 * variable from the contract title), the right control for the type, unit,
 * bounds and plain-language help. Copy overrides live in FIELD_COPY.
 */
export function SchemaField({ name, schema, value, onChange, error, required, active, onFocusField, context }: SchemaFieldProps) {
  const id = useId();
  const copy = FIELD_COPY[name] ?? {};
  const { kind, nullable } = fieldKind(schema);
  const title = parseTitle(schema.title);
  const label = copy.label ?? (title.label || name.replace(/_/g, ' '));
  const isNull = value === null;
  const range = kind === 'integer' || kind === 'number' ? rangeHint(schema, !!copy.bytes) : null;
  const help = [copy.help ?? (copy.label ? null : schema.description), range ? `Range ${range}${copy.unit && !copy.bytes ? ` ${copy.unit}` : ''}.` : null].filter(Boolean).join(' ');
  const showNullToggle = nullable && (kind === 'integer' || kind === 'number');

  let control: React.ReactNode;

  if (schema.const !== undefined) {
    control = (
      <div className="flex items-center gap-2 rounded-md border border-dashed border-border bg-muted/30 px-3 py-2 text-xs text-muted-foreground">
        <ShieldCheck className="size-4 text-success" />
        <span>Always on — cannot be turned off.</span>
        <Lock className="ml-auto size-3.5" />
      </div>
    );
  } else if (name === 'layout_id') {
    control = (
      <div className="flex items-center gap-2">
        <Select value={typeof value === 'string' ? value : ''} onValueChange={(v) => onChange(v ? String(v) : null)}>
          <SelectTrigger id={id} className="w-full sm:w-72" aria-invalid={!!error} onFocus={() => onFocusField?.(name)}>
            <SelectValue>{(v: unknown) => context.layouts.find((l) => l.id === v)?.name ?? <span className="text-muted-foreground">Choose a layout</span>}</SelectValue>
          </SelectTrigger>
          <SelectContent>
            {context.layouts.map((l) => (
              <SelectItem key={l.id} value={l.id}>
                {l.name}
                {l.isActive && <span className="ml-2 text-[11px] text-success">active now</span>}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Link href="/layouts/new" className="inline-flex items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground">
          <Plus className="size-3" /> New layout
        </Link>
      </div>
    );
  } else if (name === 'timezone') {
    const zones = allTimeZones();
    const local = browserTimeZone();
    control = (
      <Combobox
        className="w-full sm:w-72"
        options={zones.map((z) => ({ value: z, data: z, searchTerms: [z.replace(/_/g, ' ')] }))}
        value={typeof value === 'string' ? value : 'UTC'}
        onSelect={(v) => onChange(v)}
        searchPlaceholder="Search timezones…"
        renderOption={(z) => (
          <span className="flex w-full items-center justify-between gap-3">
            <span>{z}</span>
            <span className="text-[11px] text-muted-foreground">
              {zoneAbbreviation(z)}
              {z === local ? ' · yours' : ''}
            </span>
          </span>
        )}
      />
    );
  } else if (name === 'days') {
    const days = new Set(Array.isArray(value) ? (value as string[]) : []);
    const toggle = (d: string) => {
      const next = new Set(days);
      if (next.has(d)) next.delete(d);
      else next.add(d);
      onChange(DAY_KEYS.filter((k) => next.has(k)));
    };
    control = (
      <div className="flex flex-wrap items-center gap-2" onFocus={() => onFocusField?.(name)}>
        <div className="inline-flex rounded-md bg-muted p-[3px]" role="group" aria-label="Days">
          {DAY_KEYS.map((d) => (
            <button
              key={d}
              type="button"
              aria-pressed={days.has(d)}
              onClick={() => toggle(d)}
              className={cn(
                'h-7 w-10 rounded-[5px] text-xs font-medium outline-none transition-all focus-visible:ring-2 focus-visible:ring-ring/30',
                days.has(d) ? 'bg-card text-foreground shadow-subtle ring-1 ring-foreground/10' : 'text-muted-foreground hover:text-foreground',
              )}
            >
              {DAY_LABELS[d]}
            </button>
          ))}
        </div>
        <Button type="button" variant="ghost" size="xs" onClick={() => onChange(['mon', 'tue', 'wed', 'thu', 'fri'])}>
          Weekdays
        </Button>
        <Button type="button" variant="ghost" size="xs" onClick={() => onChange([...DAY_KEYS])}>
          Every day
        </Button>
      </div>
    );
  } else if (name === 'respect_time_windows') {
    const windows = Array.isArray(value) ? (value as string[]) : [];
    const update = (i: number, part: 0 | 1, v: string) => {
      const next = windows.map((w, j) => {
        if (j !== i) return w;
        const [a, b] = w.split('-');
        return part === 0 ? `${v}-${b ?? '08:00'}` : `${a ?? '20:00'}-${v}`;
      });
      onChange(next);
    };
    control = (
      <div className="space-y-2" onFocus={() => onFocusField?.(name)}>
        {windows.length === 0 && <p className="text-xs text-muted-foreground">Any time of day.</p>}
        {windows.map((w, i) => {
          const [a, b] = w.split('-');
          return (
            <div key={i} className="flex items-center gap-2">
              <Clock className="size-3.5 text-muted-foreground" />
              <Input type="time" value={a ?? ''} onChange={(e) => update(i, 0, e.target.value)} className="h-8 w-28 font-mono text-xs" aria-label="Window start" />
              <span className="text-xs text-muted-foreground">to</span>
              <Input type="time" value={b ?? ''} onChange={(e) => update(i, 1, e.target.value)} className="h-8 w-28 font-mono text-xs" aria-label="Window end" />
              {a && b && a > b && <span className="text-[11px] text-muted-foreground">crosses midnight</span>}
              <Button type="button" variant="ghost" size="icon-xs" onClick={() => onChange(windows.filter((_, j) => j !== i))} aria-label="Remove window">
                <X />
              </Button>
            </div>
          );
        })}
        <Button type="button" variant="outline" size="xs" onClick={() => onChange([...windows, '20:00-08:00'])}>
          <Plus className="size-3" /> Add window
        </Button>
      </div>
    );
  } else if (kind === 'boolean') {
    control = null;
  } else if (kind === 'array' && schema.items?.enum) {
    const selected = new Set(Array.isArray(value) ? (value as string[]) : []);
    control = (
      <div className="flex flex-wrap gap-2" onFocus={() => onFocusField?.(name)}>
        {(schema.items.enum as string[]).map((opt) => {
          const on = selected.has(opt);
          return (
            <button
              key={opt}
              type="button"
              aria-pressed={on}
              onClick={() => {
                const next = new Set(selected);
                if (on) next.delete(opt);
                else next.add(opt);
                onChange((schema.items?.enum as string[]).filter((o) => next.has(o)));
              }}
              className={cn(
                'inline-flex h-7 items-center gap-1.5 rounded-full border px-3 text-xs font-medium outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring/30',
                on ? 'border-foreground/30 bg-foreground/[0.06] text-foreground' : 'border-border text-muted-foreground hover:text-foreground',
              )}
            >
              <span className={cn('size-1.5 rounded-full', on ? 'bg-foreground' : 'bg-muted-foreground/40')} />
              {copy.options?.[opt] ?? opt}
            </button>
          );
        })}
      </div>
    );
  } else if (schema.enum) {
    const options = (schema.enum as (string | null)[]).map((v) => ({ value: v === null ? 'null' : v, label: copy.options?.[v === null ? 'null' : v] ?? (v === null ? 'Default' : v.replace(/_/g, ' ')) }));
    const current = value === null || value === undefined ? 'null' : String(value);
    control =
      options.length <= 3 && options.every((o) => o.label.length < 34) ? (
        <Segmented id={id} value={current} onChange={(v) => onChange(v === 'null' ? null : v)} options={options} aria-label={label} />
      ) : (
        <Select value={current} onValueChange={(v) => onChange(v === 'null' ? null : String(v))}>
          <SelectTrigger id={id} className="w-full sm:w-72" onFocus={() => onFocusField?.(name)}>
            <SelectValue>{(v: unknown) => options.find((o) => o.value === v)?.label ?? 'Choose'}</SelectValue>
          </SelectTrigger>
          <SelectContent>
            {options.map((o) => (
              <SelectItem key={o.value} value={o.value}>
                {o.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      );
  } else if (kind === 'object') {
    const obj = (value && typeof value === 'object' ? value : null) as { vram_bytes?: number; compute_pct?: number } | null;
    control = (
      <div className="flex flex-wrap items-center gap-3" onFocus={() => onFocusField?.(name)}>
        <div className="relative w-36">
          <Input
            type="number"
            min={0.5}
            step={0.5}
            value={obj?.vram_bytes ? +(obj.vram_bytes / GiB).toFixed(2) : ''}
            placeholder="8"
            onChange={(e) => {
              const gib = e.target.value === '' ? null : Number(e.target.value);
              onChange(gib === null ? null : { ...(obj ?? {}), vram_bytes: Math.round(gib * GiB) });
            }}
            className="h-9 pr-12 font-mono text-xs tabular-nums"
            aria-label="Slice VRAM in GiB"
          />
          <span className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-[11px] text-muted-foreground">GiB</span>
        </div>
        <div className="relative w-32">
          <Input
            type="number"
            min={1}
            max={100}
            value={obj?.compute_pct ?? ''}
            placeholder="100"
            onChange={(e) => onChange({ ...(obj ?? { vram_bytes: 8 * GiB }), compute_pct: e.target.value === '' ? undefined : Number(e.target.value) })}
            className="h-9 pr-16 font-mono text-xs tabular-nums"
            aria-label="Compute share percent"
          />
          <span className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-[11px] text-muted-foreground">% compute</span>
        </div>
      </div>
    );
  } else if (kind === 'integer' || kind === 'number') {
    const display = typeof value === 'number' ? (copy.bytes ? +(value / GiB).toFixed(2) : value) : '';
    control = (
      <div className="space-y-2">
        <div className={cn('relative w-full sm:w-48', isNull && 'opacity-50')}>
          <Input
            id={id}
            type="number"
            inputMode="decimal"
            disabled={isNull}
            min={copy.bytes ? undefined : schema.minimum}
            max={copy.bytes ? undefined : schema.maximum}
            step={kind === 'integer' && !copy.bytes ? 1 : 'any'}
            value={isNull ? '' : display}
            placeholder={isNull ? 'off' : undefined}
            aria-invalid={!!error}
            onFocus={() => onFocusField?.(name)}
            onChange={(e) => {
              if (e.target.value === '') return onChange(undefined);
              const n = Number(e.target.value);
              onChange(copy.bytes ? Math.round(n * GiB) : n);
            }}
            className={cn('h-9 font-mono text-[13px] tabular-nums', copy.unit && 'pr-20')}
          />
          {copy.unit && <span className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-[11px] text-muted-foreground">{copy.unit}</span>}
        </div>
        {copy.presets && !isNull && (
          <div className="flex flex-wrap gap-1">
            {copy.presets.map((p) => (
              <button
                key={p}
                type="button"
                onClick={() => onChange(p)}
                className={cn(
                  'h-6 rounded-[5px] px-2 font-mono text-[11px] tabular-nums outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring/30',
                  value === p ? 'bg-foreground text-background' : 'bg-muted text-muted-foreground hover:text-foreground',
                )}
              >
                {copy.unit === 'min' || copy.unit === 'min before' ? formatMinutes(p) : p}
              </button>
            ))}
          </div>
        )}
      </div>
    );
  } else {
    control = (
      <Input
        id={id}
        value={typeof value === 'string' ? value : ''}
        onChange={(e) => onChange(e.target.value === '' ? (nullable ? null : '') : e.target.value)}
        onFocus={() => onFocusField?.(name)}
        aria-invalid={!!error}
        placeholder={name === 'cron' ? '0 22 * * *' : name === 'at' ? '08:00' : undefined}
        type={name === 'at' ? 'time' : 'text'}
        className={cn('h-9 w-full sm:w-48', (name === 'cron' || name === 'at') && 'font-mono text-[13px]')}
      />
    );
  }

  const booleanRow = kind === 'boolean' && schema.const === undefined;

  return (
    <div
      id={`field-${name}`}
      className={cn('-mx-3 scroll-mt-24 rounded-lg px-3 py-2.5 transition-colors', active && 'bg-brand/[0.035] ring-1 ring-inset ring-brand/25')}
    >
      <div className={cn('flex items-center justify-between gap-3', !booleanRow && 'mb-2')}>
        <label htmlFor={booleanRow ? `${id}-switch` : id} className="flex items-center gap-2 text-[13px] font-medium text-foreground">
          {title.variable && <VariableChip letter={title.variable} />}
          {label}
          {required && !booleanRow && <span className="text-[11px] font-normal text-muted-foreground">required</span>}
        </label>
        {showNullToggle && (
          <span className="flex items-center gap-2 text-[11px] text-muted-foreground">
            {isNull ? 'Off' : 'On'}
            <Switch
              size="sm"
              checked={!isNull}
              onCheckedChange={(on) => {
                onFocusField?.(name);
                onChange(on ? ((schema.default as number | null | undefined) ?? schema.minimum ?? 1) : null);
              }}
              aria-label={`Use ${copy.toggle ?? label}`}
            />
          </span>
        )}
        {booleanRow && <Switch id={`${id}-switch`} checked={value === true} onCheckedChange={(on) => { onFocusField?.(name); onChange(on); }} />}
      </div>
      {control}
      {error ? <p className="mt-1.5 text-[11px] font-medium text-destructive">{error}</p> : help ? <p className="mt-1.5 max-w-prose text-[11px] leading-relaxed text-muted-foreground">{help}</p> : null}
    </div>
  );
}
