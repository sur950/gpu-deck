import type { Metadata } from 'next';
import { Suspense } from 'react';
import { ListPageSkeleton } from '@/components/shared/ListPageSkeleton';
import { ClusterCreate } from '@/app/(dashboard)/clusters/_components/cluster-create';

export const metadata: Metadata = { title: 'New cluster' };

export default function NewClusterPage() {
  return (
    <Suspense fallback={<ListPageSkeleton stats={0} rows={4} />}>
      <ClusterCreate />
    </Suspense>
  );
}
