'use client';

/** Date-range state kept in the URL (`preset`, `from`, `to`), as used by the audit log. */

import { useMemo } from 'react';
import { buildDefaultDateRange, computeDateRange, USAGE_PRESETS, type DateRangeValue } from '@/components/shared/DateRangeFilter';

/** Resolve the range once per (preset, from, to) so query keys stay stable. */
export function useRangeFromUrl(preset: string, from: string, to: string): DateRangeValue {
  return useMemo(() => {
    if (preset === 'custom' && from && to) return { preset, from, to, label: 'Custom range' };
    const base = buildDefaultDateRange(preset || 'last_7d', USAGE_PRESETS);
    const r = computeDateRange(preset || 'last_7d');
    return { ...base, from: r.from, to: r.to };
  }, [preset, from, to]);
}
