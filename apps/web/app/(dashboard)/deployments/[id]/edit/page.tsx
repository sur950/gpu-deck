import type { Metadata } from 'next';
import { DeploymentEdit } from '@/app/(dashboard)/deployments/_components/deployment-edit';

export const metadata: Metadata = { title: 'Edit deployment' };

export default function DeploymentEditPage() {
  return <DeploymentEdit />;
}
