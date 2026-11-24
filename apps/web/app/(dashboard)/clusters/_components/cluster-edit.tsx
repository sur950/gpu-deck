'use client';

import { useParams, useRouter } from 'next/navigation';
import { PageContainer } from '@/components/shared/PageContainer';
import { PageHeader } from '@/components/shared/PageHeader';
import { ListPageSkeleton } from '@/components/shared/ListPageSkeleton';
import { ErrorState } from '@/components/shared/ErrorState';
import { ClusterForm } from '@/components/fleet/cluster-form';
import { useCluster, useUpdateCluster } from '@/hooks/api/fleet';

export function ClusterEdit() {
  const router = useRouter();
  const { id } = useParams<{ id: string }>();
  const { data: cluster, isLoading, isError, refetch } = useCluster(id);
  const update = useUpdateCluster();

  if (isLoading) return <ListPageSkeleton stats={0} rows={4} />;
  if (isError || !cluster) {
    return (
      <PageContainer>
        <ErrorState title="Cluster not found" description="It may have been deleted, or the control plane is unreachable." onRetry={() => void refetch()} />
      </PageContainer>
    );
  }

  return (
    <PageContainer className="max-w-5xl">
      <PageHeader title={`Edit ${cluster.name}`} description="Changes apply to new placements; running instances are not touched." />
      <ClusterForm
        cluster={cluster}
        isPending={update.isPending}
        onCancel={() => router.push(`/clusters/${cluster.id}`)}
        onSubmit={(payload) =>
          update.mutate({ id: cluster.id, body: payload }, { onSuccess: () => router.push(`/clusters/${cluster.id}`) })
        }
      />
    </PageContainer>
  );
}
