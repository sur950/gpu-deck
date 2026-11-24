import { describe, expect, it } from 'vitest';
import {
  describeDays,
  inTimeWindows,
  nextOccurrences,
  occurrencesBetween,
  parseCron,
  parseHhMm,
  previousOccurrence,
  zonedParts,
  zonedToUtc,
} from '@/components/automation/schedule';

const iso = (ms: number) => new Date(ms).toISOString();

describe('zoned time conversion', () => {
  it('maps wall-clock time in a zone to the right instant', () => {
    // 08:00 in Kolkata (UTC+5:30) is 02:30 UTC.
    expect(iso(zonedToUtc(2026, 11, 17, 8, 0, 'Asia/Kolkata'))).toBe('2026-11-17T02:30:00.000Z');
    expect(zonedParts(Date.parse('2026-11-17T02:30:00Z'), 'Asia/Kolkata')).toMatchObject({ year: 2026, month: 11, day: 17, hour: 8, minute: 0, weekday: 2 });
  });

  it('moves a time inside a DST gap forward and picks the earlier instant in an overlap', () => {
    // US spring forward 2026-03-08: 02:30 does not exist in New York → 03:30 EDT (07:30 UTC).
    expect(iso(zonedToUtc(2026, 3, 8, 2, 30, 'America/New_York'))).toBe('2026-03-08T07:30:00.000Z');
    // Fall back 2026-11-01: 01:30 happens twice → the first (EDT, 05:30 UTC).
    expect(iso(zonedToUtc(2026, 11, 1, 1, 30, 'America/New_York'))).toBe('2026-11-01T05:30:00.000Z');
    // East of UTC: Berlin 2026-03-29 02:30 (gap) → 03:30 CEST; 2026-10-25 02:30 (overlap) → the CEST one.
    expect(iso(zonedToUtc(2026, 3, 29, 2, 30, 'Europe/Berlin'))).toBe('2026-03-29T01:30:00.000Z');
    expect(iso(zonedToUtc(2026, 10, 25, 2, 30, 'Europe/Berlin'))).toBe('2026-10-25T00:30:00.000Z');
  });
});

describe('parsers', () => {
  it('parses HH:MM', () => {
    expect(parseHhMm('08:05')).toEqual({ hour: 8, minute: 5 });
    expect(parseHhMm('24:00')).toBeNull();
    expect(parseHhMm('8:00')).toBeNull();
  });

  it('parses 5-field cron with names, ranges, steps and Sunday as 7', () => {
    const spec = parseCron('*/15 9-17 * jan-mar mon-fri');
    expect(spec?.minutes).toEqual(new Set([0, 15, 30, 45]));
    expect(spec?.hours.size).toBe(9);
    expect(spec?.months).toEqual(new Set([1, 2, 3]));
    expect(spec?.dows).toEqual(new Set([1, 2, 3, 4, 5]));
    expect(parseCron('0 0 * * 7')?.dows).toEqual(new Set([0]));
    expect(parseCron('0 0 * *')).toBeNull();
    expect(parseCron('61 0 * * *')).toBeNull();
  });
});

describe('occurrences', () => {
  // Tue 2026-11-17 12:00 UTC
  const from = Date.parse('2026-11-17T12:00:00Z');

  it('finds the next weekday fire time in the rule zone', () => {
    const [next] = nextOccurrences({ at: '08:00', days: ['mon', 'tue', 'wed', 'thu', 'fri'], timezone: 'Asia/Kolkata' }, from);
    // 08:00 IST on Wed 18 Nov = 02:30 UTC.
    expect(iso(next)).toBe('2026-11-18T02:30:00.000Z');
  });

  it('skips days that are not selected', () => {
    const list = nextOccurrences({ at: '20:00', days: ['sat'], timezone: 'UTC' }, from, 2);
    expect(list.map(iso)).toEqual(['2026-11-21T20:00:00.000Z', '2026-11-28T20:00:00.000Z']);
  });

  it('expands cron expressions and the previous occurrence', () => {
    const list = occurrencesBetween({ cron: '0 */6 * * *', timezone: 'UTC' }, from, from + 86_400_000);
    expect(list.map(iso)).toEqual(['2026-11-17T18:00:00.000Z', '2026-11-18T00:00:00.000Z', '2026-11-18T06:00:00.000Z', '2026-11-18T12:00:00.000Z']);
    expect(iso(previousOccurrence({ cron: '30 7 * * *', timezone: 'UTC' }, from) ?? 0)).toBe('2026-11-17T07:30:00.000Z');
  });

  it('returns nothing for an invalid cron', () => {
    expect(occurrencesBetween({ cron: 'nope', timezone: 'UTC' }, from, from + 86_400_000)).toEqual([]);
  });
});

describe('time windows and day labels', () => {
  it('handles windows that wrap midnight', () => {
    const at = (h: number) => Date.parse(`2026-11-17T${String(h).padStart(2, '0')}:00:00Z`);
    expect(inTimeWindows(['22:00-06:00'], 'UTC', at(23))).toBe(true);
    expect(inTimeWindows(['22:00-06:00'], 'UTC', at(5))).toBe(true);
    expect(inTimeWindows(['22:00-06:00'], 'UTC', at(12))).toBe(false);
    expect(inTimeWindows([], 'UTC', at(12))).toBe(true);
  });

  it('describes day sets', () => {
    expect(describeDays([])).toBe('every day');
    expect(describeDays(['mon', 'tue', 'wed', 'thu', 'fri'])).toBe('on weekdays');
    expect(describeDays(['sat', 'sun'])).toBe('on weekends');
    expect(describeDays(['wed', 'mon'])).toBe('on Mon, Wed');
  });
});
