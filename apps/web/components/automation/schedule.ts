/**
 * Schedule math for rules and layouts — timezone and DST aware, no deps.
 *
 * Used by the rule builder (next run preview), the rules list ("next
 * evaluation"), the 24 h layout timeline and the mock evaluator. Times are
 * epoch milliseconds; wall-clock values are interpreted in an IANA zone via
 * `Intl.DateTimeFormat`, so a "08:00 Asia/Kolkata" schedule lands on the
 * right instant whatever the browser's zone is.
 */

export const DAY_KEYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'] as const;
export type DayKey = (typeof DAY_KEYS)[number];

export const DAY_LABELS: Record<DayKey, string> = {
  mon: 'Mon',
  tue: 'Tue',
  wed: 'Wed',
  thu: 'Thu',
  fri: 'Fri',
  sat: 'Sat',
  sun: 'Sun',
};

/** JS weekday (0 = Sunday) → contract day key. */
const WEEKDAY_TO_KEY: DayKey[] = ['sun', 'mon', 'tue', 'wed', 'thu', 'fri', 'sat'];

export interface ZonedParts {
  year: number;
  month: number; // 1-12
  day: number; // 1-31
  hour: number;
  minute: number;
  weekday: number; // 0 = Sunday
}

const formatters = new Map<string, Intl.DateTimeFormat>();

function formatterFor(timeZone: string): Intl.DateTimeFormat {
  let f = formatters.get(timeZone);
  if (!f) {
    f = new Intl.DateTimeFormat('en-US', {
      timeZone,
      hourCycle: 'h23',
      year: 'numeric',
      month: 'numeric',
      day: 'numeric',
      hour: 'numeric',
      minute: 'numeric',
      weekday: 'short',
    });
    formatters.set(timeZone, f);
  }
  return f;
}

/** True when the runtime knows the zone. */
export function isValidTimeZone(timeZone: string): boolean {
  if (!timeZone) return false;
  try {
    formatterFor(timeZone);
    return true;
  } catch {
    return false;
  }
}

const SHORT_WEEKDAY: Record<string, number> = { Sun: 0, Mon: 1, Tue: 2, Wed: 3, Thu: 4, Fri: 5, Sat: 6 };

export function zonedParts(ms: number, timeZone: string): ZonedParts {
  const parts = formatterFor(isValidTimeZone(timeZone) ? timeZone : 'UTC').formatToParts(new Date(ms));
  const get = (type: string) => parts.find((p) => p.type === type)?.value ?? '0';
  return {
    year: Number(get('year')),
    month: Number(get('month')),
    day: Number(get('day')),
    hour: Number(get('hour')) % 24,
    minute: Number(get('minute')),
    weekday: SHORT_WEEKDAY[get('weekday')] ?? 0,
  };
}

/** Offset of `timeZone` from UTC at instant `ms`, in ms (IST → +19 800 000). */
function offsetAt(ms: number, timeZone: string): number {
  const p = zonedParts(ms, timeZone);
  const asUtc = Date.UTC(p.year, p.month - 1, p.day, p.hour, p.minute);
  return asUtc - Math.floor(ms / 60_000) * 60_000;
}

/**
 * The instant at which the wall clock in `timeZone` reads the given time.
 * In a DST gap the time is moved forward by the gap; in an overlap the
 * earlier instant wins (what cron implementations do).
 */
export function zonedToUtc(year: number, month: number, day: number, hour: number, minute: number, timeZone: string): number {
  const guess = Date.UTC(year, month - 1, day, hour, minute);
  // The zone's offsets half a day either side cover both sides of any DST
  // transition on that date; each gives one candidate instant.
  const candidates = [...new Set([-12, 0, 12].map((h) => guess - offsetAt(guess + h * 3_600_000, timeZone)))];
  const matching = candidates.filter((t) => {
    const p = zonedParts(t, timeZone);
    return p.year === year && p.month === month && p.day === day && p.hour === hour && p.minute === minute;
  });
  // Overlap → both match, the earlier wins. Gap → none match; the candidate
  // computed with the pre-transition offset is the wall time shifted forward
  // by the gap, which is the latest candidate.
  return matching.length ? Math.min(...matching) : Math.max(...candidates);
}

export function parseHhMm(value: string): { hour: number; minute: number } | null {
  const m = /^([01]\d|2[0-3]):([0-5]\d)$/.exec(value);
  return m ? { hour: Number(m[1]), minute: Number(m[2]) } : null;
}

