'use client';

import Link from 'next/link';
import { cn } from '@/lib/utils';
import type { Layout, Rule } from '@/hooks/api/automation-api';
import { occurrencesBetween, previousOccurrence, zonedParts, zonedToUtc, zoneAbbreviation, type ScheduleSpec } from '@/components/automation/schedule';

interface Switch {
  at: number;
  layoutId: string;
  ruleId: string;
  ruleName: string;
}

interface LayoutTimelineProps {
  rules: Rule[];
  layouts: Layout[];
  colors: Map<string, string>;
  timeZone: string;
  now: number;
}

function spec(rule: Rule): ScheduleSpec & { layout_id?: string } {
  return rule.params as ScheduleSpec & { layout_id?: string };
}

/**
 * Today (00:00–24:00 in `timeZone`) as one band: which layout is in force
 * when, a tick at every scheduled switch, and a "now" needle.
 */
export function LayoutTimeline({ rules, layouts, colors, timeZone, now }: LayoutTimelineProps) {
  const p = zonedParts(now, timeZone);
  const start = zonedToUtc(p.year, p.month, p.day, 0, 0, timeZone);
  const next = new Date(Date.UTC(p.year, p.month - 1, p.day + 1));
  const end = zonedToUtc(next.getUTCFullYear(), next.getUTCMonth() + 1, next.getUTCDate(), 0, 0, timeZone);
  const span = end - start;
  const schedules = rules.filter((r) => r.enabled && r.type === 'time_schedule' && spec(r).layout_id);

  const switches: Switch[] = schedules
    .flatMap((r) => occurrencesBetween(spec(r), start - 1, end - 1).map((at) => ({ at, layoutId: spec(r).layout_id as string, ruleId: r.id, ruleName: r.name })))
    .sort((a, b) => a.at - b.at);

  // Which layout is in force at 00:00: the latest switch before the day started.
  const before = schedules
    .map((r) => ({ at: previousOccurrence(spec(r), start - 1), layoutId: spec(r).layout_id as string }))
    .filter((x): x is { at: number; layoutId: string } => x.at !== null)
    .sort((a, b) => b.at - a.at)[0];
  const initial = before?.layoutId ?? layouts.find((l) => l.isActive)?.id ?? null;

  const segments: { from: number; to: number; layoutId: string | null }[] = [];
  let cursor = start;
  let current = initial;
  for (const s of switches) {
    if (s.at > cursor) segments.push({ from: cursor, to: s.at, layoutId: current });
    cursor = s.at;
    current = s.layoutId;
  }
  segments.push({ from: cursor, to: end, layoutId: current });

  const nameOf = (id: string | null) => layouts.find((l) => l.id === id)?.name ?? (id ? 'Unknown layout' : 'No layout');
  const pct = (t: number) => ((t - start) / span) * 100;
  const nowPct = pct(now);
  const hours = [0, 3, 6, 9, 12, 15, 18, 21, 24];
  const fmt = (t: number) => new Intl.DateTimeFormat('en-GB', { timeZone, hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).format(new Date(t));

  if (!schedules.length) {
    return (
      <div className="rounded-lg border border-dashed border-border px-4 py-6 text-center text-xs text-muted-foreground">
        No time-schedule rules are enabled, so layouts only change when you apply one.{' '}
        <Link href="/rules/new?type=time_schedule" className="font-medium text-foreground underline-offset-4 hover:underline">
          Schedule a switch
        </Link>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {/* switch labels */}
      <div className="relative h-9">
        {switches.map((s) => {
          const left = pct(s.at);
          const alignRight = left > 82;
          return (
            <div key={`${s.ruleId}-${s.at}`} className={cn('absolute bottom-0 flex flex-col', alignRight ? 'items-end' : 'items-start')} style={alignRight ? { right: `${100 - left}%` } : { left: `${left}%` }}>
              <span className="whitespace-nowrap text-[11px] font-semibold tabular-nums">{fmt(s.at)}</span>
              <span className="whitespace-nowrap text-[10px] text-muted-foreground">→ {nameOf(s.layoutId)}</span>
            </div>
          );
        })}
      </div>
      {/* band */}
      <div className="relative">
        <div className="flex h-10 overflow-hidden rounded-lg border border-border">
          {segments.map((seg, i) => {
            const color = seg.layoutId ? colors.get(seg.layoutId) : undefined;
            const width = ((seg.to - seg.from) / span) * 100;
            return (
              <div
                key={i}
                className="relative flex h-full items-center overflow-hidden px-2"
                style={{ width: `${width}%`, backgroundColor: color ? `color-mix(in srgb, ${color} 18%, transparent)` : undefined }}
                title={`${nameOf(seg.layoutId)} · ${fmt(seg.from)}–${seg.to === end ? '24:00' : fmt(seg.to)}`}
              >
                <span className="absolute inset-y-0 left-0 w-[3px]" style={{ backgroundColor: color ?? 'rgb(var(--border))' }} />
                {width > 9 && (
                  <span className="truncate pl-1.5 text-xs font-semibold" style={{ color: color ?? undefined }}>
                    {nameOf(seg.layoutId)}
                  </span>
                )}
              </div>
            );
          })}
        </div>
        {nowPct >= 0 && nowPct <= 100 && (
          <div className="pointer-events-none absolute -bottom-1 -top-1 w-px bg-foreground" style={{ left: `${nowPct}%` }} aria-hidden>
            <span className="absolute -top-1.5 left-1/2 size-2 -translate-x-1/2 rounded-full bg-foreground ring-2 ring-card" />
          </div>
        )}
      </div>
      {/* hour axis */}
      <div className="relative h-4 font-mono text-[10px] tabular-nums text-muted-foreground">
        {hours.map((h) => (
          <span key={h} className={cn('absolute', h === 0 ? 'left-0' : h === 24 ? 'right-0' : '-translate-x-1/2')} style={h === 0 || h === 24 ? undefined : { left: `${(h / 24) * 100}%` }}>
            {String(h).padStart(2, '0')}:00
          </span>
        ))}
      </div>
      <p className="text-[11px] text-muted-foreground">
        Today in {timeZone} ({zoneAbbreviation(timeZone, now)}) · now {fmt(now)}
      </p>
    </div>
  );
}
