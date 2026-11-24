/**
 * Mock mode entry point.
 *
 *   NEXT_PUBLIC_API_MOCKS=1 pnpm dev        (sign in as admin / admin-admin-admin)
 *
 * `isMockMode()` is a build-time constant (NEXT_PUBLIC_* and NODE_ENV), so in
 * a normal build — and in every production build — nothing is patched and the
 * fixtures are never loaded.
 *
 * `bootMocks()` runs from a side-effect import (`import '@/lib/mocks/boot'`)
 * in the client layouts / pages (`app/(dashboard)/layout.tsx`,
 * `app/(auth)/login/page.tsx`). It patches `fetch` synchronously while the
 * route's modules evaluate — before React runs any effect — so the very
 * first `/auth/me` and `/health` calls are answered by the mocks. The handler
 * modules and fixtures load lazily; the patched `fetch` waits for them.
 *
 * Streams (`/live/events`, `/logs/stream`) cannot be served through a
 * patched `fetch` + EventSource; the live hooks consult `isMockMode()` and
 * subscribe to `lib/mocks/live.ts` instead.
 */

import { installMockFetch } from '@/lib/mocks/install';

export function isMockMode(): boolean {
  // Never in a production build: `next build` inlines NODE_ENV=production, so
  // NEXT_PUBLIC_API_MOCKS=1 at build time cannot ship a fixture-backed admin
  // UI that signs anyone in with the mock credentials.
  return process.env.NEXT_PUBLIC_API_MOCKS === '1' && process.env.NODE_ENV !== 'production';
}

let booted = false;

export function bootMocks(): void {
  if (!isMockMode() || typeof window === 'undefined' || booted) return;
  booted = true;
  installMockFetch(() =>
    import('@/lib/mocks/handlers').then(({ MOCK_HANDLER_FILES }) => {
      if (process.env.NODE_ENV !== 'production') {
        console.info(
          `[mocks] API mock mode on — ${process.env.NEXT_PUBLIC_API_URL} served from fixtures (${MOCK_HANDLER_FILES.join(', ')})`,
        );
      }
    }),
  );
}