/** Calendar date `offsetDays` after the local date of `ms` in `timeZone`. */
function localDate(ms: number, timeZone: string, offsetDays: number): { year: number; month: number; day: number; weekday: number } {
  const p = zonedParts(ms, timeZone);
  const d = new Date(Date.UTC(p.year, p.month - 1, p.day + offsetDays));
  return { year: d.getUTCFullYear(), month: d.getUTCMonth() + 1, day: d.getUTCDate(), weekday: d.getUTCDay() };
}

// ─── Cron (5 fields: minute hour day-of-month month day-of-week) ─────────────

interface CronSpec {
  minutes: Set<number>;
  hours: Set<number>;
  doms: Set<number>;
  months: Set<number>;
  dows: Set<number>;
  domRestricted: boolean;
  dowRestricted: boolean;
}

const DOW_NAMES: Record<string, number> = { sun: 0, mon: 1, tue: 2, wed: 3, thu: 4, fri: 5, sat: 6 };
const MONTH_NAMES: Record<string, number> = { jan: 1, feb: 2, mar: 3, apr: 4, may: 5, jun: 6, jul: 7, aug: 8, sep: 9, oct: 10, nov: 11, dec: 12 };

function parseField(field: string, min: number, max: number, names: Record<string, number> = {}): Set<number> | null {
  const out = new Set<number>();
  const value = (token: string): number | null => {
    const lower = token.toLowerCase();
    if (lower in names) return names[lower];
    if (!/^\d+$/.test(token)) return null;
    return Number(token);
  };
  for (const part of field.split(',')) {
    const [rangePart, stepPart] = part.split('/');
    const step = stepPart === undefined ? 1 : Number(stepPart);
    if (!Number.isInteger(step) || step < 1) return null;
    let lo: number;
    let hi: number;
    if (rangePart === '*') {
      lo = min;
      hi = max;
    } else if (rangePart.includes('-')) {
      const [a, b] = rangePart.split('-');
      const va = value(a);
      const vb = value(b);
      if (va === null || vb === null) return null;
      lo = va;
      hi = vb;
    } else {
      const v = value(rangePart);
      if (v === null) return null;
      lo = v;
      hi = stepPart === undefined ? v : max;
    }
    if (lo < min || hi > max || lo > hi) {
      // Sunday may be written as 7.
      if (!(max === 6 && hi === 7 && lo <= 7)) return null;
    }
    for (let v = lo; v <= hi; v += step) out.add(max === 6 && v === 7 ? 0 : v);
  }
  return out;
}

export function parseCron(expr: string): CronSpec | null {
  const fields = expr.trim().split(/\s+/);
  if (fields.length !== 5) return null;
  const minutes = parseField(fields[0], 0, 59);
  const hours = parseField(fields[1], 0, 23);
  const doms = parseField(fields[2], 1, 31);
  const months = parseField(fields[3], 1, 12, MONTH_NAMES);
  const dows = parseField(fields[4], 0, 6, DOW_NAMES);
  if (!minutes || !hours || !doms || !months || !dows) return null;
  return { minutes, hours, doms, months, dows, domRestricted: fields[2] !== '*', dowRestricted: fields[4] !== '*' };
}

function cronDayMatches(spec: CronSpec, month: number, day: number, weekday: number): boolean {
  if (!spec.months.has(month)) return false;
  const domOk = spec.doms.has(day);
  const dowOk = spec.dows.has(weekday);
  if (spec.domRestricted && spec.dowRestricted) return domOk || dowOk;
  if (spec.domRestricted) return domOk;
  if (spec.dowRestricted) return dowOk;
  return true;
}

// ─── Occurrences ──────────────────────────────────────────────────────────────

export interface ScheduleSpec {
  at?: string | null;
  days?: string[] | null;
  cron?: string | null;
  timezone?: string | null;
}

