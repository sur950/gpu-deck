/**
 * Mock layer types.
 *
 * Mock mode (NEXT_PUBLIC_API_MOCKS=1) patches `fetch` so every call the API
 * client makes to NEXT_PUBLIC_API_URL is answered by a registered handler
 * instead of the backend. Handlers return either an envelope result (wrapped
 * as `{success, data, error, meta}` like the real admin API) or a raw body
 * (SSE / text / CSV endpoints).
 */

import type { components } from '@/lib/api/generated/admin';

/** Contract schemas, for fixture typing. */
export type Schemas = components['schemas'];

export type HttpMethod = 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';

export interface MockRequest {
  method: HttpMethod;
  /** Path relative to the API base, e.g. `/metrics/gpu`. */
  path: string;
  /** Path parameters from the handler pattern (`/nodes/:node_id` → `{ node_id }`). */
  params: Record<string, string>;
  query: URLSearchParams;
  /** Parsed JSON body (or the raw string / FormData when not JSON). */
  body: unknown;
  url: URL;
}

export interface MockError {
  code: string;
  message: string;
  details?: unknown;
  field?: string | null;
}

/** Wrapped as the admin API envelope. */
export interface MockEnvelopeResult {
  status?: number;
  data?: unknown;
  meta?: Schemas['ListMeta'] | Record<string, unknown> | null;
  error?: MockError;
  headers?: Record<string, string>;
}

/** Sent as-is (plain text, CSV, event streams). */
export interface MockRawResult {
  raw: BodyInit | null;
  status?: number;
  headers?: Record<string, string>;
}

export type MockResult = MockEnvelopeResult | MockRawResult;

export interface MockHandler {
  method: HttpMethod | '*';
  /** Express-style pattern relative to the API base: `/nodes/:node_id/devices`. */
  path: string;
  handle: (req: MockRequest) => MockResult | Promise<MockResult>;
  /**
   * Read-only stand-in for a resource another feature owns (e.g. the
   * dashboards' `GET /nodes`). Any non-fallback handler that matches the same
   * request wins over it, whatever order the files load in.
   */
  fallback?: boolean;
}

export function isRawResult(result: MockResult): result is MockRawResult {
  return 'raw' in result;
}
