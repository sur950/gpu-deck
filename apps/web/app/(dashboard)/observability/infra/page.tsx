import type { Metadata } from 'next';
import { Suspense } from 'react';
import { MetricsPageSkeleton } from '@/components/observability/page-skeletons';
import { InfraView } from '@/app/(dashboard)/observability/infra/_components/infra-view';

export const metadata: Metadata = { title: 'GPUs / infra' };

export default function ObservabilityInfraPage() {
  return (
    <Suspense fallback={<MetricsPageSkeleton />}>
      <InfraView />
    </Suspense>
  );
}
