import type { Metadata } from 'next';
import { Suspense } from 'react';
import { DashboardSkeleton } from '@/components/charts/dashboard-skeleton';
import { RuleNew } from '@/app/(dashboard)/rules/_components/rule-new';

export const metadata: Metadata = { title: 'New rule' };

export default function NewRulePage() {
  return (
    <Suspense fallback={<DashboardSkeleton />}>
      <RuleNew />
    </Suspense>
  );
}
