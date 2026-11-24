'use client';

import { cn } from '@/lib/utils';

type Flat = Map<string, string>;

function flatten(value: unknown, prefix = '', out: Flat = new Map()): Flat {
  if (value === null || value === undefined) {
    if (prefix) out.set(prefix, value === null ? 'null' : '—');
    return out;
  }
  if (Array.isArray(value)) {
    if (value.every((v) => v === null || typeof v !== 'object')) out.set(prefix, `[${value.map((v) => JSON.stringify(v)).join(', ')}]`);
    else value.forEach((v, i) => flatten(v, `${prefix}[${i}]`, out));
    return out;
  }
  if (typeof value === 'object') {
    const entries = Object.entries(value as Record<string, unknown>);
    if (!entries.length && prefix) out.set(prefix, '{}');
    for (const [k, v] of entries) flatten(v, prefix ? `${prefix}.${k}` : k, out);
    return out;
  }
  out.set(prefix, typeof value === 'string' ? value : JSON.stringify(value));
  return out;
}

interface StateDiffProps {
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
}

/**
 * Before / after as a field-level diff: one row per changed path with the
 * old value struck through and the new value highlighted; unchanged fields
 * are listed muted below.
 */
export function StateDiff({ before, after }: StateDiffProps) {
  if (!before && !after) return <p className="text-xs text-muted-foreground">This event recorded no state.</p>;
  const a = flatten(before ?? {});
  const b = flatten(after ?? {});
  const keys = [...new Set([...a.keys(), ...b.keys()])];
  const rows = keys.map((k) => ({ key: k, from: a.get(k), to: b.get(k) }));
  const changed = rows.filter((r) => r.from !== r.to);
  const same = rows.filter((r) => r.from === r.to);
  return (
    <div className="overflow-hidden rounded-lg border border-border text-xs">
      <div className="grid grid-cols-[minmax(110px,0.8fr)_1fr_1fr] border-b border-border bg-muted/40 px-3 py-1.5 text-[11px] font-medium text-muted-foreground">
        <span>Field</span>
        <span>Before</span>
        <span>After</span>
      </div>
      {changed.map((r) => (
        <div key={r.key} className="grid grid-cols-[minmax(110px,0.8fr)_1fr_1fr] gap-2 border-b border-border px-3 py-1.5 last:border-b-0">
          <span className="break-all font-mono text-[11px] text-muted-foreground">{r.key}</span>
          <span className={cn('break-all font-mono text-[11px]', r.from === undefined ? 'text-muted-foreground' : 'text-error-dark line-through decoration-error/50 dark:text-error-light')}>{r.from ?? 'added'}</span>
          <span className={cn('break-all font-mono text-[11px]', r.to === undefined ? 'text-muted-foreground' : 'rounded-[3px] bg-success/10 px-1 text-success-dark dark:text-success-light')}>{r.to ?? 'removed'}</span>
        </div>
      ))}
      {same.map((r) => (
        <div key={r.key} className="grid grid-cols-[minmax(110px,0.8fr)_2fr] gap-2 border-b border-border px-3 py-1.5 text-muted-foreground last:border-b-0">
          <span className="break-all font-mono text-[11px]">{r.key}</span>
          <span className="break-all font-mono text-[11px]">{r.to}</span>
        </div>
      ))}
    </div>
  );
}
