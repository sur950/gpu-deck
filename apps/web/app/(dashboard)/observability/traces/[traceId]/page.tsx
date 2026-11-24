import type { Metadata } from 'next';
import { Suspense } from 'react';
import { TraceDetail } from '@/app/(dashboard)/observability/traces/[traceId]/_components/trace-detail';

export const metadata: Metadata = { title: 'Trace details' };

export default async function TraceDetailPage({ params }: { params: Promise<{ traceId: string }> }) {
  const { traceId } = await params;
  return (
    <Suspense>
      <TraceDetail traceId={traceId} />
    </Suspense>
  );
}
