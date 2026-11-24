import type { Metadata } from 'next';
import { Suspense } from 'react';
import { DashboardSkeleton } from '@/components/charts/dashboard-skeleton';
import { LayoutsList } from '@/app/(dashboard)/layouts/_components/layouts-list';

export const metadata: Metadata = { title: 'Layouts' };

export default function LayoutsPage() {
  return (
    <Suspense fallback={<DashboardSkeleton />}>
      <LayoutsList />
    </Suspense>
  );
}
