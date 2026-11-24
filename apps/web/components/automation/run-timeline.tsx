'use client';

import { cn } from '@/lib/utils';
import type { RuleRun } from '@/hooks/api/automation-api';
import { OUTCOME_META, summarizeRun } from '@/components/automation/run-meta';

function dayLabel(ms: number): string {
  const d = new Date(ms);
  const today = new Date();
  const start = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const diff = Math.round((start(today) - start(d)) / 86_400_000);
  if (diff === 0) return 'Today';
  if (diff === 1) return 'Yesterday';
  return d.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' });
}

function clock(ms: number): string {
  return new Date(ms).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23' });
}

interface RunTimelineProps {
  runs: RuleRun[];
  selectedId: string | null;
  onSelect: (run: RuleRun) => void;
  className?: string;
}

/**
 * Run history as a vertical timeline grouped by day: an outcome-coloured
 * node on a hairline spine, time, verdict and what the run saw or did.
 * Acting runs carry a filled node; quiet ones a hollow ring.
 */
export function RunTimeline({ runs, selectedId, onSelect, className }: RunTimelineProps) {
  const groups: { label: string; runs: RuleRun[] }[] = [];
  for (const run of runs) {
    const label = dayLabel(Date.parse(run.evaluatedAt));
    const last = groups[groups.length - 1];
    if (last && last.label === label) last.runs.push(run);
    else groups.push({ label, runs: [run] });
  }
  return (
    <div className={cn('space-y-5', className)}>
      {groups.map((g) => (
        <section key={g.label} aria-label={g.label}>
          <p className="sticky top-0 z-[1] mb-1 bg-card/95 py-1 text-[11px] font-semibold uppercase tracking-[0.08em] text-muted-foreground backdrop-blur">{g.label}</p>
          <ol className="relative">
            <span aria-hidden className="absolute bottom-3 left-[7px] top-3 w-px bg-border" />
            {g.runs.map((run) => {
              const meta = OUTCOME_META[run.outcome];
              const acted = run.outcome === 'fired' || run.outcome === 'dry_run' || run.outcome === 'error';
              const selected = run.id === selectedId;
              return (
                <li key={run.id}>
                  <button
                    type="button"
                    onClick={() => onSelect(run)}
                    aria-current={selected ? 'true' : undefined}
                    className={cn(
                      'group/run relative flex w-full items-start gap-3 rounded-lg py-2 pl-0 pr-2 text-left outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring/30',
                      selected ? 'bg-muted/70' : 'hover:bg-muted/40',
                    )}
                  >
                    <span className="relative z-[1] mt-1 flex size-[15px] shrink-0 items-center justify-center rounded-full bg-card">
                      <span className={cn('size-[9px] rounded-full', run.outcome === 'no_match' ? 'border-[1.5px] border-muted-foreground/45 bg-card' : meta.dot)} />
                      {run.outcome === 'fired' && <span className="absolute inset-0 rounded-full ring-2 ring-success/25" />}
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="flex flex-wrap items-baseline gap-x-2">
                        <span className="font-mono text-[11px] tabular-nums text-muted-foreground">{clock(Date.parse(run.evaluatedAt))}</span>
                        <span className={cn('text-xs font-semibold', meta.text)}>{meta.short}</span>
                        {run.dryRun && run.outcome !== 'dry_run' && <span className="text-[10px] font-medium uppercase tracking-wide text-info-dark dark:text-info-light">dry run</span>}
                      </span>
                      <span className={cn('mt-0.5 block text-xs leading-relaxed', acted ? 'text-foreground/85' : 'text-muted-foreground', !selected && 'line-clamp-2')}>{summarizeRun(run)}</span>
                    </span>
                  </button>
                </li>
              );
            })}
          </ol>
        </section>
      ))}
    </div>
  );
}

interface OutcomeStripProps {
  runs: RuleRun[];
  fromMs: number;
  toMs: number;
  selectedId?: string | null;
  onSelect?: (run: RuleRun) => void;
  className?: string;
}

/** The last N hours as a strip: one tick per run, height and colour by outcome. */
export function OutcomeStrip({ runs, fromMs, toMs, selectedId, onSelect, className }: OutcomeStripProps) {
  const span = Math.max(1, toMs - fromMs);
  const hours = Math.round(span / 3_600_000);
  const marks = [0, 0.25, 0.5, 0.75, 1];
  return (
    <div className={cn('space-y-1.5', className)}>
      <div className="relative h-9 overflow-hidden rounded-md border border-border bg-muted/30" role="list" aria-label={`Rule runs over the last ${hours} hours`}>
        {marks.slice(1, -1).map((m) => (
          <span key={m} aria-hidden className="absolute inset-y-0 w-px bg-border/70" style={{ left: `${m * 100}%` }} />
        ))}
        {runs
          .filter((r) => {
            const t = Date.parse(r.evaluatedAt);
            return t >= fromMs && t <= toMs;
          })
          .sort((a, b) => (a.outcome === 'no_match' ? -1 : 0) - (b.outcome === 'no_match' ? -1 : 0))
          .map((r) => {
            const left = ((Date.parse(r.evaluatedAt) - fromMs) / span) * 100;
            const meta = OUTCOME_META[r.outcome];
            const tall = r.outcome !== 'no_match';
            return (
              <button
                key={r.id}
                type="button"
                role="listitem"
                title={`${new Date(r.evaluatedAt).toLocaleString()} — ${meta.label}`}
                aria-label={`${meta.label} at ${new Date(r.evaluatedAt).toLocaleTimeString()}`}
                onClick={() => onSelect?.(r)}
                className={cn(
                  'absolute bottom-1 w-[3px] -translate-x-1/2 rounded-full outline-none transition-transform hover:scale-y-110 focus-visible:ring-2 focus-visible:ring-ring/40',
                  meta.dot,
                  tall ? 'top-1' : 'top-[60%] opacity-70',
                  r.id === selectedId && 'w-[5px] ring-2 ring-foreground/40',
                )}
                style={{ left: `${left}%` }}
              />
            );
          })}
      </div>
      <div className="flex justify-between font-mono text-[10px] tabular-nums text-muted-foreground">
        {marks.map((m) => (
          <span key={m}>{m === 1 ? 'now' : `−${Math.round(hours * (1 - m))}h`}</span>
        ))}
      </div>
    </div>
  );
}
