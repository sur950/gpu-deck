/**
 * Base HTTP client for all API modules.
 * Handles the backend envelope, credential cookies, the CSRF header, and
 * typed error classes.
 *
 * Every failed response — 401 included — is parsed before it is turned into
 * an error, so the backend's own message ("Invalid username or password",
 * the lockout notice, "CSRF token missing", a 409 reason…) reaches the UI.
 */

import { getGatewayUrl } from '@/utils/gateway-url';
import { reportConnectionLost } from '@/lib/api/connection';
import { CSRF_HEADER, getCsrfToken } from '@/lib/api/csrf';
import type { components } from '@/lib/api/generated/admin';

export type ListMeta = components['schemas']['ListMeta'];

// ─── Error classes ────────────────────────────────────────────────────────────

/** Thrown when the backend returns HTTP 401 (bad credentials, locked account, expired session). */
export class AuthError extends Error {
  readonly status = 401;
  constructor(
    message = 'Unauthorized',
    readonly code?: string,
  ) {
    super(message);
    this.name = 'AuthError';
  }
}

/** Thrown when the backend returns HTTP 403 (authenticated but refused — CSRF, IP rule). */
export class ForbiddenError extends Error {
  readonly status = 403;
  constructor(
    message = 'You do not have permission to access this resource.',
    readonly code?: string,
  ) {
    super(message);
    this.name = 'ForbiddenError';
  }
}

/** Thrown for any other non-2xx response from the backend. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    readonly code?: string,
    readonly details?: unknown,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

/**
 * Thrown when the backend is completely unreachable (network down, CORS failure,
 * connection refused, DNS failure, etc.). Distinct from HTTP errors so callers
 * can treat "backend is down" differently from "bad credentials".
 */
export class NetworkError extends Error {
  constructor(cause?: unknown) {
    super('Backend is unreachable');
    this.name = 'NetworkError';
    if (cause) this.cause = cause;
  }
}

// ─── Error body parsing ───────────────────────────────────────────────────────

export interface ErrorBody {
  message?: string;
  code?: string;
  details?: unknown;
}

/**
 * Read the message out of whatever error body the backend (or the edge in
 * front of it) sent: the admin envelope `{error: {code, message, details}}`,
 * a bare `{error: "…"}`, `{success: false, message}`, or FastAPI's
 * `{detail: "…" | [{msg}]}`.
 */
export function readErrorBody(parsed: unknown): ErrorBody {
  if (!parsed || typeof parsed !== 'object') return {};
  const body = parsed as Record<string, unknown>;
  const err = body.error;
  if (typeof err === 'string' && err) return { message: err };
  if (err && typeof err === 'object') {
    const e = err as Record<string, unknown>;
    return {
      message: typeof e.message === 'string' && e.message ? e.message : undefined,
      code: typeof e.code === 'string' ? e.code : undefined,
      details: e.details,
    };
  }
  if (body.success === false && typeof body.message === 'string' && body.message) return { message: body.message };
  if (typeof body.detail === 'string' && body.detail) return { message: body.detail };
  if (Array.isArray(body.detail) && body.detail.length > 0) {
    const first = body.detail[0] as Record<string, unknown> | undefined;
    if (first && typeof first.msg === 'string') return { message: first.msg, details: body.detail };
  }
  return {};
}

/** Map a failed HTTP status + parsed body to the client's error classes. */
export function errorFromResponse(status: number, parsed: unknown): Error {
  const { message, code, details } = readErrorBody(parsed);
  if (status === 401) return new AuthError(message, code);
  if (status === 403) return new ForbiddenError(message, code);
  return new ApiError(status, message ?? `Request failed with status ${status}`, code, details);
}

// ─── Transport ────────────────────────────────────────────────────────────────

export type HttpMethod = 'GET' | 'POST' | 'PATCH' | 'PUT' | 'DELETE';

const MUTATING_METHODS: HttpMethod[] = ['POST', 'PATCH', 'PUT', 'DELETE'];

function buildHeaders(method: HttpMethod, hasJsonBody: boolean): HeadersInit {
  const headers: Record<string, string> = {};

  // FormData bodies: the browser sets Content-Type (with the multipart
  // boundary) itself — setting it here would omit the boundary.
  if (hasJsonBody) headers['Content-Type'] = 'application/json';

  // Double-submit CSRF: echo the readable cookie on every mutating request.
  if (MUTATING_METHODS.includes(method)) {
    const token = getCsrfToken();
    if (token) headers[CSRF_HEADER] = token;
  }

  return headers;
}

/**
 * Send a request to the admin API and return the raw `Response`.
 * Throws `NetworkError` when the backend cannot be reached (fetch threw, or
 * the reverse proxy answered 502/503/504 with its own error page).
 */
