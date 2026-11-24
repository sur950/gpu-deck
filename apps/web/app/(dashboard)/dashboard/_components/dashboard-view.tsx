'use client';

import Link from 'next/link';
import { KeyRound, Rocket, Server, Workflow } from 'lucide-react';
import { buttonVariants } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { EmptyState } from '@/components/shared/EmptyState';
import { ErrorState } from '@/components/shared/ErrorState';
import { PageContainer } from '@/components/shared/PageContainer';
import { QuickActions } from '@/components/shared/QuickActions';
import type { QuickAction } from '@/components/shared/QuickActions';
import { DashboardSkeleton } from '@/components/charts/dashboard-skeleton';
import { STEP_SECONDS_BY_ID } from '@/components/charts/series-utils';
import { DashboardBand } from '@/app/(dashboard)/dashboard/_components/dashboard-band';
import { KpiGrid } from '@/app/(dashboard)/dashboard/_components/kpi-grid';
import { TrafficCard } from '@/app/(dashboard)/dashboard/_components/traffic-card';
import { FleetCard } from '@/app/(dashboard)/dashboard/_components/fleet-card';
import { ActivityCard, AlertsCard, TopModelsCard } from '@/app/(dashboard)/dashboard/_components/activity-cards';
import { useOverview, useOverviewTrends } from '@/hooks/use-overview';
import { useDevices, useNodes } from '@/hooks/use-topology';
import { isNodeConnected } from '@/components/fleet/node-status';
import { useDeployments } from '@/hooks/use-metrics';
import { readEngineSamples, readGpuSamples } from '@/hooks/use-live-events';
import { useTimeRange } from '@/hooks/use-time-range';

const QUICK_ACTIONS: QuickAction[] = [
  { id: 'deploy', title: 'Deploy a model', description: 'Pick a model, an engine and the GPUs it runs on.', icon: Rocket, href: '/deployments/new' },
  { id: 'rule', title: 'Write a rule', description: 'Unload idle models, grow on load, heal crashed engines.', icon: Workflow, href: '/rules' },
  { id: 'key', title: 'Create an API key', description: 'One key for the OpenAI, Anthropic and Ollama endpoints.', icon: KeyRound, href: '/api-keys' },
];

/** Overview dashboard: band → KPIs → traffic + fleet → top models, activity, alerts → quick actions. */
export function DashboardView() {
  const range = useTimeRange('24h');
  const overview = useOverview();
  const trends = useOverviewTrends(range);
  const devices = useDevices();
  const nodes = useNodes();
  const deployments = useDeployments();

  if (overview.isPending) return <DashboardSkeleton />;

  if (overview.isError || !overview.data) {
    return (
      <PageContainer>
        <DashboardBand range={range.rangeId} onRangeChange={range.setRange} />
        <ErrorState
          title="Couldn't load the overview"
          description="The control plane did not answer. It retries on its own; you can also try now."
          onRetry={() => void overview.refetch()}
        />
      </PageContainer>
    );
  }

  const data = overview.data;
  const gpuLive = readGpuSamples(overview.live.latest.metrics);
  const engineLive = readEngineSamples(overview.live.latest.metrics);
  const stepSeconds = STEP_SECONDS_BY_ID[range.step] ?? 60;
  const rangeLabel = range.preset.label.toLowerCase();

  return (
    <PageContainer>
      <DashboardBand counts={data.counts} connectedNodes={nodes.data ? nodes.data.filter((n) => isNodeConnected(n)).length : undefined} liveStatus={overview.live.status} range={range.rangeId} onRangeChange={range.setRange} />

      {data.counts.nodes === 0 ? (
        <Card className="rounded-xl">
          <CardContent>
            <EmptyState
              icon={Server}
              title="Join your first GPU node"
              description="Create a one-time join token, run the install command on a Linux or macOS machine, and its GPUs, models and metrics appear here."
              action={
                <Link href="/nodes" className={buttonVariants({ size: 'sm' })}>
                  Add a node
                </Link>
              }
            />
          </CardContent>
        </Card>
      ) : (
        <>
          <KpiGrid overview={data} trends={trends} gpuLive={gpuLive} engineLive={engineLive} stepSeconds={stepSeconds} />

          <section className="grid gap-4 lg:grid-cols-3">
            <TrafficCard className="lg:col-span-2" trends={trends} fromMs={range.fromMs} toMs={range.toMs} stepSeconds={stepSeconds} rangeLabel={rangeLabel} />
            <FleetCard devices={devices.data} nodes={nodes.data} gpuLive={gpuLive} isLoading={devices.isPending} />
          </section>

          <section className="grid gap-4 lg:grid-cols-3">
            <TopModelsCard usage={trends.usage} deployments={deployments.data} rangeLabel={rangeLabel} />
            <ActivityCard runs={data.recentRuleRuns} swaps={data.recentSwaps} />
            <AlertsCard alerts={data.recentAlerts} openCount={data.counts.alertsOpen} />
          </section>

          <QuickActions actions={QUICK_ACTIONS} />
        </>
      )}
    </PageContainer>
  );
}
