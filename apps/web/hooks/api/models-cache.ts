'use client';

/**
 * Model cache hooks — per-node cache items and summaries, prefetch, evict,
 * pin / unpin (React Query over lib/api/models.ts).
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import * as api from '@/lib/api/models';
import { fleetKeys } from '@/hooks/api/fleet';
import type { CacheItem, CacheListParams, EvictRequest, PrefetchRequest } from '@/types/models';

const LIVE_INTERVAL_MS = 10_000;

export const cacheKeys = {
  all: ['model-cache'] as const,
  list: (params?: CacheListParams) => ['model-cache', 'list', params] as const,
  summary: (clusterId?: string) => ['model-cache', 'summary', clusterId ?? ''] as const,
};

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'Request failed';
}

export function useCacheItems(params: CacheListParams = {}, options: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: cacheKeys.list(params),
    queryFn: () => api.listCacheItems(params),
    staleTime: 10_000,
    placeholderData: (prev) => prev,
    refetchInterval: LIVE_INTERVAL_MS,
    ...options,
  });
}

export function useCacheSummary(clusterId?: string) {
  return useQuery({
    queryKey: cacheKeys.summary(clusterId),
    queryFn: async () => (await api.getCacheSummary(clusterId)).items,
    staleTime: 10_000,
    refetchInterval: LIVE_INTERVAL_MS,
  });
}

function invalidateCache(qc: ReturnType<typeof useQueryClient>) {
  qc.invalidateQueries({ queryKey: cacheKeys.all });
  qc.invalidateQueries({ queryKey: ['models'] });
  qc.invalidateQueries({ queryKey: fleetKeys.nodes });
}

export function usePrefetchModel() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: PrefetchRequest) => api.prefetchModel(body),
    onSuccess: (_, body) => {
      invalidateCache(qc);
      toast.success(`Prefetch queued on ${body.node_ids.length} node${body.node_ids.length === 1 ? '' : 's'}`);
    },
    onError: (error) => toast.error(`Could not queue the prefetch: ${errorMessage(error)}`),
  });
}

export function useEvictCache() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: EvictRequest) => api.evictCache(body),
    onSuccess: () => {
      invalidateCache(qc);
      toast.success('Eviction queued — files are removed as instances release them');
    },
    onError: (error) => toast.error(`Could not evict: ${errorMessage(error)}`),
  });
}

export function useDeleteCacheItem() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, force }: { id: string; force?: boolean }) => api.deleteCacheItem(id, force),
    onSuccess: () => {
      invalidateCache(qc);
      toast.success('Cache item eviction queued');
    },
    onError: (error) => toast.error(`Could not evict the item: ${errorMessage(error)}`),
  });
}

/** Pin / unpin flip `pinned` optimistically in every cached list page. */
function usePinMutation(pinned: boolean) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, reason }: { id: string; reason?: string }) =>
      pinned ? api.pinCacheItem(id, reason) : api.unpinCacheItem(id),
    onMutate: async ({ id, reason }) => {
      await qc.cancelQueries({ queryKey: cacheKeys.all });
      const snapshots = qc.getQueriesData<{ items: CacheItem[]; meta: unknown }>({ queryKey: ['model-cache', 'list'] });
      for (const [key, data] of snapshots) {
        if (!data) continue;
        qc.setQueryData(key, {
          ...data,
          items: data.items.map((item) =>
            item.id === id ? { ...item, pinned, pinned_reason: pinned ? (reason ?? null) : null } : item,
          ),
        });
      }
      return { snapshots };
    },
    onError: (error, _vars, ctx) => {
      for (const [key, data] of ctx?.snapshots ?? []) qc.setQueryData(key, data);
      toast.error(`Could not ${pinned ? 'pin' : 'unpin'} the item: ${errorMessage(error)}`);
    },
    onSuccess: () => toast.success(pinned ? 'Pinned — eviction rules will skip this file' : 'Unpinned'),
    onSettled: () => invalidateCache(qc),
  });
}

export function usePinCacheItem() {
  return usePinMutation(true);
}

export function useUnpinCacheItem() {
  return usePinMutation(false);
}
