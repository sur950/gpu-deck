import type { Metadata } from 'next';
import { Suspense } from 'react';
import { ListPageSkeleton } from '@/components/shared/ListPageSkeleton';
import { BenchmarkDetail } from '@/app/(dashboard)/benchmarks/_components/benchmark-pages';

export const metadata: Metadata = { title: 'Benchmark' };

export default function Page() {
  return (
    <Suspense fallback={<ListPageSkeleton stats={0} />}>
      <BenchmarkDetail />
    </Suspense>
  );
}
