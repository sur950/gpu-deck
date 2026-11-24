import type { Metadata } from 'next';
import { Suspense } from 'react';
import { ListPageSkeleton } from '@/components/shared/ListPageSkeleton';
import { SwapsList } from '@/app/(dashboard)/swaps/_components/swaps-list';

export const metadata: Metadata = { title: 'Swaps' };

export default function SwapsPage() {
  return (
    <Suspense fallback={<ListPageSkeleton />}>
      <SwapsList />
    </Suspense>
  );
}
