import type { Metadata } from 'next';
import { Suspense } from 'react';
import { ListPageSkeleton } from '@/components/shared/ListPageSkeleton';
import { CloudList } from '@/app/(dashboard)/cloud/_components/cloud-list';

export const metadata: Metadata = { title: 'Cloud VMs' };

export default function CloudPage() {
  return (
    <Suspense fallback={<ListPageSkeleton />}>
      <CloudList />
    </Suspense>
  );
}
