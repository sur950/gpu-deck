'use client';

import type { HealthData } from '@/types/health';

// Services that affect what the admin can do right now. Keys match the
// `checks` object of GET /api/v1/health.
const CRITICAL_SERVICES: Record<string, string> = {
  database: 'Database',
  redis:    'Cache / Redis',
};

/** Labels of critical services the last health check reported as not healthy. */
export function criticalServicesAffected(health: HealthData): string[] {
  const checks = health.checks;
  if (!checks) return [];
  return Object.entries(CRITICAL_SERVICES)
    .filter(([key]) => {
      const check = checks[key as keyof typeof checks] as { status?: string } | undefined;
      return check?.status !== undefined && check.status !== 'healthy';
    })
    .map(([, label]) => label);
}

// ── Connection bar — the backend is down; the page underneath stays usable ───
export function ConnectionBanner({ health, onRetry }: { health: HealthData; onRetry: () => void }) {
  const affected = criticalServicesAffected(health);
  const message = affected.length > 0
    ? `${affected.join(' and ')} not responding.`
    : "Can't reach the control plane.";

  return (
    <div
      role="status"
      aria-live="polite"
      className="fixed top-0 left-0 right-0 z-[9998] flex flex-wrap items-center justify-center gap-x-2 gap-y-1 bg-error/10 border-b border-error/30 px-4 py-2 text-sm text-error-dark dark:text-error"
    >
      <span className="inline-block h-2 w-2 rounded-full bg-error flex-shrink-0 animate-pulse" />
      <span className="font-medium">{message}</span>
      <span className="text-muted-foreground">Retrying automatically.</span>
      <button
        type="button"
        onClick={onRetry}
        className="font-medium underline underline-offset-2 hover:no-underline focus-visible:outline-2 focus-visible:outline-offset-2"
      >
        Retry now
      </button>
    </div>
  );
}

// ── Degraded bar — a critical service is struggling; single row, non-dismissable ─
export function DegradedBanner({ health }: { health: HealthData }) {
  const affected = criticalServicesAffected(health);

  return (
    <div
      role="status"
      aria-live="polite"
      className="fixed top-0 left-0 right-0 z-[9998] flex items-center justify-center gap-2 bg-warning/10 border-b border-warning/30 px-4 py-2 text-sm text-warning-dark dark:text-warning"
    >
      <span className="inline-block h-2 w-2 rounded-full bg-warning flex-shrink-0 animate-pulse" />
      <span className="font-medium">Partial degradation detected.</span>
      <span className="text-muted-foreground">
        Affected: {affected.join(', ')}. Some features may be slow or limited.
      </span>
    </div>
  );
}
