import type { Metadata } from 'next';
import { Suspense } from 'react';
import { StreamPageSkeleton } from '@/components/observability/page-skeletons';
import { LogsView } from '@/app/(dashboard)/observability/logs/_components/logs-view';

export const metadata: Metadata = { title: 'Logs' };

export default function ObservabilityLogsPage() {
  return (
    <Suspense fallback={<StreamPageSkeleton />}>
      <LogsView />
    </Suspense>
  );
}
