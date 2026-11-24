import type { Metadata } from 'next';
import { Suspense } from 'react';
import { ListPageSkeleton } from '@/components/shared/ListPageSkeleton';
import { AddModel } from '@/app/(dashboard)/catalog/_components/add-model';

export const metadata: Metadata = { title: 'Add a model' };

export default function AddModelPage() {
  return (
    <Suspense fallback={<ListPageSkeleton stats={0} rows={6} />}>
      <AddModel />
    </Suspense>
  );
}
