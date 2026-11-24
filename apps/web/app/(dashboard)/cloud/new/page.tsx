import type { Metadata } from 'next';
import { Suspense } from 'react';
import { ListPageSkeleton } from '@/components/shared/ListPageSkeleton';
import { CloudProvision } from '@/app/(dashboard)/cloud/_components/cloud-provision';

export const metadata: Metadata = { title: 'Provision a GPU VM' };

export default function CloudProvisionPage() {
  return (
    <Suspense fallback={<ListPageSkeleton stats={0} rows={4} />}>
      <CloudProvision />
    </Suspense>
  );
}
