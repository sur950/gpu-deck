import type { Metadata } from 'next';
import { Suspense } from 'react';
import { ListPageSkeleton } from '@/components/shared/ListPageSkeleton';
import { CredentialsPage } from '@/app/(dashboard)/credentials/_components/credentials-page';

export const metadata: Metadata = { title: 'Credentials' };

export default function CredentialsRoute() {
  return (
    <Suspense fallback={<ListPageSkeleton />}>
      <CredentialsPage />
    </Suspense>
  );
}
