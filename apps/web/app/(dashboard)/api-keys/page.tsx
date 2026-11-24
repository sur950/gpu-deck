import type { Metadata } from 'next';
import { Suspense } from 'react';
import { ListPageSkeleton } from '@/components/shared/ListPageSkeleton';
import { ApiKeysList } from '@/app/(dashboard)/api-keys/_components/api-keys-list';

export const metadata: Metadata = { title: 'API keys' };

export default function ApiKeysPage() {
  return (
    <Suspense fallback={<ListPageSkeleton />}>
      <ApiKeysList />
    </Suspense>
  );
}
