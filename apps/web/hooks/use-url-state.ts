'use client';

import { useCallback, useMemo } from 'react';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';

/**
 * Table / list state mirrored to the URL: search,
 * filters, sort, page and page size survive a refresh and are shareable.
 *
 * Writes use `router.replace(…, { scroll: false })` so typing in a filter
 * never adds history entries or jumps the page. Pair with `useDebounce` for
 * the search box (300 ms).
 *
 *   const { params, setParams, resetParams } = useUrlState({
 *     search: '', status: 'all', sortBy: 'created_at', sortOrder: 'desc', page: 1, pageSize: 25,
 *   });
 *   setParams({ status: 'running', page: 1 });
 */

export type UrlStateValue = string | number | boolean;
export type UrlStateShape = Record<string, UrlStateValue>;

function parseValue(raw: string | null, fallback: UrlStateValue): UrlStateValue {
  if (raw === null) return fallback;
  if (typeof fallback === 'number') {
    const n = Number(raw);
    return Number.isFinite(n) ? n : fallback;
  }
  if (typeof fallback === 'boolean') return raw === 'true' || raw === '1';
  return raw;
}

export function useUrlState<T extends UrlStateShape>(defaults: T) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const params = useMemo<T>(() => {
    const next = { ...defaults };
    for (const key of Object.keys(defaults) as (keyof T)[]) {
      next[key] = parseValue(searchParams.get(String(key)), defaults[key]) as T[keyof T];
    }
    return next;
  }, [defaults, searchParams]);

  const setParams = useCallback(
    (patch: Partial<T>) => {
      const search = new URLSearchParams(searchParams.toString());
      for (const [key, value] of Object.entries(patch)) {
        const fallback = defaults[key as keyof T];
        // Defaults are omitted from the URL so a clean page has a clean address.
        if (value === undefined || value === null || value === fallback || value === '') {
          search.delete(key);
        } else {
          search.set(key, String(value));
        }
      }
      const qs = search.toString();
      router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
    },
    [defaults, pathname, router, searchParams],
  );

  const resetParams = useCallback(() => {
    router.replace(pathname, { scroll: false });
  }, [pathname, router]);

  return { params, setParams, resetParams };
}
