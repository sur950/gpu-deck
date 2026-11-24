import type { Metadata } from 'next';
import { ClusterEdit } from '@/app/(dashboard)/clusters/_components/cluster-edit';

export const metadata: Metadata = { title: 'Edit cluster' };

export default function ClusterEditPage() {
  return <ClusterEdit />;
}
