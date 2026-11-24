import type { Metadata } from 'next';
import { Suspense } from 'react';
import { ListPageSkeleton } from '@/components/shared/ListPageSkeleton';
import { BenchmarkNew } from '@/app/(dashboard)/benchmarks/_components/benchmark-pages';

export const metadata: Metadata = { title: 'New benchmark' };

export default function Page() {
  return (
    <Suspense fallback={<ListPageSkeleton stats={0} />}>
      <BenchmarkNew />
    </Suspense>
  );
}
