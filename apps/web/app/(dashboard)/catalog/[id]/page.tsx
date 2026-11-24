import type { Metadata } from 'next';
import { ModelDetail } from '@/app/(dashboard)/catalog/_components/model-detail';

export const metadata: Metadata = { title: 'Model details' };

export default function ModelDetailPage() {
  return <ModelDetail />;
}
