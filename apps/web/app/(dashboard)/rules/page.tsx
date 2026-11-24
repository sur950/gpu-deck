import type { Metadata } from 'next';
import { Suspense } from 'react';
import { ListPageSkeleton } from '@/components/shared/ListPageSkeleton';
import { RulesList } from '@/app/(dashboard)/rules/_components/rules-list';

export const metadata: Metadata = { title: 'Rules' };

export default function RulesPage() {
  return (
    <Suspense fallback={<ListPageSkeleton />}>
      <RulesList />
    </Suspense>
  );
}
