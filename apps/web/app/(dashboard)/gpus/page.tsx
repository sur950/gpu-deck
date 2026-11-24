import type { Metadata } from 'next';
import { Suspense } from 'react';
import { ListPageSkeleton } from '@/components/shared/ListPageSkeleton';
import { GpusList } from '@/app/(dashboard)/gpus/_components/gpus-list';

export const metadata: Metadata = { title: 'GPUs' };

export default function GpusPage() {
  return (
    <Suspense fallback={<ListPageSkeleton />}>
      <GpusList />
    </Suspense>
  );
}
