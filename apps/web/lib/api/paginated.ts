/**
 * `apiGetList` — like `apiGet` but keeps the envelope's `meta` (total, page,
 * page_size, has_more) that list endpoints return. Same transport and error
 * mapping as lib/api/client.ts.
 */

import { apiRequest, metaFor, type ListMeta } from '@/lib/api/client';

export type { ListMeta };

export interface ListResult<T> {
  items: T[];
  meta: ListMeta;
}

export async function apiGetList<T>(path: string): Promise<ListResult<T>> {
  const { data, meta } = await apiRequest<T[] | null>('GET', path);
  const items = Array.isArray(data) ? data : [];
  return { items, meta: metaFor(items, meta) };
}
