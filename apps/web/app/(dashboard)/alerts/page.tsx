import type { Metadata } from 'next';
import { Suspense } from 'react';
import { ListPageSkeleton } from '@/components/shared/ListPageSkeleton';
import { AlertsPage } from '@/app/(dashboard)/alerts/_components/alerts-page';

export const metadata: Metadata = { title: 'Alerts' };

export default function AlertsRoute() {
  return (
    <Suspense fallback={<ListPageSkeleton />}>
      <AlertsPage />
    </Suspense>
  );
}
