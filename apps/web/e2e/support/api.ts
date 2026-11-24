/**
 * Admin API client for spec setup / cleanup, sharing the signed-in session
 * (cookies from e2e/.auth/admin.json + the CSRF double-submit header).
 */

import { readFileSync } from 'node:fs';
import { request, type APIRequestContext } from '@playwright/test';
import { E2E } from './env';

interface Envelope<T> {
  success: boolean;
  data: T;
  error: { code: string; message: string } | null;
  meta: Record<string, unknown> | null;
}

export class AdminApi {
  private constructor(private readonly ctx: APIRequestContext) {}

  static async create(): Promise<AdminApi> {
    const state = JSON.parse(readFileSync(E2E.authFile, 'utf8')) as { cookies: { name: string; value: string }[] };
    const csrf = state.cookies.find((c) => c.name === E2E.csrfCookie)?.value ?? '';
    const ctx = await request.newContext({
      baseURL: `${E2E.apiUrl}/`,
      storageState: E2E.authFile,
      extraHTTPHeaders: { 'X-CSRF-Token': csrf, Origin: E2E.baseUrl },
    });
    return new AdminApi(ctx);
  }

  private async call<T>(method: 'GET' | 'POST' | 'PATCH' | 'DELETE', path: string, body?: unknown): Promise<T> {
    const res = await this.ctx.fetch(path.replace(/^\//, ''), { method, data: body });
    const text = await res.text();
    const json = text ? (JSON.parse(text) as Envelope<T>) : null;
    if (!res.ok()) throw new Error(`${method} ${path} → ${res.status()}: ${json?.error?.message ?? text.slice(0, 300)}`);
    return (json?.data ?? null) as T;
  }

  get<T>(path: string) {
    return this.call<T>('GET', path);
  }
  post<T>(path: string, body?: unknown) {
    return this.call<T>('POST', path, body);
  }
  patch<T>(path: string, body?: unknown) {
    return this.call<T>('PATCH', path, body);
  }
  delete<T>(path: string) {
    return this.call<T>('DELETE', path);
  }

  /** DELETE that ignores 404 / 409 (cleanup of records a failed spec may have left half-made). */
  async remove(path: string): Promise<void> {
    const res = await this.ctx.fetch(path.replace(/^\//, ''), { method: 'DELETE' });
    if (!res.ok() && ![404, 409].includes(res.status())) throw new Error(`DELETE ${path} → ${res.status()}: ${(await res.text()).slice(0, 300)}`);
  }

  async dispose(): Promise<void> {
    await this.ctx.dispose();
  }
}
