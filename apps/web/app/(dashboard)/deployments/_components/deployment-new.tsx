'use client';

import { useMemo } from 'react';
import { useSearchParams } from 'next/navigation';
import { PageContainer } from '@/components/shared/PageContainer';
import { PageHeader } from '@/components/shared/PageHeader';
import { DeploymentWizard } from '@/components/models/wizard/deployment-wizard';
import { draftFromParams } from '@/components/models/wizard/draft';

export function DeploymentNew() {
  const searchParams = useSearchParams();
  const initial = useMemo(() => draftFromParams(new URLSearchParams(searchParams.toString())), [searchParams]);
  return (
    <PageContainer>
      <PageHeader title="New deployment" description="Pick a model, let GPU Deck check engines and preview placement, tune it, then start it." />
      <DeploymentWizard initial={initial} />
    </PageContainer>
  );
}
