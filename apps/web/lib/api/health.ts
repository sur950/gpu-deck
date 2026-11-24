import type { BackendStatus, HealthData } from '@/types/health';
import { getApiOrigin } from '@/utils/gateway-url';

function getHealthUrl(): string {
  return `${getApiOrigin()}/api/v1/health`;
}

/** Services whose failure is reported as "degraded" even if the backend says healthy. */
const CRITICAL_CHECKS = ['database', 'redis'];

function mapStatus(raw: string | undefined): BackendStatus {
  if (raw === 'healthy')   return 'healthy';
  if (raw === 'degraded')  return 'degraded';
  if (raw === 'unhealthy') return 'down';
  return 'down';
}

export async function checkBackendHealth(): Promise<HealthData> {
  try {
    const res = await fetch(getHealthUrl(), {
      method: 'GET',
      cache: 'no-store',
      signal: AbortSignal.timeout(5000),
    });

    if (!res.ok) {
      return { status: 'down' };
    }

    const json = await res.json();
    const data = json?.data;

    if (!data) return { status: 'down' };

    let status = mapStatus(data.status);

    // If the overall status is reported healthy but a service is unhealthy,
    // mark as degraded for defensive display.
    if (status === 'healthy' && data.checks) {
      const serviceDown = CRITICAL_CHECKS.some(
        key => data.checks[key]?.status === 'unhealthy'
      );
      if (serviceDown) status = 'degraded';
    }

    return {
      status,
      version: data.version,
      checks:  data.checks,
    };
  } catch {
    return { status: 'down' };
  }
}
