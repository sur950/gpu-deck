/**
 * Dashboard layout
 * Main authenticated app layout with sidebar and topnav. One admin, no
 * roles: there is no permissions step between the session check and the shell.
 */

'use client';

// No-op unless NEXT_PUBLIC_API_MOCKS=1: patches fetch with fixture handlers.
import '@/lib/mocks/boot';

import { useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { useAuth } from '@/hooks/use-auth';
import { FullPageLoader } from '@/components/shared/LoadingSkeleton';
import { Sidebar } from '@/components/layout/Sidebar';
import { TopNav } from '@/components/layout/TopNav';
// No-op outside the GPU Deck desktop app (see lib/desktop).
import { DesktopBridge } from '@/lib/desktop/desktop-bridge';

export default function DashboardLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const router = useRouter();
  const { isAuthenticated, isLoading, sessionCheckFailed } = useAuth();

  // Redirect to login if not authenticated. A failed session check (backend
  // unreachable or erroring) is not a sign-out: stay on this page while the
  // auth context retries; the health bar above says why we're waiting.
  useEffect(() => {
    if (!isAuthenticated && !isLoading && !sessionCheckFailed) {
      router.replace('/login');
    }
  }, [isAuthenticated, isLoading, sessionCheckFailed, router]);

  if (isLoading) {
    return <FullPageLoader />;
  }

  if (!isAuthenticated) {
    return <FullPageLoader />;
  }

  return (
    <div className="h-screen bg-background flex overflow-hidden">
      <DesktopBridge />
      {/* Sidebar */}
      <Sidebar />

      {/* Main content area */}
      <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
        {/* Top navigation */}
        <TopNav />

        {/* Page content */}
        <main className="relative flex-1 overflow-auto">
          {children}
        </main>
      </div>
    </div>
  );
}
