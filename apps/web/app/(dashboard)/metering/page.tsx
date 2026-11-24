import type { Metadata } from 'next';
import { Suspense } from 'react';
import { DashboardSkeleton } from '@/components/charts/dashboard-skeleton';
import { MeteringPage } from '@/app/(dashboard)/metering/_components/metering-page';

export const metadata: Metadata = { title: 'Metering' };

export default function MeteringRoute() {
  return (
    <Suspense fallback={<DashboardSkeleton />}>
      <MeteringPage />
    </Suspense>
  );
}
