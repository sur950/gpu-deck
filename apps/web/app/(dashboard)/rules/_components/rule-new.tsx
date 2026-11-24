'use client';

import { useSearchParams } from 'next/navigation';
import { PageContainer } from '@/components/shared/PageContainer';
import { PageHeader } from '@/components/shared/PageHeader';
import { DashboardSkeleton } from '@/components/charts/dashboard-skeleton';
import { useRule } from '@/hooks/api/automation';
import { RULE_TYPES, type RuleType } from '@/hooks/api/automation-api';
import { RuleBuilder } from '@/components/automation/rule-builder';

/** New rule: blank, pre-typed (?type=idle_unload) or a copy of another rule (?from=<id>). */
export function RuleNew() {
  const searchParams = useSearchParams();
  const typeParam = searchParams.get('type');
  const fromId = searchParams.get('from') ?? undefined;
  const template = useRule(fromId);
  const initialType = typeParam && (RULE_TYPES as string[]).includes(typeParam) ? (typeParam as RuleType) : null;

  if (fromId && template.isLoading) return <DashboardSkeleton />;

  return (
    <PageContainer>
      <PageHeader
        title={template.data ? `Duplicate “${template.data.name}”` : 'New rule'}
        description="Choose what should happen, set the thresholds, pick the deployments — the sentence on the right reads it back to you."
      />
      <RuleBuilder key={template.data?.id ?? initialType ?? 'blank'} template={template.data} initialType={initialType} />
    </PageContainer>
  );
}
