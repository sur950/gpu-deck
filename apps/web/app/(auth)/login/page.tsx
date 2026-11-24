/**
 * Login page
 * Entry point for the seeded admin.
 */

'use client';

// No-op unless NEXT_PUBLIC_API_MOCKS=1: patches fetch with fixture handlers.
import '@/lib/mocks/boot';

import { Suspense, useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { LoginForm, safeRedirect } from '@/app/(auth)/_components/LoginForm';
import { useAuth } from '@/hooks/use-auth';
import { FullPageLoader } from '@/components/shared/LoadingSkeleton';
import { GpuDeckTile } from '@/components/shared/gpu-deck-mark';
import { LOGO_URL } from '@/utils/brand-env';

export default function LoginPage() {
  const router = useRouter();
  const { isAuthenticated, isLoading } = useAuth();

  // Already signed in (or just signed in): go to the page that sent us here, else the dashboard.
  // Must agree with LoginForm's own redirect, or the two navigations race.
  useEffect(() => {
    if (isAuthenticated && !isLoading) {
      router.replace(safeRedirect(new URLSearchParams(window.location.search).get('from')));
    }
  }, [isAuthenticated, isLoading, router]);

  if (isLoading) {
    return <FullPageLoader />;
  }

  if (isAuthenticated) {
    return <FullPageLoader />;
  }

  return (
    <div className="flex flex-col items-center justify-center">
      {/* The app tile over the card, unless a white-label logo is configured. */}
      {!LOGO_URL && <GpuDeckTile size={52} className="mb-6 drop-shadow-lg" />}
      {/* useSearchParams() in the form needs a Suspense boundary for static rendering. */}
      <Suspense fallback={<FullPageLoader />}>
        <LoginForm />
      </Suspense>
    </div>
  );
}
