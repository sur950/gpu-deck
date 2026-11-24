'use client';

import { useState } from 'react';
import Link from 'next/link';
import { ArrowUpRight, Check, ChevronRight, CircleAlert, Minus, ShieldAlert } from 'lucide-react';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { JsonView } from '@/components/shared/json-view';
import { StatusBadge, toBadgeVariant } from '@/components/shared/StatusBadge';
import { cn } from '@/lib/utils';
import type { RuleRun } from '@/hooks/api/automation-api';
import { formatMinutes, formatSeconds, humanize } from '@/components/automation/rule-meta';
import { formatBytes } from '@/utils/format';
import { OUTCOME_META, reasonText } from '@/components/automation/run-meta';

const TABLE_KEYS = ['targets', 'instances', 'candidate_victims', 'victims'];
const HIDDEN_KEYS = new Set(['reason', 'warnings', ...TABLE_KEYS]);

const KEY_LABELS: Record<string, string> = {
  threshold_minutes: 'Idle threshold',
  in_window: 'Inside time window',
  windows: 'Time windows',
  window_minutes: 'Window',
  waiting_threshold: 'Waiting threshold',
  kv_threshold_pct: 'KV threshold',
  threshold_rpm: 'Traffic threshold',
  health_failures_threshold: 'Health failure threshold',
  error_rate_threshold_pct: 'Error-rate threshold',
  min_requests_in_window: 'Min requests',
  cooldown_remaining_seconds: 'Cooldown left',
  pending_deployments: 'Models waiting for VRAM',
  evict_priorities: 'May evict',
  victim_idle_minutes: 'Victim idle for',
  next_scheduled: 'Next scheduled run',
  scheduled_for: 'Scheduled for',
  idle_minutes: 'Idle',
  active_sessions: 'Active',
  waiting_avg: 'Waiting (avg)',
  kv_cache_pct_max: 'KV cache (max)',
  satisfied_minutes: 'Minutes over',
  max_rpm_in_window: 'Peak req/min',
  min_rpm_in_window: 'Lowest req/min',
  error_rate_pct_min: 'Errors (min)',
  error_rate_pct_max: 'Errors (max)',
  requests_in_window: 'Requests',
  consecutive_failures: 'Failed checks',
  meets: 'Condition',
  needed_bytes: 'VRAM needed',
  vram_bytes: 'VRAM',
};

/** Format a decision value by what its key says it is. */
export function formatDecisionValue(key: string, value: unknown): React.ReactNode {
  if (value === null || value === undefined) return <span className="text-muted-foreground">—</span>;
  if (typeof value === 'boolean') {
    if (key === 'meets') return value ? <Check className="size-3.5 text-success" aria-label="met" /> : <Minus className="size-3.5 text-muted-foreground" aria-label="not met" />;
    return value ? 'Yes' : 'No';
  }
  if (typeof value === 'number') {
    if (key.endsWith('_bytes')) return formatBytes(value);
    if (key.endsWith('_seconds')) return formatSeconds(value);
    if (key.endsWith('minutes') || key === 'window_minutes') return formatMinutes(value);
    if (key.includes('pct')) return `${value}%`;
    if (key.includes('rpm')) return `${value} req/min`;
    return value.toLocaleString();
  }
  if (typeof value === 'string') {
    if (/^\d{4}-\d{2}-\d{2}T/.test(value)) return new Date(value).toLocaleString();
    if (key === 'reason') return reasonText(value);
    return value;
  }
  if (Array.isArray(value)) return value.length ? value.map((v) => (typeof v === 'object' ? JSON.stringify(v) : String(v))).join(', ') : '—';
  return <code className="font-mono text-[11px]">{JSON.stringify(value)}</code>;
}

