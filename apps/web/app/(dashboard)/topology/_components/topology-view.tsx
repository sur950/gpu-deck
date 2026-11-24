'use client';

import { useMemo, useState } from 'react';
import { Network } from 'lucide-react';
import { cn } from '@/lib/utils';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Switch } from '@/components/ui/switch';
import { Label } from '@/components/ui/label';
import { PageContainer } from '@/components/shared/PageContainer';
import { PageHeader } from '@/components/shared/PageHeader';
import { EmptyState } from '@/components/shared/EmptyState';
import { ErrorState } from '@/components/shared/ErrorState';
import { ScopeSelect } from '@/components/charts/range-bar';
import { LiveIndicator } from '@/components/charts/live-indicator';
import { ENTITY_OTHER } from '@/components/charts/entity-colors';
import { MetricsPageSkeleton } from '@/components/observability/page-skeletons';
import { engineLabel } from '@/components/observability/labels';
import { LOAD_METRICS, TopologyMap } from '@/components/topology/topology-map';
import { isNodeConnected } from '@/components/fleet/node-status';
import type { LiveIndex, LoadMetric } from '@/components/topology/topology-map';
import { useTopology } from '@/hooks/use-topology';
import { useDeployments } from '@/hooks/use-metrics';
import { readEngineSamples, readGpuSamples, useLiveEvents } from '@/hooks/use-live-events';
import { useDeploymentColors } from '@/hooks/use-entity-colors';
import { useUrlState } from '@/hooks/use-url-state';
import { formatGiB, formatGiBPair } from '@/utils/metrics-format';

const DEFAULTS = { color: 'util', cluster: '', stopped: false };

