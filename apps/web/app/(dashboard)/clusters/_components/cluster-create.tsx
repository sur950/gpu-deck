'use client';

import { useRouter, useSearchParams } from 'next/navigation';
import { PageContainer } from '@/components/shared/PageContainer';
import { PageHeader } from '@/components/shared/PageHeader';
import { ClusterForm } from '@/components/fleet/cluster-form';
import { useCreateCluster } from '@/hooks/api/fleet';
import { CLUSTER_TYPE_LABELS, type ClusterCreate as ClusterCreateBody, type ClusterType } from '@/types/fleet';

export function ClusterCreate() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const create = useCreateCluster();
  const presetType = searchParams.get('type');
  const type = presetType && presetType in CLUSTER_TYPE_LABELS ? (presetType as ClusterType) : undefined;

  return (
    <PageContainer className="max-w-5xl">
      <PageHeader title="New cluster" description="Group GPU machines that share a network and a credential." />
      <ClusterForm
        presetType={type}
        isPending={create.isPending}
        onCancel={() => router.push('/clusters')}
        onSubmit={(payload) =>
          create.mutate(payload as ClusterCreateBody, {
            onSuccess: (cluster) => router.push(`/clusters/${cluster.id}`),
          })
        }
      />
    </PageContainer>
  );
}
