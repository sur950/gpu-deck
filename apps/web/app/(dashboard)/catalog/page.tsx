import type { Metadata } from 'next';
import { Suspense } from 'react';
import { ListPageSkeleton } from '@/components/shared/ListPageSkeleton';
import { CatalogList } from '@/app/(dashboard)/catalog/_components/catalog-list';

export const metadata: Metadata = { title: 'Catalog' };

export default function CatalogPage() {
  return (
    <Suspense fallback={<ListPageSkeleton />}>
      <CatalogList />
    </Suspense>
  );
}
