import type { Metadata } from 'next';
import { Suspense } from 'react';
import { ListPageSkeleton } from '@/components/shared/ListPageSkeleton';
import { NodesList } from '@/app/(dashboard)/nodes/_components/nodes-list';

export const metadata: Metadata = { title: 'Nodes' };

export default function NodesPage() {
  return (
    <Suspense fallback={<ListPageSkeleton />}>
      <NodesList />
    </Suspense>
  );
}
