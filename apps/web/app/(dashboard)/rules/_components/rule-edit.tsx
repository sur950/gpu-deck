'use client';

import { useParams } from 'next/navigation';
import { PageContainer } from '@/components/shared/PageContainer';
import { PageHeader } from '@/components/shared/PageHeader';
import { ErrorState } from '@/components/shared/ErrorState';
import { DashboardSkeleton } from '@/components/charts/dashboard-skeleton';
import { useRule } from '@/hooks/api/automation';
import { RuleBuilder } from '@/components/automation/rule-builder';

export function RuleEdit() {
  const { id } = useParams<{ id: string }>();
  const { data: rule, isLoading, isError, refetch } = useRule(id);

  if (isLoading) return <DashboardSkeleton />;
  if (isError || !rule) {
    return (
      <PageContainer>
        <ErrorState title="Rule not found" description="It may have been deleted, or the control plane did not answer." onRetry={() => void refetch()} />
      </PageContainer>
    );
  }
  return (
    <PageContainer>
      <PageHeader title={`Edit “${rule.name}”`} description="Changes apply from the next evaluation. Switching dry run off lets the rule act." />
      <RuleBuilder key={rule.id} rule={rule} />
    </PageContainer>
  );
}
