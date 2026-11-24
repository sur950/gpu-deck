/**
 * Connection-lost signal.
 *
 * The health check polls every few minutes while the backend is healthy, so on
 * its own it would notice an outage late. Any API call that cannot reach the
 * backend fires this event, and the health check re-checks right away.
 */

export const CONNECTION_LOST_EVENT = 'gpu-deck:connection-lost';

export function reportConnectionLost(): void {
  if (typeof window !== 'undefined') {
    window.dispatchEvent(new Event(CONNECTION_LOST_EVENT));
  }
}