/** All fire instants in (fromMs, toMs], ascending. Scans at most `maxDays` local days. */
export function occurrencesBetween(spec: ScheduleSpec, fromMs: number, toMs: number, maxDays = 40): number[] {
  const tz = spec.timezone && isValidTimeZone(spec.timezone) ? spec.timezone : 'UTC';
  const out: number[] = [];
  const spanDays = Math.min(maxDays, Math.ceil((toMs - fromMs) / 86_400_000) + 2);
  const cron = spec.cron ? parseCron(spec.cron) : null;
  if (spec.cron && !cron) return out;
  const at = parseHhMm(spec.at ?? '00:00') ?? { hour: 0, minute: 0 };
  const days = new Set((spec.days && spec.days.length ? spec.days : DAY_KEYS) as string[]);
  for (let k = -1; k <= spanDays; k += 1) {
    const date = localDate(fromMs, tz, k);
    if (cron) {
      if (!cronDayMatches(cron, date.month, date.day, date.weekday)) continue;
      for (const hour of [...cron.hours].sort((a, b) => a - b)) {
        for (const minute of [...cron.minutes].sort((a, b) => a - b)) {
          const t = zonedToUtc(date.year, date.month, date.day, hour, minute, tz);
          if (t > fromMs && t <= toMs) out.push(t);
        }
      }
    } else {
      if (!days.has(WEEKDAY_TO_KEY[date.weekday])) continue;
      const t = zonedToUtc(date.year, date.month, date.day, at.hour, at.minute, tz);
      if (t > fromMs && t <= toMs) out.push(t);
    }
  }
  return [...new Set(out)].sort((a, b) => a - b);
}

/** The next `count` fire instants after `fromMs` (within ~5 weeks). */
export function nextOccurrences(spec: ScheduleSpec, fromMs: number, count = 1): number[] {
  return occurrencesBetween(spec, fromMs, fromMs + 35 * 86_400_000).slice(0, count);
}

/** The most recent fire instant at or before `atMs` (within ~5 weeks), or null. */
export function previousOccurrence(spec: ScheduleSpec, atMs: number): number | null {
  const list = occurrencesBetween(spec, atMs - 35 * 86_400_000, atMs);
  return list.length ? list[list.length - 1] : null;
}

/** Whether `ms` falls inside any "HH:MM-HH:MM" window in `timeZone` (windows may wrap midnight). */
export function inTimeWindows(windows: string[], timeZone: string, ms: number): boolean {
  if (!windows.length) return true;
  const p = zonedParts(ms, timeZone);
  const now = p.hour * 60 + p.minute;
  return windows.some((w) => {
    const [a, b] = w.split('-');
    const start = parseHhMm(a);
    const end = parseHhMm(b);
    if (!start || !end) return false;
    const s = start.hour * 60 + start.minute;
    const e = end.hour * 60 + end.minute;
    return s <= e ? now >= s && now < e : now >= s || now < e;
  });
}

/** "weekdays", "every day", "Mon, Wed, Fri". */
export function describeDays(days: string[] | null | undefined): string {
  const set = new Set(days && days.length ? days : DAY_KEYS);
  if (set.size === 7) return 'every day';
  const weekdays: DayKey[] = ['mon', 'tue', 'wed', 'thu', 'fri'];
  if (set.size === 5 && weekdays.every((d) => set.has(d))) return 'on weekdays';
  if (set.size === 2 && set.has('sat') && set.has('sun')) return 'on weekends';
  return `on ${DAY_KEYS.filter((d) => set.has(d)).map((d) => DAY_LABELS[d]).join(', ')}`;
}

/** Short zone label for the browser's zone or an IANA name ("IST", "UTC+5:30"). */
export function zoneAbbreviation(timeZone: string, ms = Date.now()): string {
  try {
    const parts = new Intl.DateTimeFormat('en-US', { timeZone, timeZoneName: 'short' }).formatToParts(new Date(ms));
    return parts.find((p) => p.type === 'timeZoneName')?.value ?? timeZone;
  } catch {
    return timeZone;
  }
}

export function browserTimeZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
  } catch {
    return 'UTC';
  }
}

/** Format an instant as wall-clock time in a zone: "Tue 08:00". */
export function formatInZone(ms: number, timeZone: string, withDay = true): string {
  try {
    return new Intl.DateTimeFormat('en-GB', {
      timeZone,
      weekday: withDay ? 'short' : undefined,
      hour: '2-digit',
      minute: '2-digit',
      hourCycle: 'h23',
    }).format(new Date(ms));
  } catch {
    return new Date(ms).toISOString();
  }
}

/** A short list of IANA zones offered first in pickers (the runtime list follows). */
export const COMMON_TIME_ZONES = [
  'UTC',
  'Asia/Kolkata',
  'Asia/Singapore',
  'Asia/Tokyo',
  'Europe/London',
  'Europe/Berlin',
  'America/New_York',
  'America/Chicago',
  'America/Los_Angeles',
  'Australia/Sydney',
];

export function allTimeZones(): string[] {
  const intl = Intl as typeof Intl & { supportedValuesOf?: (key: string) => string[] };
  const list = typeof intl.supportedValuesOf === 'function' ? intl.supportedValuesOf('timeZone') : [];
  return [...new Set([...COMMON_TIME_ZONES, ...list])];
}
