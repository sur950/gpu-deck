import type { Metadata } from 'next';
import { Suspense } from 'react';
import { StreamPageSkeleton } from '@/components/observability/page-skeletons';
import { TracesView } from '@/app/(dashboard)/observability/traces/_components/traces-view';

export const metadata: Metadata = { title: 'Traces' };

export default function ObservabilityTracesPage() {
  return (
    <Suspense fallback={<StreamPageSkeleton />}>
      <TracesView />
    </Suspense>
  );
}
