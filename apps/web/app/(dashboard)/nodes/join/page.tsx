import type { Metadata } from 'next';
import { Suspense } from 'react';
import { ListPageSkeleton } from '@/components/shared/ListPageSkeleton';
import { JoinNode } from '@/app/(dashboard)/nodes/_components/join-node';

export const metadata: Metadata = { title: 'Add a node' };

export default function JoinNodePage() {
  return (
    <Suspense fallback={<ListPageSkeleton stats={0} rows={4} />}>
      <JoinNode />
    </Suspense>
  );
}
