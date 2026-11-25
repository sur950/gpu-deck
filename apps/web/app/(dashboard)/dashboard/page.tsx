import type { Metadata } from 'next';
import { Suspense } from 'react';
import { DashboardSkeleton } from '@/components/charts/dashboard-skeleton';
import { DashboardView } from '@/app/(dashboard)/dashboard/_components/dashboard-view';

export const metadata: Metadata = { title: 'Dashboard' };

/** Overview: fleet health, inference traffic and automation activity. */
export default function DashboardPage() {
  return (
    // The range lives in the URL (useSearchParams), which needs a Suspense boundary.
    <Suspense fallback={<DashboardSkeleton />}>
      <DashboardView />
    </Suspense>
  );
}