export async function sendRequest(method: HttpMethod, path: string, body?: unknown): Promise<Response> {
  const url = `${getGatewayUrl()}${path}`;
  const isFormData = typeof FormData !== 'undefined' && body instanceof FormData;
  const hasJsonBody = body !== undefined && !isFormData && ['POST', 'PATCH', 'PUT'].includes(method);

  let response: Response;
  try {
    response = await fetch(url, {
      method,
      credentials: 'include',
      headers: buildHeaders(method, hasJsonBody),
      ...(body !== undefined ? { body: isFormData ? (body as FormData) : JSON.stringify(body) } : {}),
    });
  } catch (err) {
    reportConnectionLost();
    throw new NetworkError(err);
  }

  if (response.status === 502 || response.status === 503 || response.status === 504) {
    // 503 can also be a deliberate backend answer (FEATURE_DISABLED) — keep
    // its envelope message when there is one; an edge error page has none.
    const { message, code } = readErrorBody(await readJson(response.clone()));
    if (response.status === 503 && message) throw new ApiError(503, message, code);
    reportConnectionLost();
    throw new NetworkError(new Error(`Gateway responded ${response.status}`));
  }

  return response;
}

/** Parse a JSON body, or null for empty / non-JSON bodies (204, HTML error pages). */
export async function readJson(response: Response): Promise<unknown> {
  const contentType = response.headers.get('content-type') ?? '';
  if (!contentType.includes('json')) return null;
  try {
    return await response.json();
  } catch {
    return null;
  }
}

export interface ApiResult<T> {
  data: T;
  /** The envelope's `meta` (pagination on list endpoints), or null. */
  meta: ListMeta | null;
}

/**
 * Perform a request and return the unwrapped `data` together with `meta`.
 * Envelope `{success, data, error, meta}` is unwrapped; `success: false` or a
 * non-2xx status becomes `AuthError` / `ForbiddenError` / `ApiError` carrying
 * the backend's message.
 */
export async function apiRequest<T>(method: HttpMethod, path: string, body?: unknown): Promise<ApiResult<T>> {
  const response = await sendRequest(method, path, body);
  const parsed = await readJson(response);

  if (!response.ok) throw errorFromResponse(response.status, parsed);

  if (parsed !== null && typeof parsed === 'object' && 'success' in parsed && 'data' in parsed) {
    const envelope = parsed as { success: boolean; data: T; meta?: ListMeta | null };
    if (!envelope.success) throw errorFromResponse(response.status, parsed);
    return { data: envelope.data, meta: envelope.meta ?? null };
  }

  // Non-envelope success (raw JSON, or null for 204).
  return { data: parsed as T, meta: null };
}

async function request<T>(method: HttpMethod, path: string, body?: unknown): Promise<T> {
  return (await apiRequest<T>(method, path, body)).data;
}

/**
 * Whether a failed request is worth retrying: the backend was unreachable or
 * hit a transient server-side condition. 4xx (bad input, auth, permission,
 * not found) will fail the same way again.
 */
export function isTransientApiError(error: unknown): boolean {
  if (error instanceof NetworkError) return true;
  if (error instanceof ApiError) {
    return error.status >= 500 || error.status === 408 || error.status === 429;
  }
  return false;
}

// ─── Public API ───────────────────────────────────────────────────────────────

/**
 * Perform a GET request and return the unwrapped data payload.
 */
export function apiGet<T>(path: string): Promise<T> {
  return request<T>('GET', path);
}

/**
 * Perform a POST request with a JSON body and return the unwrapped data payload.
 */
export function apiPost<T>(path: string, body?: unknown): Promise<T> {
  return request<T>('POST', path, body);
}

/**
 * Perform a PATCH request with a JSON body and return the unwrapped data payload.
 */
export function apiPatch<T>(path: string, body?: unknown): Promise<T> {
  return request<T>('PATCH', path, body);
}

/**
 * Perform a PUT request and return the unwrapped data payload.
 */
export function apiPut<T>(path: string, body?: unknown): Promise<T> {
  return request<T>('PUT', path, body);
}

/**
 * Perform a DELETE request and return the unwrapped data payload.
 */
export function apiDelete<T>(path: string): Promise<T> {
  return request<T>('DELETE', path);
}

/** Fallback meta for list endpoints that answered without one. */
export function metaFor(items: unknown[], meta: ListMeta | null): ListMeta {
  return meta ?? { total: items.length, page: 1, page_size: items.length || 25, has_more: false };
}

/**
 * Build a query string from a params object, skipping empty values.
 * `toQuery({ page: 2, status: '' })` → `?page=2`
 */
export function toQuery(params: Record<string, string | number | boolean | null | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === '') continue;
    search.set(key, String(value));
  }
  const qs = search.toString();
  return qs ? `?${qs}` : '';
}
