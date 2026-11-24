/**
 * Log history (cursor-paginated, newest first) and the live tail.
 *
 * `useLogStream` follows `/logs/stream` (SSE, same filters) and appends to
 * a bounded in-memory buffer; in mock mode it consumes the mock live stream's
 * `log` topic. Reconnect is handled by `useLiveEvents`.
 */

'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { useInfiniteQuery } from '@tanstack/react-query';
import { getLogs, mapLogChunk } from '@/lib/api/logs';
import { useLiveEvents } from '@/hooks/use-live-events';
import type { components } from '@/lib/api/generated/admin';
import type { LogChunk, LogQuery } from '@/types/logs';

export const logsKeys = {
  all: ['logs'] as const,
  history: (query: LogQuery) => [...logsKeys.all, 'history', query] as const,
};

export function useLogHistory(query: Omit<LogQuery, 'cursor'>, enabled = true) {
  return useInfiniteQuery({
    queryKey: logsKeys.history(query),
    queryFn: ({ pageParam }) => getLogs({ ...query, cursor: pageParam ?? undefined }),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => (last.hasMore ? last.nextCursor : null),
    enabled,
    staleTime: 10_000,
    placeholderData: (prev) => prev,
  });
}

const MAX_TAIL = 2_000;

export interface LogTail {
  chunks: LogChunk[];
  status: ReturnType<typeof useLiveEvents>['status'];
  clear: () => void;
}

/** Live tail of log chunks matching the filters (bounded buffer, oldest dropped). */
export function useLogStream(filters: Pick<LogQuery, 'nodeId' | 'instanceId' | 'deploymentId' | 'sources' | 'level' | 'search'>, enabled: boolean): LogTail {
  const [chunks, setChunks] = useState<LogChunk[]>([]);
  const pending = useRef<LogChunk[]>([]);
  const flushTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const filtersRef = useRef(filters);
  useEffect(() => {
    filtersRef.current = filters;
  });

  // Batch appends at ~8 fps so a busy stream doesn't re-render per line.
  const flush = useCallback(() => {
    flushTimer.current = null;
    if (pending.current.length === 0) return;
    const next = pending.current;
    pending.current = [];
    setChunks((prev) => {
      const merged = [...prev, ...next];
      return merged.length > MAX_TAIL ? merged.slice(merged.length - MAX_TAIL) : merged;
    });
  }, []);

  const onEvent = useCallback(
    (event: components['schemas']['LiveEvent']) => {
      if (event.event !== 'log') return;
      const chunk = mapLogChunk(event.data as unknown as components['schemas']['LogChunk']);
      const f = filtersRef.current;
      if (f.nodeId && chunk.nodeId !== f.nodeId) return;
      if (f.instanceId && chunk.instanceId !== f.instanceId) return;
      if (f.deploymentId && chunk.deploymentId !== f.deploymentId) return;
      if (f.sources && f.sources.length > 0 && !f.sources.includes(chunk.source)) return;
      if (f.level && LEVEL_RANK[chunk.level] < LEVEL_RANK[f.level]) return;
      if (f.search && !chunk.content.toLowerCase().includes(f.search.toLowerCase())) return;
      pending.current.push(chunk);
      if (!flushTimer.current) flushTimer.current = setTimeout(flush, 120);
    },
    [flush],
  );

  const scope = { nodeId: filters.nodeId, deploymentId: filters.deploymentId, instanceId: filters.instanceId };
  const live = useLiveEvents({ topics: ['log'], scope, enabled, onEvent });

  useEffect(() => {
    if (!enabled) {
      pending.current = [];
      if (flushTimer.current) clearTimeout(flushTimer.current);
      flushTimer.current = null;
    }
  }, [enabled]);

  const clear = useCallback(() => {
    pending.current = [];
    setChunks([]);
  }, []);

  return { chunks, status: live.status, clear };
}

const LEVEL_RANK: Record<string, number> = { debug: 0, info: 1, warn: 2, error: 3 };
