'use client';

import { createContext, useContext, useEffect, useRef } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useHealthCheck } from '@/hooks/use-health-check';
import { ConnectionBanner, DegradedBanner, criticalServicesAffected } from '@/components/shared/StatusBanners';
import type { HealthData } from '@/types/health';

const HealthContext = createContext<HealthData>({ status: 'unknown' });

/** The latest backend health check (per-service detail in `checks`). */
export function useBackendHealth(): HealthData {
  return useContext(HealthContext);
}

interface HealthCheckProviderProps {
  children: React.ReactNode;
}

export function HealthCheckProvider({ children }: HealthCheckProviderProps) {
  const { health, recheck } = useHealthCheck();
  const queryClient = useQueryClient();
  const wasDown = useRef(false);

  // Back from an outage: refetch everything that failed while the backend was
  // away, so the page fills in without a reload.
  useEffect(() => {
    if (health.status === 'down') {
      wasDown.current = true;
    } else if (wasDown.current && health.status !== 'unknown') {
      wasDown.current = false;
      void queryClient.invalidateQueries();
    }
  }, [health.status, queryClient]);

  // The app is never replaced by an outage screen.
  // 'down'     = a bar above the page; it keeps retrying and the page stays usable.
  // 'degraded' = a bar only when a critical service (database, Redis) is affected;
  //              storage-only degradation is shown in the dashboard's System Status card.
  return (
    <HealthContext.Provider value={health}>
      {health.status === 'down' && <ConnectionBanner health={health} onRetry={recheck} />}
      {health.status === 'degraded' && criticalServicesAffected(health).length > 0 && (
        <DegradedBanner health={health} />
      )}
      {children}
    </HealthContext.Provider>
  );
}
