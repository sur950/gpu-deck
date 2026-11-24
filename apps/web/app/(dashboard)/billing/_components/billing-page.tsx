'use client';

import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { PageContainer } from '@/components/shared/PageContainer';
import { PageHeader } from '@/components/shared/PageHeader';
import { useUrlState } from '@/hooks/use-url-state';
import { OverviewTab } from '@/app/(dashboard)/billing/_components/overview-tab';
import { PlansTab } from '@/app/(dashboard)/billing/_components/plans-tab';
import { ReportsTab } from '@/app/(dashboard)/billing/_components/reports-tab';

const DEFAULTS = { tab: 'overview' };

export function BillingPage() {
  const { params, setParams } = useUrlState(DEFAULTS);
  return (
    <PageContainer>
      <PageHeader title="Billing" description="Cost per API key, model and node from your price plans — including what idle loaded models cost — plus monthly reports." />
      <Tabs value={params.tab} onValueChange={(v) => setParams({ tab: String(v) })}>
        <TabsList className="bg-muted">
          <TabsTrigger value="overview" className="px-3 text-xs">
            This month
          </TabsTrigger>
          <TabsTrigger value="plans" className="px-3 text-xs">
            Price plans
          </TabsTrigger>
          <TabsTrigger value="reports" className="px-3 text-xs">
            Reports
          </TabsTrigger>
        </TabsList>
      </Tabs>
      {params.tab === 'plans' ? <PlansTab /> : params.tab === 'reports' ? <ReportsTab /> : <OverviewTab />}
    </PageContainer>
  );
}
