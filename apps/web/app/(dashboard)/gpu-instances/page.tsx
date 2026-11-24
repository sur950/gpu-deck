import type { Metadata } from 'next';
import { Suspense } from 'react';
import { ListPageSkeleton } from '@/components/shared/ListPageSkeleton';
import { GpuInstancesList } from '@/app/(dashboard)/gpu-instances/_components/gpu-instances-list';

export const metadata: Metadata = { title: 'SSH GPU instances' };

export default function GpuInstancesPage() {
  return (
    <Suspense fallback={<ListPageSkeleton />}>
      <GpuInstancesList />
    </Suspense>
  );
}
