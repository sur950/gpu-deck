import type { Metadata } from 'next';
import { Suspense } from 'react';
import { MetricsPageSkeleton } from '@/components/observability/page-skeletons';
import { DeviceDetail } from '@/app/(dashboard)/observability/infra/[deviceId]/_components/device-detail';

export const metadata: Metadata = { title: 'GPU details' };

export default async function GpuDetailPage({ params }: { params: Promise<{ deviceId: string }> }) {
  const { deviceId } = await params;
  return (
    <Suspense fallback={<MetricsPageSkeleton charts={6} />}>
      <DeviceDetail deviceId={deviceId} />
    </Suspense>
  );
}
