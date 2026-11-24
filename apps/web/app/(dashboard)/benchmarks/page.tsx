import type { Metadata } from 'next';
import { Suspense } from 'react';
import { ListPageSkeleton } from '@/components/shared/ListPageSkeleton';
import { BenchmarksList } from '@/app/(dashboard)/benchmarks/_components/benchmark-pages';

export const metadata: Metadata = { title: 'Benchmarks' };

export default function Page() {
  return (
    <Suspense fallback={<ListPageSkeleton stats={0} />}>
      <BenchmarksList />
    </Suspense>
  );
}
