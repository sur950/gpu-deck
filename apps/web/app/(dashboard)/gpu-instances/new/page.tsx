import type { Metadata } from 'next';
import { Suspense } from 'react';
import { ListPageSkeleton } from '@/components/shared/ListPageSkeleton';
import { GpuInstanceNew } from '@/app/(dashboard)/gpu-instances/_components/gpu-instance-new';

export const metadata: Metadata = { title: 'Request an SSH GPU instance' };

export default function GpuInstanceNewPage() {
  return (
    <Suspense fallback={<ListPageSkeleton stats={0} rows={4} />}>
      <GpuInstanceNew />
    </Suspense>
  );
}
