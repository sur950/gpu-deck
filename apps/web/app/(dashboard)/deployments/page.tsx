import type { Metadata } from 'next';
import { Suspense } from 'react';
import { ListPageSkeleton } from '@/components/shared/ListPageSkeleton';
import { DeploymentsList } from '@/app/(dashboard)/deployments/_components/deployments-list';

export const metadata: Metadata = { title: 'Deployments' };

export default function Page() {
  return (
    <Suspense fallback={<ListPageSkeleton />}>
      <DeploymentsList />
    </Suspense>
  );
}
