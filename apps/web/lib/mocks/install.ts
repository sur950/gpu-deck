/**
 * Fetch interception for mock mode.
 *
 * Patches `globalThis.fetch` once per page (guarded against HMR re-runs).
 * Only requests to the admin API base (NEXT_PUBLIC_API_URL) are intercepted;
 * everything else — fonts, Next.js internals, other origins — goes to the
 * real `fetch`.
 *
 * Behaviour mirrors the backend closely enough for the UI to be exercised
 * end-to-end: the envelope shape, 401 for protected routes without the
 * session cookie, 404 for unknown routes, a little latency, and raw bodies
 * for stream/text endpoints.
 */

import { getGatewayUrl } from '@/utils/gateway-url';
import { matchMockHandler } from '@/lib/mocks/registry';
import { isRawResult } from '@/lib/mocks/types';
import type { HttpMethod, MockRequest, MockResult } from '@/lib/mocks/types';
import { hasMockSession } from '@/lib/mocks/session';

const INSTALLED_FLAG = '__gpuDeckMockFetchInstalled';

/** Endpoints that answer without a session (same list as the contract). */
const PUBLIC_PATH_PREFIXES = ['/auth/login', '/health', '/branding', '/pki/ca', '/pki/crl', '/metrics/prometheus'];

function baseLatencyMs(): number {
  const raw = Number(process.env.NEXT_PUBLIC_API_MOCK_LATENCY_MS ?? 160);
  return Number.isFinite(raw) && raw >= 0 ? raw : 160;
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function jitter(ms: number): number {
  return Math.round(ms * (0.7 + Math.random() * 0.6));
}

function toUrl(input: RequestInfo | URL): URL | null {
  try {
    if (typeof input === 'string') return new URL(input, window.location.origin);
    if (input instanceof URL) return input;
    return new URL(input.url, window.location.origin);
  } catch {
    return null;
  }
}

async function readBody(input: RequestInfo | URL, init?: RequestInit): Promise<unknown> {
  const raw = init?.body ?? (input instanceof Request ? await input.clone().text() : undefined);
  if (raw === undefined || raw === null) return undefined;
  if (typeof raw === 'string') {
    try {
      return JSON.parse(raw);
    } catch {
      return raw;
    }
  }
  return raw;
}

function envelope(result: MockResult): Response {
  if (isRawResult(result)) {
    return new Response(result.raw, {
      status: result.status ?? 200,
      headers: { 'content-type': 'text/plain; charset=utf-8', ...(result.headers ?? {}) },
    });
  }
  const status = result.status ?? (result.error ? 400 : 200);
  const body = result.error
    ? { success: false, data: null, error: result.error, meta: null }
    : { success: true, data: result.data ?? null, error: null, meta: result.meta ?? null };
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json', ...(result.headers ?? {}) },
  });
}

function isPublic(path: string): boolean {
  return PUBLIC_PATH_PREFIXES.some((p) => path === p || path.startsWith(`${p}/`));
}

/**
 * Patch `fetch`. `loadHandlers` imports the handler modules (they register
 * themselves); intercepted requests wait for it, so the patch can be
 * installed synchronously before the fixtures have downloaded.
 */
export function installMockFetch(loadHandlers: () => Promise<unknown>): void {
  if (typeof window === 'undefined') return;
  const g = globalThis as typeof globalThis & { [INSTALLED_FLAG]?: boolean };
  if (g[INSTALLED_FLAG]) return;

  const realFetch = g.fetch.bind(globalThis);
  const base = new URL(getGatewayUrl(), window.location.origin);
  const basePath = base.pathname.replace(/\/$/, '');
  // Start downloading the handlers now; intercepted requests wait for them.
  const ready = loadHandlers();

  g.fetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url = toUrl(input);
    if (!url || url.origin !== base.origin || !url.pathname.startsWith(basePath)) {
      return realFetch(input, init);
    }

    const path = url.pathname.slice(basePath.length) || '/';
    const method = (init?.method ?? (input instanceof Request ? input.method : 'GET')).toUpperCase() as HttpMethod;

    await Promise.all([ready, sleep(jitter(baseLatencyMs()))]);

    if (!isPublic(path) && !hasMockSession()) {
      return envelope({ status: 401, error: { code: 'UNAUTHENTICATED', message: 'Session expired or missing' } });
    }

    const match = matchMockHandler(method, path);
    if (!match) {
      console.warn(`[mocks] no handler for ${method} ${path}`);
      return envelope({ status: 404, error: { code: 'NOT_FOUND', message: `No mock for ${method} ${path}` } });
    }

    const req: MockRequest = {
      method,
      path,
      params: match.params,
      query: url.searchParams,
      body: await readBody(input, init),
      url,
    };

    try {
      const result = await match.handler.handle(req);
      return envelope(result);
    } catch (err) {
      console.error(`[mocks] handler for ${method} ${path} threw`, err);
      return envelope({ status: 500, error: { code: 'INTERNAL_ERROR', message: 'Mock handler failed' } });
    }
  };

  g[INSTALLED_FLAG] = true;
}
