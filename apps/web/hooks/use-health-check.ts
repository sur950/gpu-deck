'use client';

import { useState, useEffect, useRef, useCallback } from 'react';
import { checkBackendHealth } from '@/lib/api/health';
import { CONNECTION_LOST_EVENT } from '@/lib/api/connection';
import type { BackendStatus, HealthData } from '@/types/health';

const POLL_HEALTHY_MS   = 300_000;  // 5 min when healthy
const POLL_DEGRADED_MS  =  60_000;  // 60 s when degraded
const POLL_DOWN_MS      =  10_000;  // 10 s when down — the page stays usable, so notice recovery quickly

// One failed or slow check (a network blip, a cold backend) must not be
// reported as down. Re-check quickly and only report down once it fails this
// many times in a row.
const DOWN_CONFIRM_CHECKS = 3;
const DOWN_RECHECK_MS     = 5_000;

function pollInterval(status: BackendStatus): number {
  if (status === 'down')     return POLL_DOWN_MS;
  if (status === 'degraded') return POLL_DEGRADED_MS;
  return POLL_HEALTHY_MS;
}

const INITIAL: HealthData = { status: 'unknown' };

export interface HealthCheck {
  health: HealthData;
  /** Check now instead of waiting for the next poll (e.g. a "Retry now" button). */
  recheck: () => void;
}

export function useHealthCheck(): HealthCheck {
  const [health, setHealth] = useState<HealthData>(INITIAL);
  const timerRef   = useRef<ReturnType<typeof setTimeout> | null>(null);
  const healthRef  = useRef<HealthData>(INITIAL);
  const recheckRef = useRef<() => void>(() => {});

  useEffect(() => {
    let cancelled = false;
    let running = false;
    let consecutiveDown = 0;

    async function tick() {
      running = true;
      const result = await checkBackendHealth();
      running = false;
      if (cancelled) return;

      consecutiveDown = result.status === 'down' ? consecutiveDown + 1 : 0;
      if (
        result.status === 'down' &&
        healthRef.current.status !== 'down' &&
        consecutiveDown < DOWN_CONFIRM_CHECKS
      ) {
        timerRef.current = setTimeout(tick, DOWN_RECHECK_MS);
        return;
      }

      if (result.status !== healthRef.current.status) {
        healthRef.current = result;
        setHealth(result);
      }

      timerRef.current = setTimeout(tick, pollInterval(result.status));
    }

    function checkNow() {
      if (cancelled || running) return;
      if (timerRef.current) clearTimeout(timerRef.current);
      tick();
    }

    // An API call failed to reach the backend. Check now rather than at the
    // next poll — but not while a down confirmation is already counting, so a
    // burst of failed calls can't confirm an outage in under a second.
    function onConnectionLost() {
      if (consecutiveDown === 0 && healthRef.current.status !== 'down') checkNow();
    }

    recheckRef.current = checkNow;
    window.addEventListener(CONNECTION_LOST_EVENT, onConnectionLost);
    tick();

    return () => {
      cancelled = true;
      window.removeEventListener(CONNECTION_LOST_EVENT, onConnectionLost);
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, []);

  const recheck = useCallback(() => recheckRef.current(), []);
  return { health, recheck };
}