function ObservedTable({ rows }: { rows: Record<string, unknown>[] }) {
  const keys = [...new Set(rows.flatMap((r) => Object.keys(r)))];
  const first = keys.find((k) => ['deployment', 'instance'].includes(k));
  const ordered = [...(first ? [first] : []), ...keys.filter((k) => k !== first && k !== 'meets' && k !== 'protected'), ...(keys.includes('meets') ? ['meets'] : [])];
  return (
    <div className="overflow-x-auto rounded-lg border border-border">
      <Table>
        <TableHeader>
          <TableRow>
            {ordered.map((k) => (
              <TableHead key={k} className={cn('h-8 whitespace-nowrap text-[11px]', k !== first && 'text-right')}>
                {KEY_LABELS[k] ?? humanize(k)}
              </TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((row, i) => (
            <TableRow key={i} className={cn(row.meets === true && 'bg-success/[0.05]')}>
              {ordered.map((k) => (
                <TableCell key={k} className={cn('py-1.5 text-xs tabular-nums', k === first ? 'font-medium' : 'text-right', k === 'meets' && 'flex justify-end')}>
                  {formatDecisionValue(k, row[k])}
                  {k === first && row.protected ? (
                    <span className="ml-2 inline-flex items-center gap-0.5 text-[11px] font-normal text-warning-dark dark:text-warning-light">
                      <ShieldAlert className="size-3" />
                      {String(row.protected).replace('_', '-')}
                    </span>
                  ) : null}
                </TableCell>
              ))}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

interface DecisionViewProps {
  run: RuleRun;
  className?: string;
}

/** Why a run decided what it did: verdict, protections, observed values, planned or executed actions. */
export function DecisionView({ run, className }: DecisionViewProps) {
  const [raw, setRaw] = useState(false);
  const meta = OUTCOME_META[run.outcome];
  const Icon = meta.icon;
  const d = run.decision ?? {};
  const reason = reasonText(d.reason);
  const warnings = Array.isArray(d.warnings) ? (d.warnings as string[]) : [];
  const scalars = Object.entries(d).filter(([k, v]) => !HIDDEN_KEYS.has(k) && (v === null || typeof v !== 'object' || (Array.isArray(v) && v.every((x) => typeof x !== 'object'))));
  const tables = TABLE_KEYS.filter((k) => Array.isArray(d[k]) && (d[k] as unknown[]).length > 0 && typeof (d[k] as unknown[])[0] === 'object');
  const nested = Object.entries(d).filter(([k, v]) => !HIDDEN_KEYS.has(k) && v !== null && typeof v === 'object' && !Array.isArray(v));

  return (
    <div className={cn('space-y-4', className)}>
      <div className="flex items-start gap-3">
        <span className={cn('flex size-9 shrink-0 items-center justify-center rounded-lg border border-border bg-muted/50', meta.text)}>
          <Icon className="size-4" />
        </span>
        <div className="min-w-0">
          <p className="flex flex-wrap items-center gap-2 text-sm font-semibold">
            {meta.label}
            {run.dryRun && run.outcome !== 'dry_run' && <StatusBadge variant="dry_run">dry run</StatusBadge>}
          </p>
          <p className="text-xs text-muted-foreground">
            {meta.description} Evaluated {new Date(run.evaluatedAt).toLocaleString()}
            {run.durationMs !== null ? ` in ${run.durationMs < 1000 ? `${run.durationMs} ms` : formatSeconds(Math.round(run.durationMs / 100) / 10)}` : ''}.
          </p>
        </div>
      </div>

      {run.errorMessage && (
        <p className="flex items-start gap-2 rounded-md bg-destructive/10 p-3 text-xs text-destructive">
          <CircleAlert className="mt-px size-3.5 shrink-0" />
          {run.errorMessage}
        </p>
      )}
      {reason && (
        <p className="flex items-start gap-2 rounded-md border border-warning/30 bg-warning/10 p-3 text-xs text-warning-dark dark:text-warning-light">
          <ShieldAlert className="mt-px size-3.5 shrink-0" />
          Held back because {reason}.
        </p>
      )}
      {warnings.map((w) => (
        <p key={w} className="flex items-start gap-2 rounded-md border border-border bg-muted/40 p-3 text-xs text-muted-foreground">
          <CircleAlert className="mt-px size-3.5 shrink-0" />
          {w}
        </p>
      ))}

      {scalars.length > 0 && (
        <dl className="grid gap-1.5 sm:grid-cols-2">
          {scalars.map(([k, v]) => (
            <div key={k} className="flex items-baseline justify-between gap-3 rounded-lg bg-muted/50 px-3 py-2">
              <dt className="text-xs text-muted-foreground">{KEY_LABELS[k] ?? humanize(k)}</dt>
              <dd className="text-right text-xs font-medium tabular-nums">{formatDecisionValue(k, v)}</dd>
            </div>
          ))}
        </dl>
      )}

      {tables.map((k) => (
        <div key={k} className="space-y-1.5">
          <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">{k === 'candidate_victims' ? 'Eviction candidates' : `Observed ${humanize(k).toLowerCase()}`}</p>
          <ObservedTable rows={d[k] as Record<string, unknown>[]} />
        </div>
      ))}

      {nested.map(([k, v]) => (
        <div key={k} className="space-y-1.5">
          <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">{humanize(k)}</p>
          <ObservedTable rows={[v as Record<string, unknown>]} />
        </div>
      ))}

      {run.actions.length > 0 && (
        <div className="space-y-1.5">
          <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">{run.outcome === 'fired' ? 'Actions taken' : 'Actions it would take'}</p>
          <ul className="divide-y divide-border rounded-lg border border-border">
            {run.actions.map((a, i) => (
              <li key={i} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2.5 text-xs">
                <span className="rounded-[5px] bg-foreground/[0.07] px-1.5 py-0.5 font-mono text-[11px] font-medium">{a.action}</span>
                <span className="font-medium">{a.targetName ?? a.targetId ?? humanize(a.targetType)}</span>
                {a.message && <span className="text-muted-foreground">{a.message}</span>}
                <span className="ml-auto flex items-center gap-2">
                  {a.status && <StatusBadge variant={toBadgeVariant(a.status)}>{humanize(a.status)}</StatusBadge>}
                  {a.swapOperationId && (
                    <Link href={`/swaps/${a.swapOperationId}`} className="inline-flex items-center gap-0.5 font-medium text-foreground underline-offset-4 hover:underline">
                      Swap <ArrowUpRight className="size-3" />
                    </Link>
                  )}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div>
        <button type="button" onClick={() => setRaw((r) => !r)} className="inline-flex items-center gap-1 text-[11px] font-medium text-muted-foreground hover:text-foreground" aria-expanded={raw}>
          <ChevronRight className={cn('size-3 transition-transform', raw && 'rotate-90')} />
          Raw decision
        </button>
        {raw && <JsonView value={{ decision: run.decision, actions: run.actions }} className="mt-2" maxHeight={260} />}
      </div>
    </div>
  );
}
