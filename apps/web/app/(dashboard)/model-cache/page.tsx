import type { Metadata } from 'next';
import { Suspense } from 'react';
import { ListPageSkeleton } from '@/components/shared/ListPageSkeleton';
import { ModelCachePage } from '@/app/(dashboard)/model-cache/_components/model-cache-page';

export const metadata: Metadata = { title: 'Model cache' };

export default function Page() {
  return (
    <Suspense fallback={<ListPageSkeleton stats={2} />}>
      <ModelCachePage />
    </Suspense>
  );
}
