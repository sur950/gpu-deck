import type { Metadata } from 'next';
import { Suspense } from 'react';
import { ListPageSkeleton } from '@/components/shared/ListPageSkeleton';
import { NodeDetail } from '@/app/(dashboard)/nodes/_components/node-detail';

export const metadata: Metadata = { title: 'Node details' };

export default function NodeDetailPage() {
  return (
    <Suspense fallback={<ListPageSkeleton stats={4} rows={5} />}>
      <NodeDetail />
    </Suspense>
  );
}
