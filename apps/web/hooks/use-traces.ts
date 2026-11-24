/**
 * Trace search and trace detail hooks.
 */

'use client';

import { useQuery } from '@tanstack/react-query';
import { getTrace, getTraces } from '@/lib/api/traces';
import type { TraceQuery } from '@/types/traces';

export const tracesKeys = {
  all: ['traces'] as const,
  list: (query: TraceQuery) => [...tracesKeys.all, 'list', query] as const,
  detail: (traceId: string) => [...tracesKeys.all, 'detail', traceId] as const,
};

export function useTraces(query: TraceQuery) {
  return useQuery({
    queryKey: tracesKeys.list(query),
    queryFn: () => getTraces(query),
    staleTime: 15_000,
    placeholderData: (prev) => prev,
  });
}

export function useTrace(traceId: string | null) {
  return useQuery({
    queryKey: tracesKeys.detail(traceId ?? ''),
    queryFn: () => getTrace(traceId as string),
    enabled: Boolean(traceId),
    staleTime: 60_000,
  });
}