/** Fleet › Topology: live map of clusters, nodes, GPUs, slices and instances, coloured by load. */
export function TopologyView() {
  const { params, setParams } = useUrlState(DEFAULTS);
  const metric = (LOAD_METRICS.some((m) => m.id === params.color) ? params.color : 'util') as LoadMetric;
  const topology = useTopology({ clusterId: params.cluster || undefined, includeStopped: params.stopped });
  const allClusters = useTopology();
  const deployments = useDeployments();
  const colors = useDeploymentColors();
  const live = useLiveEvents({ topics: ['metrics'], scope: { clusterId: params.cluster || undefined }, intervalSeconds: 2 });
  const [hovered, setHovered] = useState<string | null>(null);
  const [pinned, setPinned] = useState<string | null>(null);
  const focus = hovered ?? pinned;

  const index: LiveIndex = useMemo(
    () => ({
      gpu: new Map(readGpuSamples(live.latest.metrics).map((g) => [g.deviceId, g])),
      engine: new Map(readEngineSamples(live.latest.metrics).map((e) => [e.instanceId, e])),
    }),
    [live.latest.metrics],
  );

  const clusters = useMemo(() => topology.data?.clusters ?? [], [topology.data]);
  const summary = useMemo(() => {
    const nodes = clusters.flatMap((c) => c.nodes);
    const devices = nodes.flatMap((n) => n.devices);
    const instances = devices.flatMap((d) => d.instances);
    const unique = new Map(instances.map((i) => [i.id, i]));
    const reserved = devices.reduce((s, d) => s + d.device.slices.filter((x) => x.status !== 'released').reduce((a, x) => a + x.vramBytes, 0), 0);
    const total = devices.reduce((s, d) => s + d.device.vramTotalBytes, 0);
    return { nodes: nodes.length, online: nodes.filter((n) => isNodeConnected(n.node)).length, devices: devices.length, instances: unique.size, reserved, total };
  }, [clusters]);

  // Deployments present on the map, with where they run.
  const placed = useMemo(() => {
    const byDeployment = new Map<string, { name: string; engine: string | null; instances: number; gpus: Set<string>; active: number; waiting: number }>();
    for (const c of clusters)
      for (const n of c.nodes)
        for (const d of n.devices)
          for (const i of d.instances) {
            const entry = byDeployment.get(i.deploymentId) ?? { name: i.deploymentName, engine: i.engine, instances: 0, gpus: new Set<string>(), active: 0, waiting: 0 };
            entry.gpus.add(d.device.id);
            byDeployment.set(i.deploymentId, entry);
          }
    const seen = new Set<string>();
    for (const c of clusters)
      for (const n of c.nodes)
        for (const d of n.devices)
          for (const i of d.instances) {
            if (seen.has(i.id)) continue;
            seen.add(i.id);
            const entry = byDeployment.get(i.deploymentId);
            if (!entry) continue;
            const s = index.engine.get(i.id);
            entry.instances += 1;
            entry.active += s?.activeSessions ?? i.activeSessions ?? 0;
            entry.waiting += s?.waitingSessions ?? i.waitingSessions ?? 0;
          }
    return [...byDeployment.entries()].sort((a, b) => a[1].name.localeCompare(b[1].name));
  }, [clusters, index.engine]);

  const header = (
    <PageHeader
      title="Topology"
      description="Live map of clusters, nodes, GPUs and the model instances placed on them."
      actions={<LiveIndicator status={live.status} detail="2 s" />}
    />
  );

  if (topology.isPending) return <MetricsPageSkeleton tiles={0} charts={2} tall />;
  if (topology.isError) {
    return (
      <PageContainer>
        {header}
        <ErrorState title="Couldn't load the topology" description="The fleet map did not load. Try again in a moment." onRetry={() => void topology.refetch()} />
      </PageContainer>
    );
  }

  const legend = LOAD_METRICS.find((m) => m.id === metric)?.legend ?? '';

  return (
    <PageContainer>
      {header}

      <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
        <div className="flex flex-wrap items-center gap-2">
          <div role="radiogroup" aria-label="Colour by" className="inline-flex h-9 items-center gap-0.5 rounded-full border border-border bg-background/60 p-0.5">
            <span className="ml-2.5 mr-1 text-[11px] font-medium text-muted-foreground">Fill by</span>
            {LOAD_METRICS.map((m) => (
              <button
                key={m.id}
                type="button"
                role="radio"
                aria-checked={metric === m.id}
                onClick={() => setParams({ color: m.id })}
                className={cn(
                  'h-7 rounded-full px-3 text-xs font-medium outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring/30',
                  metric === m.id ? 'bg-foreground text-background shadow-premium' : 'text-muted-foreground hover:bg-accent hover:text-foreground',
                )}
              >
                {m.label}
              </button>
            ))}
          </div>
          {(allClusters.data?.clusters.length ?? 0) > 1 && (
            <ScopeSelect
              value={params.cluster}
              onValueChange={(v) => setParams({ cluster: v })}
              placeholder="All clusters"
              options={(allClusters.data?.clusters ?? []).map((c) => ({ value: c.cluster.id, label: c.cluster.name }))}
              ariaLabel="Cluster"
            />
          )}
          <div className="flex h-9 items-center gap-2 rounded-full border border-border bg-background/60 px-3">
            <Switch id="show-stopped" checked={params.stopped} onCheckedChange={(v) => setParams({ stopped: Boolean(v) })} />
            <Label htmlFor="show-stopped" className="text-xs font-normal text-muted-foreground">
              Show stopped instances
            </Label>
          </div>
        </div>
        <p className="text-xs text-muted-foreground tabular-nums">
          {summary.online}/{summary.nodes} nodes connected · {summary.devices} GPUs · {summary.instances} instances · {formatGiBPair(summary.reserved, summary.total)} reserved
        </p>
      </div>

      {clusters.length === 0 || summary.nodes === 0 ? (
        <Card className="rounded-xl">
          <CardContent>
            <EmptyState icon={Network} title="Nothing to map yet" description="The topology fills in as nodes join and models are deployed." />
          </CardContent>
        </Card>
      ) : (
        <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_300px]">
          <div className="relative overflow-hidden rounded-2xl border border-border bg-card/40 p-5 shadow-premium sm:p-6">
            <div aria-hidden className="bg-dot-grid pointer-events-none absolute inset-0 [mask-image:linear-gradient(to_bottom,black,transparent_85%)]" />
            <div className="relative">
              <TopologyMap clusters={clusters} live={index} metric={metric} colors={colors} focus={focus} />
            </div>
          </div>

          <aside className="space-y-4">
            <Card className="gap-0 rounded-xl py-0">
              <CardHeader className="border-b border-border px-5 py-4">
                <CardTitle className="text-sm font-semibold">Deployments</CardTitle>
                <CardDescription>Hover to trace where each one runs; click to pin</CardDescription>
              </CardHeader>
              <CardContent className="px-2 py-2">
                {placed.length === 0 ? (
                  <p className="px-3 py-4 text-xs text-muted-foreground">No model instances placed.</p>
                ) : (
                  <ul onMouseLeave={() => setHovered(null)}>
                    {placed.map(([id, p]) => {
                      const dep = deployments.data?.find((d) => d.id === id);
                      const isPinned = pinned === id;
                      return (
                        <li key={id}>
                          <button
                            type="button"
                            aria-pressed={isPinned}
                            onMouseEnter={() => setHovered(id)}
                            onFocus={() => setHovered(id)}
                            onBlur={() => setHovered(null)}
                            onClick={() => setPinned((cur) => (cur === id ? null : id))}
                            className={cn(
                              'flex w-full items-start gap-2.5 rounded-lg px-3 py-2 text-left outline-none transition-colors hover:bg-accent focus-visible:bg-accent',
                              isPinned && 'bg-accent ring-1 ring-foreground/15',
                            )}
                          >
                            <span aria-hidden className="mt-1 size-2.5 shrink-0 rounded-[3px]" style={{ backgroundColor: colors.get(id) ?? ENTITY_OTHER }} />
                            <span className="min-w-0 flex-1">
                              <span className="block truncate text-[13px] font-semibold">{p.name}</span>
                              <span className="block text-[11px] text-muted-foreground tabular-nums">
                                {engineLabel(p.engine)} · {p.instances} instance{p.instances === 1 ? '' : 's'} on {p.gpus.size} GPU{p.gpus.size === 1 ? '' : 's'}
                                {dep?.priority ? ` · ${dep.priority.replace(/_/g, ' ')}` : ''}
                              </span>
                            </span>
                            <span className="shrink-0 text-right text-[11px] text-muted-foreground tabular-nums">
                              <span className="font-semibold text-foreground">{p.active}</span> active
                              {p.waiting > 0 && <span className="block font-semibold text-warning-dark dark:text-warning-light">{p.waiting} waiting</span>}
                            </span>
                          </button>
                        </li>
                      );
                    })}
                  </ul>
                )}
              </CardContent>
            </Card>

            <Card className="gap-0 rounded-xl py-0">
              <CardHeader className="border-b border-border px-5 py-4">
                <CardTitle className="text-sm font-semibold">Legend</CardTitle>
              </CardHeader>
              <CardContent className="space-y-3 px-5 py-4 text-xs text-muted-foreground">
                <div className="flex items-center gap-3">
                  <span aria-hidden className="relative h-9 w-12 shrink-0 overflow-hidden rounded-md border border-border bg-background/60">
                    <span className="absolute inset-x-0 bottom-0 h-[60%] bg-foreground/[0.07]" />
                    <span className="absolute inset-x-0 bottom-[60%] h-px bg-foreground/35" />
                  </span>
                  <span>{legend}. Tiles above 90% turn amber.</span>
                </div>
                <div className="flex items-center gap-3">
                  <span aria-hidden className="flex h-1.5 w-12 shrink-0 gap-[2px] overflow-hidden rounded-full bg-muted">
                    <span className="h-full w-[45%] rounded-l-full bg-chart-2" />
                    <span className="h-full w-[25%] bg-chart-4" />
                  </span>
                  <span>VRAM slices reserved per deployment; the rest is free.</span>
                </div>
                <div className="flex items-center gap-3">
                  <span className="w-12 shrink-0 text-center font-semibold tabular-nums text-foreground">
                    6/8 <span className="text-warning-dark dark:text-warning-light">+2</span>
                  </span>
                  <span>Active sessions / slots, plus requests waiting.</span>
                </div>
                {topology.data && topology.data.links.length > 0 && (
                  <p>
                    {topology.data.links.length} cross-GPU link{topology.data.links.length === 1 ? '' : 's'} (tensor / pipeline parallel, PD transfer, KV sharing).
                  </p>
                )}
                <p className="text-[11px]">Map refreshes every 15 s; load and sessions every 2 s. {formatGiB(summary.total - summary.reserved)} unreserved.</p>
              </CardContent>
            </Card>
          </aside>
        </div>
      )}
    </PageContainer>
  );
}
