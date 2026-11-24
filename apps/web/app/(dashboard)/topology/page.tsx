import type { Metadata } from 'next';
import { Suspense } from 'react';
import { MetricsPageSkeleton } from '@/components/observability/page-skeletons';
import { TopologyView } from '@/app/(dashboard)/topology/_components/topology-view';

export const metadata: Metadata = { title: 'Topology' };

export default function TopologyPage() {
  return (
    <Suspense fallback={<MetricsPageSkeleton tiles={0} charts={2} tall />}>
      <TopologyView />
    </Suspense>
  );
}
