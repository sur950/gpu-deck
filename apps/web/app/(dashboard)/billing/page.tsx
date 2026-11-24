import type { Metadata } from 'next';
import { Suspense } from 'react';
import { DashboardSkeleton } from '@/components/charts/dashboard-skeleton';
import { BillingPage } from '@/app/(dashboard)/billing/_components/billing-page';

export const metadata: Metadata = { title: 'Billing' };

export default function BillingRoute() {
  return (
    <Suspense fallback={<DashboardSkeleton />}>
      <BillingPage />
    </Suspense>
  );
}
