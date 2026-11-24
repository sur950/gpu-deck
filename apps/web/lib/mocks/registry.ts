/**
 * Mock handler registry.
 *
 * Each feature registers its handlers from its own
 * `lib/mocks/handlers/<feature>.ts` file; `lib/mocks/handlers/index.ts`
 * discovers every file in that directory, so adding a feature never touches a
 * shared file.
 *
 * Lookup picks the best match for a request:
 *   1. an owning handler beats a `fallback: true` stand-in (the dashboards
 *      register read-only `GET /nodes`, `/deployments`, `/swaps`… so their
 *      screens work alone; the fleet / models / automation files override them
 *      no matter which file loads first),
 *   2. then more literal segments win, so `/nodes/join-tokens` beats
 *      `/nodes/:node_id`,
 *   3. then the later registration wins.
 */

import type { HttpMethod, MockHandler, MockRequest, MockResult } from '@/lib/mocks/types';
import type { Schemas } from '@/lib/mocks/types';

interface CompiledHandler {
  feature: string;
  handler: MockHandler;
  regex: RegExp;
  keys: string[];
  /** Number of literal segments — higher wins on ties. */
  specificity: number;
  /** Registration sequence — later wins on full ties. */
  seq: number;
}

const handlers: CompiledHandler[] = [];
const features = new Set<string>();
let sequence = 0;

function compile(pattern: string): { regex: RegExp; keys: string[]; specificity: number } {
  const keys: string[] = [];
  let specificity = 0;
  const source = pattern
    .split('/')
    .filter(Boolean)
    .map((segment) => {
      if (segment.startsWith(':')) {
        keys.push(segment.slice(1));
        return '([^/]+)';
      }
      if (segment === '*') return '(.*)';
      specificity += 1;
      return segment.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    })
    .join('/');
  return { regex: new RegExp(`^/${source}/?$`), keys, specificity };
}

/**
 * Register a feature's handlers. Re-registering a feature (HMR) replaces all
 * of its handlers. Within one tier (owning vs fallback), registering the same
 * method + pattern again replaces the earlier handler.
 */
export function registerMockHandlers(feature: string, list: MockHandler[]): void {
  if (features.has(feature)) {
    for (let i = handlers.length - 1; i >= 0; i -= 1) {
      if (handlers[i].feature === feature) handlers.splice(i, 1);
    }
  }
  features.add(feature);
  for (const handler of list) {
    const compiled = compile(handler.path);
    const existing = handlers.findIndex(
      (h) =>
        h.handler.method === handler.method &&
        h.regex.source === compiled.regex.source &&
        Boolean(h.handler.fallback) === Boolean(handler.fallback),
    );
    if (existing >= 0) handlers.splice(existing, 1);
    sequence += 1;
    handlers.push({ feature, handler, ...compiled, seq: sequence });
  }
}

/** Mark every handler in a list as a read-only stand-in (see `MockHandler.fallback`). */
export function asFallback(list: MockHandler[]): MockHandler[] {
  return list.map((h) => ({ ...h, fallback: true }));
}

function rank(entry: CompiledHandler): [number, number, number] {
  return [entry.handler.fallback ? 0 : 1, entry.specificity, entry.seq];
}

function beats(a: [number, number, number], b: [number, number, number]): boolean {
  for (let i = 0; i < a.length; i += 1) {
    if (a[i] !== b[i]) return a[i] > b[i];
  }
  return false;
}

export function matchMockHandler(
  method: HttpMethod,
  path: string,
): { handler: MockHandler; params: Record<string, string>; feature: string } | null {
  let best: { entry: CompiledHandler; params: Record<string, string> } | null = null;
  for (const entry of handlers) {
    if (entry.handler.method !== '*' && entry.handler.method !== method) continue;
    const match = entry.regex.exec(path);
    if (!match) continue;
    if (best && !beats(rank(entry), rank(best.entry))) continue;
    const params: Record<string, string> = {};
    entry.keys.forEach((key, i) => {
      params[key] = decodeURIComponent(match[i + 1] ?? '');
    });
    best = { entry, params };
  }
  return best ? { handler: best.entry.handler, params: best.params, feature: best.entry.feature } : null;
}

export function registeredMockFeatures(): string[] {
  return [...features];
}

/** Every registered handler (diagnostics / tests): owner feature, method, pattern, tier. */
export function listMockHandlers(): { feature: string; method: HttpMethod | '*'; path: string; pattern: string; fallback: boolean }[] {
  return handlers.map((h) => ({
    feature: h.feature,
    method: h.handler.method,
    path: h.handler.path,
    pattern: h.regex.source,
    fallback: Boolean(h.handler.fallback),
  }));
}

// ─── Result helpers for handler files ────────────────────────────────────────

export function ok(data: unknown, meta: Record<string, unknown> | Schemas['ListMeta'] | null = null): MockResult {
  return { status: 200, data, meta };
}

export function created(data: unknown): MockResult {
  return { status: 201, data };
}

export function accepted(data: unknown): MockResult {
  return { status: 202, data };
}

export function noContent(): MockResult {
  return { raw: null, status: 204 };
}

export function fail(status: number, code: string, message: string, details?: unknown): MockResult {
  return { status, error: { code, message, details } };
}

export function notFound(what = 'Resource'): MockResult {
  return fail(404, 'NOT_FOUND', `${what} not found`);
}

/** Slice a list by the standard `page` / `page_size` params and build `ListMeta`. */
export function paginate<T>(items: T[], query: URLSearchParams, defaultPageSize = 50): MockResult {
  const page = Math.max(1, Number(query.get('page') ?? 1) || 1);
  const pageSize = Math.min(500, Math.max(1, Number(query.get('page_size') ?? defaultPageSize) || defaultPageSize));
  const start = (page - 1) * pageSize;
  const slice = items.slice(start, start + pageSize);
  const meta: Schemas['ListMeta'] = {
    total: items.length,
    page,
    page_size: pageSize,
    has_more: start + pageSize < items.length,
  };
  return { status: 200, data: slice, meta };
}

/** Apply `sort=field` / `sort=-field` to a list of records. */
export function sortBy<T extends Record<string, unknown>>(items: T[], sort: string | null, fallback = '-created_at'): T[] {
  const spec = sort || fallback;
  const desc = spec.startsWith('-');
  const field = desc ? spec.slice(1) : spec;
  return [...items].sort((a, b) => {
    const av = a[field];
    const bv = b[field];
    if (av === bv) return 0;
    if (av === undefined || av === null) return 1;
    if (bv === undefined || bv === null) return -1;
    const cmp = av < bv ? -1 : 1;
    return desc ? -cmp : cmp;
  });
}

/** Typed helper so handlers can read the request body as an object. */
export function bodyOf<T extends object>(req: MockRequest): Partial<T> {
  return (req.body && typeof req.body === 'object' ? req.body : {}) as Partial<T>;
}
