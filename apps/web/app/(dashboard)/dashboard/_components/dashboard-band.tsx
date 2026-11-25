'use client';

import Link from 'next/link';
import { Rocket, Server } from 'lucide-react';
import { cn } from '@/lib/utils';
import { buttonVariants } from '@/components/ui/button';
import { HeaderBand, StatusChip, greeting } from '@/components/shared/HeaderBand';
import type { OverallStatus } from '@/components/shared/HeaderBand';
import { RangePills } from '@/components/charts/range-bar';
import { useAuth } from '@/hooks/use-auth';
import { useBackendHealth } from '@/context/health-check-provider';
import type { LiveStatus } from '@/hooks/use-live-events';
import type { OverviewCounts } from '@/types/overview';

export const DASHBOARD_RANGES = ['1h', '6h', '24h', '7d'];

function overallStatus(status: string): OverallStatus | null {
  if (status === 'healthy') return 'operational';
  if (status === 'degraded') return 'degraded';
  if (status === 'down') return 'outage';
  return null;
}

/** Fleet status: control-plane health first, then node / GPU problems. */
function fleetStatus(control: OverallStatus | null, counts: OverviewCounts | undefined, connected: number | undefined): OverallStatus | null {
  if (control === 'outage') return 'outage';
  if (!counts) return control;
  const up = connected ?? counts.nodesOnline;
  if (counts.nodes > 0 && up === 0) return 'outage';
  if (up < counts.nodes || counts.devicesUnhealthy > 0 || control === 'degraded') return 'degraded';
  return control ?? 'operational';
}

function statusLabel(status: OverallStatus, counts: OverviewCounts | undefined, connected: number | undefined): string {
  if (!counts || counts.nodes === 0) {
    return status === 'operational' ? 'Control plane operational' : status === 'degraded' ? 'Control plane degraded' : 'Control plane unreachable';
  }
  // Connected = agent attached and reporting (a drained, monitor-only node counts as connected).
  const nodes = connected === undefined ? `${counts.nodesOnline}/${counts.nodes} nodes online` : `${connected}/${counts.nodes} nodes connected`;
  const gpus = `${counts.devices - counts.devicesUnhealthy}/${counts.devices} GPUs healthy`;
  return `${nodes} · ${gpus}`;
}

const LIVE_DOT: Record<LiveStatus, string> = {
  idle: 'bg-muted-foreground/50',
  connecting: 'bg-warning',
  open: 'bg-success',
  reconnecting: 'bg-warning',
  closed: 'bg-error',
};

interface DashboardBandProps {
  counts?: OverviewCounts;
  /** Nodes whose agent is connected (from the node list); falls back to `counts.nodesOnline`. */
  connectedNodes?: number;
  liveStatus?: LiveStatus;
  range?: string;
  onRangeChange?: (id: string) => void;
}

/** Dashboard header band: greeting, fleet status chip, live state, range and the two primary actions. */
export function DashboardBand({ counts, connectedNodes, liveStatus, range, onRangeChange }: DashboardBandProps) {
  const { user } = useAuth();
  const health = useBackendHealth();
  const status = fleetStatus(overallStatus(health.status), counts, connectedNodes);
  // A person's first name; role-style names ("GPU Deck Admin", "admin") get the plain greeting.
  const firstWord = user?.fullName.trim().split(/\s+/)[0] ?? '';
  const firstName = firstWord && !/admin/i.test(user?.fullName ?? '') && !/^[A-Z0-9]{2,}$/.test(firstWord) ? firstWord : null;

  return (
    <HeaderBand
      eyebrow={
        <div className="flex flex-wrap items-center gap-2">
          {status && <StatusChip status={status} label={statusLabel(status, counts, connectedNodes)} />}
          {counts && counts.nodes > 0 && (
            <span className="inline-flex items-center gap-2 rounded-full border border-border bg-background/70 px-2.5 py-1 text-[11px] font-medium text-muted-foreground tabular-nums backdrop-blur">
              {counts.instancesReady} instances ready · {counts.deploymentsRunning}/{counts.deployments} deployments running
            </span>
          )}
          {liveStatus && (
            <span className="inline-flex items-center gap-1.5 rounded-full border border-border bg-background/70 px-2.5 py-1 text-[11px] font-medium text-muted-foreground backdrop-blur">
              <span aria-hidden className={cn('size-1.5 rounded-full', LIVE_DOT[liveStatus])} />
              {liveStatus === 'open' ? 'Live · 2 s' : liveStatus === 'reconnecting' ? 'Reconnecting' : liveStatus === 'closed' ? 'Stream closed' : 'Connecting'}
            </span>
          )}
        </div>
      }
      title={`${greeting()}${firstName ? `, ${firstName}` : ''}`}
      description="GPU load, model sessions and rule actions across every cluster this control plane manages."
      actions={
        <div className="flex flex-wrap items-center gap-2 xl:flex-nowrap">
          {range && onRangeChange && <RangePills value={range} onChange={onRangeChange} presets={DASHBOARD_RANGES} />}
          <Link href="/nodes" className={cn(buttonVariants({ variant: 'outline' }), 'h-9 shrink-0 gap-1.5 rounded-full px-4')}>
            <Server className="size-4" />
            Join a node
          </Link>
          <Link href="/deployments/new" className={cn(buttonVariants(), 'h-9 shrink-0 gap-1.5 rounded-full px-4')}>
            <Rocket className="size-4" />
            New deployment
          </Link>
        </div>
      }
    />
  );
}
