'use client';

import { useMemo, useState } from 'react';
import Link from 'next/link';
import { useParams, useRouter } from 'next/navigation';
import { Activity, Cpu, FlaskConical, Gauge, MoreVertical, Pencil, Play, Rocket, RotateCw, Scale, Square, Timer, Trash2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from '@/components/ui/dropdown-menu';
import { PageContainer } from '@/components/shared/PageContainer';
import { ListPageSkeleton } from '@/components/shared/ListPageSkeleton';
import { ErrorState } from '@/components/shared/ErrorState';
import { EmptyState } from '@/components/shared/EmptyState';
import { StatusBadge, toBadgeVariant } from '@/components/shared/StatusBadge';
import { StatCard } from '@/components/shared/stat-card';
import { DetailList } from '@/components/shared/detail-list';
import { ConfirmationDialog } from '@/components/shared/ConfirmationDialog';
import { DeleteConfirmationDialog } from '@/components/shared/DeleteConfirmationDialog';
import { DEPLOYMENT_BADGE } from '@/components/fleet/status';
import { EngineBadge, ModelTypeBadge, PriorityBadge } from '@/components/models/model-badges';
import { InstancesTable } from '@/components/models/instances-table';
import { EndpointsCard, ScaleDialog, TrafficCard } from '@/components/models/deployment-panels';
import { useDevices } from '@/hooks/api/fleet';
import { useDeleteDeployment, useDeployment, useDeploymentSwaps, useRestartDeployment, useStartDeployment, useStopDeployment } from '@/hooks/api/models-deployments';
import { cn } from '@/lib/utils';
import { formatBytes, formatRelativeTime } from '@/utils/format';
import { DEPLOYMENT_STATUS_LABELS, PRESET_LABELS, isExternal } from '@/types/models';
import { ExternalDeploymentDetail } from './external-deployment-detail';

function flagText(flags: Record<string, unknown> | undefined): string[] {
  return Object.entries(flags ?? {}).map(([k, v]) => (v === true ? k : `${k} ${String(v)}`));
}

export function DeploymentDetail() {
  const router = useRouter();
  const { id } = useParams<{ id: string }>();
  const { data: d, isLoading, isError, refetch } = useDeployment(id);
  const swaps = useDeploymentSwaps(id);
  const devices = useDevices({ page_size: 500 });
  const start = useStartDeployment();
  const stop = useStopDeployment();
  const restart = useRestartDeployment();
  const remove = useDeleteDeployment();
  const [scaleOpen, setScaleOpen] = useState(false);
  const [restartOpen, setRestartOpen] = useState(false);
  const [stopOpen, setStopOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const deviceLabels = useMemo(() => new Map((devices.data?.items ?? []).map((x) => [x.id, `${x.name.replace(/^NVIDIA |^AMD |^Apple /, '')} #${x.index}`])), [devices.data]);

  if (isLoading) return <ListPageSkeleton stats={4} rows={4} />;
  if (isError || !d) {
    return (
      <PageContainer>
        <ErrorState title="Deployment not found" description="It may have been deleted, or the control plane is unreachable." onRetry={() => void refetch()} />
      </PageContainer>
    );
  }

  if (isExternal(d)) return <ExternalDeploymentDetail deployment={d} deviceLabels={deviceLabels} />;

  const live = d.live ?? {};
  const instances = d.instances ?? [];
  const vram = instances.reduce((s, i) => s + Number((i.resources as { vram_bytes?: number } | undefined)?.vram_bytes ?? 0), 0);
  const spec = (d.spec_decoding ?? {}) as { enabled?: boolean; method?: string; num_draft_tokens?: number };
  const pd = (d.pd_config ?? {}) as { prefill_replicas?: number; decode_replicas?: number; transfer?: string };
  const kv = (d.kv_sharing_config ?? {}) as { backend?: string };
  const selector = Object.entries(d.node_selector ?? {});
  const flags = flagText(d.launch_flags);

  return (
    <PageContainer>
      <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div className="space-y-2">
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="font-mono text-2xl font-semibold tracking-tight sm:text-[26px]">{d.name}</h1>
            <StatusBadge variant={DEPLOYMENT_BADGE[d.status]}>{DEPLOYMENT_STATUS_LABELS[d.status]}</StatusBadge>
            {d.effective_priority && <PriorityBadge priority={d.effective_priority} />}
          </div>
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-muted-foreground">
            <Link href={`/catalog/${d.model_id}`} className="hover:text-foreground hover:underline">
              {d.model_name}
            </Link>
            {d.model_type && <ModelTypeBadge type={d.model_type} />}
            <EngineBadge engine={d.engine_name} version={d.engine_version} />
            {d.preset && <span className="text-xs">· {PRESET_LABELS[d.preset]}</span>}
          </div>
          {d.description && <p className="max-w-3xl text-sm text-muted-foreground">{d.description}</p>}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {d.desired_running ? (
            <>
              <Button variant="outline" size="sm" className="rounded-sm" onClick={() => setScaleOpen(true)}>
                <Scale className="size-4" />
                Scale
              </Button>
              <Button variant="outline" size="sm" className="rounded-sm" onClick={() => setRestartOpen(true)}>
                <RotateCw className="size-4" />
                Restart
              </Button>
            </>
          ) : (
            <Button size="sm" className="rounded-sm" disabled={start.isPending} onClick={() => start.mutate({ id: d.id })}>
              <Play className="size-4" />
              Start
            </Button>
          )}
          <Button variant="outline" size="sm" className="rounded-sm" onClick={() => router.push(`/deployments/${d.id}/edit`)}>
            <Pencil className="size-4" />
            Edit
          </Button>
          <DropdownMenu>
            <DropdownMenuTrigger className="inline-flex h-9 cursor-pointer items-center justify-center rounded-sm border border-border bg-background px-3 hover:bg-muted dark:border-input dark:bg-input/30" aria-label="More actions">
              <MoreVertical className="h-4 w-4" />
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem onClick={() => router.push(`/benchmarks/new?deployment_id=${d.id}`)}>
                <FlaskConical className="mr-2 h-4 w-4" />
                Benchmark it
              </DropdownMenuItem>
              <DropdownMenuItem onClick={() => router.push(`/observability/inference?deployment=${d.id}`)}>
                <Activity className="mr-2 h-4 w-4" />
                Inference metrics
              </DropdownMenuItem>
              {d.desired_running && (
                <DropdownMenuItem onClick={() => setStopOpen(true)}>
                  <Square className="mr-2 h-4 w-4" />
                  Stop (keep settings)
                </DropdownMenuItem>
              )}
              <DropdownMenuSeparator />
              <DropdownMenuItem variant="destructive" onClick={() => setDeleteOpen(true)}>
                <Trash2 className="mr-2 h-4 w-4" />
                Delete
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </div>

      {d.status_message && <div className="rounded-lg border border-warning/40 bg-warning/10 px-4 py-3 text-sm">{d.status_message}</div>}

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-5">
        <StatCard label="Replicas ready" value={`${d.ready_replicas ?? 0} / ${d.replicas}`} icon={Rocket} footnote={`min ${d.min_replicas} · max ${d.max_replicas}`} />
        <StatCard label="Sessions" value={`${live.active_sessions ?? 0}`} icon={Activity} footnote={(live.waiting_sessions ?? 0) > 0 ? `${live.waiting_sessions} waiting` : 'No queue'} />
        <StatCard label="TTFT p95" value={live.ttft_ms_p95 ? `${Math.round(live.ttft_ms_p95)} ms` : '—'} icon={Timer} footnote={`${(live.requests_per_minute ?? 0).toFixed(1)} req/min`} />
        <StatCard label="Generation" value={live.generation_tps ? `${Math.round(live.generation_tps)} tok/s` : '—'} icon={Gauge} footnote={live.kv_cache_usage_pct !== undefined && live.kv_cache_usage_pct !== null ? `KV cache ${Math.round(live.kv_cache_usage_pct)}% used` : 'KV cache —'} />
        <StatCard label="GPUs" value={instances.reduce((s, i) => s + i.device_ids.length, 0)} icon={Cpu} footnote={vram ? `${formatBytes(vram)} VRAM reserved` : `${d.gpu_count} per replica`} />
      </div>

      <Card className="rounded-md shadow-premium">
        <CardHeader>
          <CardTitle>Instances</CardTitle>
          <CardDescription>Replicas with their node, GPUs and live engine metrics (refreshed every 10 s).</CardDescription>
        </CardHeader>
        <CardContent>
          {instances.length === 0 ? (
            <EmptyState
              icon={Rocket}
              title={d.desired_running ? 'No instances yet' : 'Stopped'}
              description={d.desired_running ? 'The scheduler is placing replicas.' : 'Start the deployment to place and load its replicas.'}
              action={
                !d.desired_running && (
                  <Button size="sm" className="rounded-sm" onClick={() => start.mutate({ id: d.id })}>
                    <Play className="size-4" />
                    Start
                  </Button>
                )
              }
            />
          ) : (
            <InstancesTable instances={instances} columns={['node']} deviceLabels={deviceLabels} />
          )}
        </CardContent>
      </Card>

      <div className="grid items-start gap-6 xl:grid-cols-2">
        <TrafficCard key={instances.map((i) => `${i.id}:${i.traffic_weight}`).join('|')} deployment={d} instances={instances} />
        <EndpointsCard deployment={d} />
      </div>

      <div className="grid items-start gap-6 xl:grid-cols-2">
        <Card className="rounded-md shadow-premium">
          <CardHeader>
            <CardTitle>Configuration</CardTitle>
            <CardDescription>Changing model version, engine, GPUs, slices, flags or context swaps every replica one at a time.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <DetailList
              columns={2}
              items={[
                { label: 'Model version', value: d.model_version_id ? 'pinned' : 'default' },
                { label: 'Context', value: d.context_size ? `${d.context_size.toLocaleString()} tokens` : 'model default' },
                { label: 'GPUs / replica', value: `${d.gpu_count} · TP ${d.tp_size} · PP ${d.pp_size}` },
                { label: 'Strategy', value: d.placement_strategy ?? 'spread' },
                { label: 'Slice', value: d.slice_vram_bytes ? `${formatBytes(d.slice_vram_bytes)} · ${d.slice_compute_pct ?? 100}%` : 'scheduler estimate' },
                { label: 'Vendors', value: (d.device_vendor_filter ?? []).join(', ') || 'any' },
                { label: 'PD split', value: d.pd_split ? `${pd.prefill_replicas ?? 1}P / ${pd.decode_replicas ?? 1}D · ${pd.transfer ?? 'nixl'}` : 'off' },
                { label: 'Spec decoding', value: spec.enabled ? `${spec.method} · ${spec.num_draft_tokens ?? 4}` : 'off' },
                { label: 'KV sharing', value: d.kv_sharing ? kv.backend ?? 'on' : 'off' },
                { label: 'Tool parser', value: d.tool_parser ?? 'model default', mono: true },
                { label: 'Node selector', value: selector.length ? selector.map(([k, v]) => `${k}=${Array.isArray(v) ? `[${v.length}]` : String(v)}`).join(', ') : 'any node', wide: true },
              ]}
            />
            <div className="space-y-1.5">
              <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">Launch flags</p>
              <div className="flex flex-wrap gap-1">
                {flags.length ? flags.map((f) => <code key={f} className="rounded-sm border border-border bg-muted/40 px-1.5 py-0.5 font-mono text-[10.5px]">{f}</code>) : <span className="text-xs text-muted-foreground">None</span>}
              </div>
            </div>
            {(d.control_vectors ?? []).length > 0 && (
              <div className="space-y-1.5">
                <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">Control vectors</p>
                {(d.control_vectors ?? []).map((cv, i) => (
                  <div key={i} className="font-mono text-[11px]">
                    {String(cv.path)} × {String(cv.scale)}
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>

        <Card className="rounded-md shadow-premium">
          <CardHeader>
            <CardTitle>Swap history</CardTitle>
            <CardDescription>Loads, unloads, scales and restarts — manual, rule or layout triggered.</CardDescription>
          </CardHeader>
          <CardContent>
            {(swaps.data?.items.length ?? 0) === 0 ? (
              <p className="py-6 text-center text-sm text-muted-foreground">No swaps yet.</p>
            ) : (
              <ul className="space-y-3">
                {swaps.data?.items.slice(0, 8).map((s) => {
                  const duration = s.started_at && s.finished_at ? (Date.parse(s.finished_at) - Date.parse(s.started_at)) / 1000 : null;
                  return (
                    <li key={s.id} className="rounded-lg border border-border p-3">
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <span className="flex items-center gap-2 text-xs">
                          <span className="font-medium">{s.kind.replace('_', ' ')}</span>
                          <span className="text-muted-foreground">· {s.trigger}{s.rule_name ? ` (${s.rule_name})` : ''}</span>
                        </span>
                        <span className="flex items-center gap-2">
                          <span className="text-[11px] text-muted-foreground">{formatRelativeTime(s.created_at)}{duration !== null ? ` · ${duration.toFixed(1)} s` : ''}</span>
                          <StatusBadge variant={toBadgeVariant(s.state)} className="py-0">{s.state.replace('_', ' ')}</StatusBadge>
                        </span>
                      </div>
                      <div className="mt-2 flex gap-1" aria-label="Steps">
                        {s.steps.map((step) => (
                          <span
                            key={step.name}
                            title={`${step.name}: ${step.state}${step.error ? ` — ${step.error}` : ''}`}
                            className={cn(
                              'flex-1 truncate rounded-sm px-1 py-0.5 text-center text-[10px]',
                              step.state === 'completed' ? 'bg-success/15 text-success-dark dark:text-success-light' : step.state === 'failed' ? 'bg-error/15 text-error-dark dark:text-error-light' : step.state === 'running' ? 'bg-info/15' : 'bg-muted text-muted-foreground',
                            )}
                          >
                            {step.name.replace('_', ' ')}
                          </span>
                        ))}
                      </div>
                      {s.error_message && <p className="mt-1.5 text-[11px] text-error-dark dark:text-error-light">{s.error_message}</p>}
                    </li>
                  );
                })}
              </ul>
            )}
            <Link href={`/swaps?deployment_id=${d.id}`} className="mt-3 inline-block text-xs text-muted-foreground underline-offset-2 hover:text-foreground hover:underline">
              All swaps →
            </Link>
          </CardContent>
        </Card>
      </div>

      {scaleOpen && <ScaleDialog deployment={d} open onOpenChange={setScaleOpen} />}
      <ConfirmationDialog open={restartOpen} onOpenChange={setRestartOpen} title={`Rolling restart of ${d.name}?`} description="Each replica is drained, reloaded and health-checked before the next one starts." confirmText="Restart" isLoading={restart.isPending} onConfirm={() => restart.mutate({ id: d.id })} />
      <ConfirmationDialog open={stopOpen} onOpenChange={setStopOpen} title={`Stop ${d.name}?`} description="All replicas are drained and unloaded. The deployment and its settings stay; start it again any time." confirmText="Stop" variant="destructive" isLoading={stop.isPending} onConfirm={() => stop.mutate({ id: d.id })} />
      <DeleteConfirmationDialog
        open={deleteOpen}
        onOpenChange={setDeleteOpen}
        itemName={d.name}
        resourceType="deployment"
        warningMessage="Every replica is drained and unloaded first. Cached weights stay on the nodes."
        isDeleting={remove.isPending}
        onConfirm={() => remove.mutate(d.id, { onSuccess: () => router.push('/deployments') })}
      />
    </PageContainer>
  );
}

