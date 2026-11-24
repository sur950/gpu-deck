'use client';

import { QueryCache, MutationCache, QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useState } from 'react';
import { AuthError, isTransientApiError } from '@/lib/api/client';
import { isPublicPath } from '@/utils/public-paths';

/**
 * Redirect to /login whenever any query or mutation receives a 401 response.
 * We use window.location.replace so the browser does a full navigation
 * (clearing any in-flight React state) and doesn't add a history entry
 * the user can "back" into.
 *
 * Guard: skip on public pages (login, signup, forgot-password, etc.)
 * to prevent redirect loops and broken invite / reset-password flows.
 */
function handleAuthError(error: unknown): void {
  if (
    error instanceof AuthError &&
    typeof window !== 'undefined' &&
    !isPublicPath(window.location.pathname)
  ) {
    window.location.replace('/login');
  }
}

function makeQueryClient(): QueryClient {
  return new QueryClient({
    queryCache: new QueryCache({ onError: handleAuthError }),
    mutationCache: new MutationCache({ onError: handleAuthError }),
    defaultOptions: {
      queries: {
        staleTime: 60 * 1000,
        refetchOnWindowFocus: false,
        // Retry only transient failures (unreachable backend, 5xx, 408, 429),
        // up to 3 times with React Query's exponential backoff (1s, 2s, 4s),
        // so a brief blip — a deploy, a pool timeout, a slow edge — doesn't
        // land the page in its error state. 401/403/other 4xx fail the same
        // way every time, so they are not retried.
        retry: (failureCount, error) => isTransientApiError(error) && failureCount < 3,
      },
      mutations: {
        retry: 0, // mutations never retry — side effects must not be duplicated
        // Drop a finished mutation from the cache as soon as its component
        // unmounts: variables and results include passwords, plaintext API
        // keys and join tokens, which must not linger in memory (default 5 min).
        gcTime: 0,
      },
    },
  });
}

export function Providers({ children }: { children: React.ReactNode }) {
  const [queryClient] = useState(makeQueryClient);
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}
