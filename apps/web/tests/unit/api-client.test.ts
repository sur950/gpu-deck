import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  ApiError,
  AuthError,
  ForbiddenError,
  NetworkError,
  apiGet,
  apiPost,
  apiRequest,
  isTransientApiError,
  readErrorBody,
  toQuery,
} from '@/lib/api/client';
import { CONNECTION_LOST_EVENT } from '@/lib/api/connection';
import { apiGetList } from '@/lib/api/paginated';

const BASE = 'http://api.test/api/v1';

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
}

const fetchMock = vi.fn<typeof fetch>();

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal('fetch', fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('envelope handling', () => {
  it('unwraps data and keeps meta', async () => {
    fetchMock.mockResolvedValueOnce(json(200, { success: true, data: [{ id: 1 }], error: null, meta: { total: 7, page: 2, page_size: 1, has_more: true } }));
    const result = await apiRequest<{ id: number }[]>('GET', '/nodes?page=2');
    expect(result.data).toEqual([{ id: 1 }]);
    expect(result.meta?.total).toBe(7);
    expect(fetchMock).toHaveBeenCalledWith(`${BASE}/nodes?page=2`, expect.objectContaining({ method: 'GET', credentials: 'include' }));
  });

  it('returns raw JSON for non-envelope bodies and null for 204', async () => {
    fetchMock.mockResolvedValueOnce(json(200, { status: 'ok' }));
    await expect(apiGet('/health')).resolves.toEqual({ status: 'ok' });
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }));
    await expect(apiGet('/x')).resolves.toBeNull();
  });

  it('turns success:false into ApiError with the backend message and code', async () => {
    fetchMock.mockResolvedValueOnce(json(409, { success: false, data: null, error: { code: 'NODE_NAME_TAKEN', message: 'Another node is called gpu-01' }, meta: null }));
    const err = await apiGet('/nodes/1').catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err).toMatchObject({ status: 409, code: 'NODE_NAME_TAKEN', message: 'Another node is called gpu-01' });
  });
});

describe('401 / 403 keep the backend message', () => {
  it('passes the login failure message into AuthError', async () => {
    fetchMock.mockResolvedValueOnce(json(401, { success: false, data: null, error: { code: 'INVALID_CREDENTIALS', message: 'Invalid username or password' }, meta: null }));
    const err = await apiPost('/auth/login', { username: 'admin', password: 'x' }).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(AuthError);
    expect(err).toMatchObject({ status: 401, message: 'Invalid username or password', code: 'INVALID_CREDENTIALS' });
  });

  it('keeps the lockout notice', async () => {
    fetchMock.mockResolvedValueOnce(json(401, { success: false, data: null, error: { code: 'ACCOUNT_LOCKED', message: 'Account locked. Try again in 14 minutes.' }, meta: null }));
    await expect(apiPost('/auth/login', {})).rejects.toThrow('Account locked. Try again in 14 minutes.');
  });

  it('falls back to "Unauthorized" when the body has no message', async () => {
    fetchMock.mockResolvedValueOnce(new Response('nope', { status: 401 }));
    await expect(apiGet('/auth/me')).rejects.toMatchObject({ name: 'AuthError', message: 'Unauthorized' });
  });

  it('maps 403 with FastAPI detail to ForbiddenError', async () => {
    fetchMock.mockResolvedValueOnce(json(403, { detail: 'CSRF token missing' }));
    const err = await apiPost('/rules', {}).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ForbiddenError);
    expect((err as Error).message).toBe('CSRF token missing');
  });
});

describe('network failures', () => {
  it('throws NetworkError and signals connection loss when fetch rejects', async () => {
    const listener = vi.fn();
    window.addEventListener(CONNECTION_LOST_EVENT, listener);
    fetchMock.mockRejectedValueOnce(new TypeError('Failed to fetch'));
    await expect(apiGet('/nodes')).rejects.toBeInstanceOf(NetworkError);
    expect(listener).toHaveBeenCalledTimes(1);
    window.removeEventListener(CONNECTION_LOST_EVENT, listener);
  });

  it('treats an edge 502 page as unreachable but a backend 503 envelope as an ApiError', async () => {
    fetchMock.mockResolvedValueOnce(new Response('<html>Bad gateway</html>', { status: 502, headers: { 'content-type': 'text/html' } }));
    await expect(apiGet('/nodes')).rejects.toBeInstanceOf(NetworkError);
    fetchMock.mockResolvedValueOnce(json(503, { success: false, data: null, error: { code: 'FEATURE_DISABLED', message: 'Node scan needs agent 0.2' }, meta: null }));
    await expect(apiGet('/nodes/1/scan')).rejects.toMatchObject({ name: 'ApiError', status: 503, code: 'FEATURE_DISABLED' });
  });
});

describe('request headers', () => {
  it('sends JSON and echoes the CSRF cookie on mutations only', async () => {
    document.cookie = 'gpudeck_csrf=tok-123; path=/';
    fetchMock.mockResolvedValue(json(200, { success: true, data: null, error: null, meta: null }));
    await apiPost('/rules', { a: 1 });
    const [, init] = fetchMock.mock.calls[0];
    expect(init?.headers).toMatchObject({ 'Content-Type': 'application/json', 'X-CSRF-Token': 'tok-123' });
    expect(init?.body).toBe('{"a":1}');
    await apiGet('/rules');
    expect(fetchMock.mock.calls[1][1]?.headers).toEqual({});
  });

  it('lets the browser set the multipart boundary for FormData', async () => {
    fetchMock.mockResolvedValue(json(200, { success: true, data: null, error: null, meta: null }));
    const form = new FormData();
    form.set('file', 'x');
    await apiPost('/upload', form);
    const headers = fetchMock.mock.calls[0][1]?.headers as Record<string, string>;
    expect(headers['Content-Type']).toBeUndefined();
  });
});

describe('helpers', () => {
  it('builds query strings without empty values', () => {
    expect(toQuery({ page: 2, status: '', q: null, sort: '-created_at', on: false })).toBe('?page=2&sort=-created_at&on=false');
    expect(toQuery({})).toBe('');
  });

  it('reads every error body shape', () => {
    expect(readErrorBody({ error: 'bare' })).toEqual({ message: 'bare' });
    expect(readErrorBody({ success: false, message: 'top' })).toEqual({ message: 'top' });
    expect(readErrorBody({ detail: [{ msg: 'field required', loc: ['body', 'name'] }] }).message).toBe('field required');
    expect(readErrorBody(null)).toEqual({});
  });

  it('classifies transient errors', () => {
    expect(isTransientApiError(new NetworkError())).toBe(true);
    expect(isTransientApiError(new ApiError(503, 'x'))).toBe(true);
    expect(isTransientApiError(new ApiError(429, 'x'))).toBe(true);
    expect(isTransientApiError(new ApiError(404, 'x'))).toBe(false);
    expect(isTransientApiError(new AuthError())).toBe(false);
  });

  it('apiGetList falls back to a synthetic meta', async () => {
    fetchMock.mockResolvedValueOnce(json(200, { success: true, data: [1, 2, 3], error: null, meta: null }));
    await expect(apiGetList<number>('/traces')).resolves.toEqual({ items: [1, 2, 3], meta: { total: 3, page: 1, page_size: 3, has_more: false } });
  });
});
