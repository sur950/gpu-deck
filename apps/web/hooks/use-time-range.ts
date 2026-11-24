/**
 * Time range + bucket step for a metrics page, mirrored to the URL
 * (`?range=6h&step=5m`) and re-resolved every 30 s so "last hour" keeps
 * sliding forward while the page is open.
 */

'use client';

import { useEffect, useMemo, useState } from 'react';
import { useUrlState } from '@/hooks/use-url-state';
import { resolveRange } from '@/components/charts/series-utils';
import type { ResolvedRange } from '@/components/charts/series-utils';

const TICK_MS = 30_000;

export interface TimeRangeState extends ResolvedRange {
  rangeId: string;
  stepId: string;
  setRange: (id: string) => void;
  setStep: (step: string) => void;
}

export function useTimeRange(defaultRange = '1h'): TimeRangeState {
  const defaults = useMemo(() => ({ range: defaultRange, step: 'auto' }), [defaultRange]);
  const { params, setParams } = useUrlState(defaults);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), TICK_MS);
    return () => clearInterval(id);
  }, []);

  const resolved = useMemo(() => resolveRange(params.range, params.step, now), [params.range, params.step, now]);

  return {
    ...resolved,
    rangeId: resolved.preset.id,
    stepId: params.step,
    // A new range resets the step to the preset's default bucket.
    setRange: (id: string) => setParams({ range: id, step: 'auto' }),
    setStep: (step: string) => setParams({ step }),
  };
}
