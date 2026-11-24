import type { Metadata } from 'next';
import { Suspense } from 'react';
import { ListPageSkeleton } from '@/components/shared/ListPageSkeleton';
import { AuditLogPage } from '@/app/(dashboard)/audit-log/_components/audit-log-page';

export const metadata: Metadata = { title: 'Audit log' };

export default function AuditLogRoute() {
  return (
    <Suspense fallback={<ListPageSkeleton stats={0} />}>
      <AuditLogPage />
    </Suspense>
  );
}
