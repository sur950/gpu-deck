import type { Metadata } from 'next';
import { Suspense } from 'react';
import { DashboardSkeleton } from '@/components/charts/dashboard-skeleton';
import { RuleDetail } from '@/app/(dashboard)/rules/_components/rule-detail';

export const metadata: Metadata = { title: 'Rule details' };

export default function RuleDetailPage() {
  return (
    <Suspense fallback={<DashboardSkeleton />}>
      <RuleDetail />
    </Suspense>
  );
}
