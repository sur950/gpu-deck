import type { Metadata } from 'next';
import { CloudDetail } from '@/app/(dashboard)/cloud/_components/cloud-detail';

export const metadata: Metadata = { title: 'Cloud VM' };

export default function CloudDetailPage() {
  return <CloudDetail />;
}
