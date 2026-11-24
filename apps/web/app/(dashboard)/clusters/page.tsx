import type { Metadata } from 'next';
import { Suspense } from 'react';
import { ListPageSkeleton } from '@/components/shared/ListPageSkeleton';
import { ClustersList } from '@/app/(dashboard)/clusters/_components/clusters-list';

export const metadata: Metadata = { title: 'Clusters' };

export default function ClustersPage() {
  return (
    <Suspense fallback={<ListPageSkeleton />}>
      <ClustersList />
    </Suspense>
  );
}
