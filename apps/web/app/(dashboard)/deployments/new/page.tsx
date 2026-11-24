import type { Metadata } from 'next';
import { Suspense } from 'react';
import { ListPageSkeleton } from '@/components/shared/ListPageSkeleton';
import { DeploymentNew } from '@/app/(dashboard)/deployments/_components/deployment-new';

export const metadata: Metadata = { title: 'New deployment' };

export default function Page() {
  return (
    <Suspense fallback={<ListPageSkeleton stats={0} rows={6} />}>
      <DeploymentNew />
    </Suspense>
  );
}
