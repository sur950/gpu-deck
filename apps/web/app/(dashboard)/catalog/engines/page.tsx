import type { Metadata } from 'next';
import { EnginesPage } from '@/app/(dashboard)/catalog/_components/engines-page';

export const metadata: Metadata = { title: 'Engines' };

export default function CatalogEnginesPage() {
  return <EnginesPage />;
}
