import type { Metadata } from 'next';
import { DeploymentDetail } from '@/app/(dashboard)/deployments/_components/deployment-detail';

export const metadata: Metadata = { title: 'Deployment details' };

export default function DeploymentDetailPage() {
  return <DeploymentDetail />;
}
