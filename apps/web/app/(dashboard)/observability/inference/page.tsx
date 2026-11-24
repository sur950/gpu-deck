import type { Metadata } from 'next';
import { Suspense } from 'react';
import { MetricsPageSkeleton } from '@/components/observability/page-skeletons';
import { InferenceView } from '@/app/(dashboard)/observability/inference/_components/inference-view';

export const metadata: Metadata = { title: 'Inference' };

export default function ObservabilityInferencePage() {
  return (
    <Suspense fallback={<MetricsPageSkeleton charts={6} />}>
      <InferenceView />
    </Suspense>
  );
}
