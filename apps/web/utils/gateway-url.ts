/**
 * Returns the base URL of the GPU Deck admin API (including `/api/v1`).
 *
 * Default (the published web image): the UI's own origin + `/api/v1`. The image's gateway
 * (server/gateway.mjs) forwards `/api/*` to the admin API named by GPUDECK_API_INTERNAL_URL at
 * runtime, so nothing about the deployment is compiled into the bundle and one image works on
 * any hostname. NEXT_PUBLIC_API_URL (build time) points the browser at a different origin
 * instead, e.g. `pnpm dev` talking to the admin API on :8180.
 */

const PLACEHOLDER_API_URL = 'http://localhost:8180/api/v1';

export function getGatewayUrl(): string {
  const env = process.env.NEXT_PUBLIC_API_URL;
  if (env) return env.replace(/\/$/, '');
  if (typeof window !== 'undefined') return `${window.location.origin}/api/v1`;
  return PLACEHOLDER_API_URL;
}

/**
 * Origin of the admin API without a trailing slash or `/api/v1`, for routes
 * that live outside the versioned prefix (e.g. `/metrics`).
 */
export function getApiOrigin(): string {
  return getGatewayUrl().replace(/\/+$/, '').replace(/\/api\/v1$/, '');
}
