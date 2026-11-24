import type { Metadata } from 'next';
import { ClusterDetail } from '@/app/(dashboard)/clusters/_components/cluster-detail';

export const metadata: Metadata = { title: 'Cluster details' };

export default function ClusterDetailPage() {
  return <ClusterDetail />;
}
