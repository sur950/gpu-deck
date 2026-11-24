'use client';

import { Plus, Trash2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { cn } from '@/lib/utils';

export interface KeyValueRow {
  key: string;
  value: string;
}

interface KeyValueEditorProps {
  rows: KeyValueRow[];
  onChange: (rows: KeyValueRow[]) => void;
  keyPlaceholder?: string;
  valuePlaceholder?: string;
  addLabel?: string;
  className?: string;
  /** Mono inputs (flags, env vars). */
  mono?: boolean;
}

/** Editable key / value rows for labels, env vars and launch flags. */
export function KeyValueEditor({
  rows,
  onChange,
  keyPlaceholder = 'key',
  valuePlaceholder = 'value',
  addLabel = 'Add',
  className,
  mono = true,
}: KeyValueEditorProps) {
  function update(index: number, patch: Partial<KeyValueRow>) {
    onChange(rows.map((row, i) => (i === index ? { ...row, ...patch } : row)));
  }

  return (
    <div className={cn('space-y-2', className)}>
      {rows.map((row, index) => (
        <div key={index} className="flex items-center gap-2">
          <Input
            value={row.key}
            onChange={(e) => update(index, { key: e.target.value })}
            placeholder={keyPlaceholder}
            className={cn('h-9', mono && 'font-mono text-xs')}
            aria-label={`Key ${index + 1}`}
          />
          <span className="text-muted-foreground">=</span>
          <Input
            value={row.value}
            onChange={(e) => update(index, { value: e.target.value })}
            placeholder={valuePlaceholder}
            className={cn('h-9', mono && 'font-mono text-xs')}
            aria-label={`Value ${index + 1}`}
          />
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            onClick={() => onChange(rows.filter((_, i) => i !== index))}
            aria-label="Remove row"
            className="text-muted-foreground hover:text-destructive"
          >
            <Trash2 className="size-4" />
          </Button>
        </div>
      ))}
      <Button type="button" variant="outline" size="xs" onClick={() => onChange([...rows, { key: '', value: '' }])}>
        <Plus className="size-3" />
        {addLabel}
      </Button>
    </div>
  );
}

/** Convert rows to a record, dropping blank keys. */
export function rowsToRecord(rows: KeyValueRow[]): Record<string, string> {
  const out: Record<string, string> = {};
  for (const row of rows) {
    const key = row.key.trim();
    if (key) out[key] = row.value;
  }
  return out;
}

export function recordToRows(record: Record<string, unknown> | null | undefined): KeyValueRow[] {
  return Object.entries(record ?? {}).map(([key, value]) => ({
    key,
    value: typeof value === 'string' ? value : value === true ? '' : JSON.stringify(value),
  }));
}

/** Launch flags: a blank value means a bare flag (`true`); numbers are parsed. */
export function rowsToFlags(rows: KeyValueRow[]): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const row of rows) {
    const key = row.key.trim();
    if (!key) continue;
    const value = row.value.trim();
    if (value === '') out[key] = true;
    else if (value === 'true' || value === 'false') out[key] = value === 'true';
    else if (/^-?\d+(\.\d+)?$/.test(value)) out[key] = Number(value);
    else out[key] = value;
  }
  return out;
}
