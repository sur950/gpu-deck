'use client';

import { useParams } from 'next/navigation';
import { PageContainer } from '@/components/shared/PageContainer';
import { PageHeader } from '@/components/shared/PageHeader';
import { ListPageSkeleton } from '@/components/shared/ListPageSkeleton';
import { ErrorState } from '@/components/shared/ErrorState';
import { DeploymentWizard } from '@/components/models/wizard/deployment-wizard';
import { draftFromDeployment } from '@/components/models/wizard/draft';
import { useDeployment } from '@/hooks/api/models-deployments';

export function DeploymentEdit() {
  const { id } = useParams<{ id: string }>();
  const { data, isLoading, isError, refetch } = useDeployment(id);
  if (isLoading) return <ListPageSkeleton stats={0} rows={6} />;
  if (isError || !data) {
    return (
      <PageContainer>
        <ErrorState title="Deployment not found" description="It may have been deleted, or the control plane is unreachable." onRetry={() => void refetch()} />
      </PageContainer>
    );
  }
  return (
    <PageContainer>
      <PageHeader title={`Edit ${data.name}`} description="Replica and traffic changes apply live; engine, model version, GPU, slice and flag changes roll through the replicas one at a time." />
      <DeploymentWizard key={data.id} initial={draftFromDeployment(data)} current={data} />
    </PageContainer>
  );
}
