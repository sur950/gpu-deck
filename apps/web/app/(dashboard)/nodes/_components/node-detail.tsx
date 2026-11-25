'use client';

import { useMemo, useState } from 'react';
import Link from 'next/link';
import { useParams, useRouter } from 'next/navigation';
import {
  Activity,
  ArrowDownToLine,
  ArrowUpCircle,
  ArrowUpFromLine,
  Copy,
  Cpu,
  Fingerprint,
  FolderSearch,
  HardDrive,
  MemoryStick,
  MoreVertical,
  Pencil,
  Rocket,
  RotateCw,
  ShieldCheck,
  ShieldOff,
  Trash2,
} from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from '@/components/ui/dropdown-menu';
import { PageContainer } from '@/components/shared/PageContainer';
import { ListPageSkeleton } from '@/components/shared/ListPageSkeleton';
import { ErrorState } from '@/components/shared/ErrorState';
import { EmptyState } from '@/components/shared/EmptyState';
import { StatusBadge } from '@/components/shared/StatusBadge';
import { StatCard } from '@/components/shared/stat-card';
import { DetailList, SectionLabel } from '@/components/shared/detail-list';
import { LabelChips } from '@/components/shared/label-chips';
import { ConfirmationDialog } from '@/components/shared/ConfirmationDialog';
import { CACHE_BADGE, COMMAND_BADGE } from '@/components/fleet/status';
import { ActivityBadge, nodeActivity } from '@/components/fleet/activity-badge';
import { NodeStatusBadge, isNodeDetached, nodeStateInput } from '@/components/fleet/node-status';
import { Heartbeat } from '@/components/fleet/heartbeat';
import { useNow } from '@/components/fleet/expiry-countdown';
import { VramMeter } from '@/components/fleet/vram-meter';
import { VendorBadge } from '@/components/fleet/vendor-badge';
import { SliceBar, SliceLegend, useDeploymentColors } from '@/components/fleet/slice-bar';
import { GpuLive } from '@/components/fleet/gpu-live';
import { DrainNodeDialog, EditNodeDialog, RemoveNodeDialog, RevokeCertificateDialog } from '@/components/fleet/node-dialogs';
import { InstancesTable } from '@/components/models/instances-table';
import { NodeFilesPanel } from '@/app/(dashboard)/nodes/_components/node-files';
import { NodeLatencyPanel } from '@/components/observability/node-latency-panel';
import { AgentUpdateBadge, NodeAgentUpdateCard, UpdateAgentDialog } from '@/components/fleet/agent-update';
import {
  useCluster,
  useNode,
  useNodeCertificates,
  useNodeCommands,
  useNodeDevices,
  useNodeEvents,
  useNodeInstances,
  useRotateNodeCertificate,
  useUndrainNode,
} from '@/hooks/api/fleet';
import { useCacheItems } from '@/hooks/api/models-cache';
import { useUrlState } from '@/hooks/use-url-state';
import { cn } from '@/lib/utils';
import { formatBytes, formatDate, formatDateTime, formatRelativeTime } from '@/utils/format';
import { AGENT_COMMAND_STATE_LABELS, DEVICE_HEALTH_LABELS, NODE_OS_LABELS, NODE_STATUS_LABELS, nodeEngines, type LogChunk, type Node, type NodeCertificate } from '@/types/fleet';
import { CACHE_STATE_LABELS, ENGINE_LABELS, type EngineName } from '@/types/models';

const TABS = ['overview', 'gpus', 'instances', 'cache', 'files', 'network', 'events', 'certificates'] as const;
type Tab = (typeof TABS)[number];
const DEFAULTS = { tab: 'overview' as string };

const LEVEL_STYLE: Record<string, string> = {
  debug: 'text-muted-foreground',
  info: 'text-info',
  warn: 'text-warning',
  error: 'text-error',
};

function StatusCallout({ node, activity }: { node: Node; activity: string | null }) {
  const run = node.agent_update?.active_run;
  if (node.agent_update?.status === 'updating') {
    return (
      <div className="rounded-lg border border-warning/30 bg-warning/10 px-4 py-3 text-sm">
        <span className="font-medium">Updating the agent{run ? ` to ${run.to_version}` : ''}.</span>{' '}
        <span className="text-muted-foreground">
          Running models keep serving. Metrics pause while the agent restarts and resume when it reconnects; offline alerts for this node are held back until the update ends. If the new agent does not come up healthy, the node rolls back by itself.
        </span>
      </div>
    );
  }
  const detached = isNodeDetached(nodeStateInput(node)) && node.status !== 'removed';
  if (node.status === 'online' && !detached) return null;
  const since = node.disconnected_at ?? node.last_heartbeat_at;
  if (detached) {
    return (
      <div className="rounded-lg border border-error/30 bg-error/10 px-4 py-3 text-sm">
        <span className="font-medium">Detached.</span>{' '}
        <span className="text-muted-foreground">
          The agent is disconnected (last heartbeat {since ? formatDateTime(since) : 'never'}). The scheduler skips this node, the API layer stops routing to it, and the GPU values below are the last known ones.
          {node.status === 'drained' || node.status === 'draining' || node.drained_at || node.drain_requested_at ? ' It was drained before it disconnected; the drain stays when it reconnects.' : ''}
        </span>
        {node.drain_reason && <span className="text-muted-foreground"> Drain reason: “{node.drain_reason}”.</span>}
      </div>
    );
  }
  const tone =
    node.status === 'unhealthy'
      ? 'border-error/30 bg-error/10'
      : node.status === 'draining'
        ? 'border-warning/30 bg-warning/10'
        : node.status === 'drained'
          ? 'border-border bg-muted/40'
          : 'border-info/30 bg-info/10';
  const outside = activity === 'external' || activity === 'mixed' ? ' Processes outside GPU Deck still use its GPUs.' : '';
  const text: Record<string, string> = {
    pending: 'The agent joined and holds a certificate; waiting for its first inventory report.',
    offline: 'No heartbeat — the scheduler skips this node and the API layer stops routing to it.',
    unhealthy: 'The agent reports problems (see events). New placements avoid this node.',
    draining: `Draining since ${node.drain_requested_at ? formatRelativeTime(node.drain_requested_at) : 'now'}: no new placements, instances are moved or unloaded.`,
    drained: `Drained — the agent is connected and reporting, but the scheduler places nothing here.${outside} Undrain to accept work again.`,
    removed: 'Removed from the fleet.',
  };
  return (
    <div className={cn('rounded-lg border px-4 py-3 text-sm', tone)}>
      <span className="font-medium">{NODE_STATUS_LABELS[node.status]}.</span> <span className="text-muted-foreground">{node.status_message ?? text[node.status]}</span>
      {node.drain_reason && <span className="text-muted-foreground"> Reason: “{node.drain_reason}”.</span>}
    </div>
  );
}

function EventsPanel({ nodeId }: { nodeId: string }) {
  const [level, setLevel] = useState('');
  const events = useNodeEvents(nodeId, level || undefined);
  const commands = useNodeCommands(nodeId);
  const chunks: LogChunk[] = events.data?.items ?? [];
  return (
    <div className="grid gap-6 xl:grid-cols-5">
      <Card className="gap-0 rounded-md py-0 shadow-premium xl:col-span-3">
        <CardHeader className="flex flex-row items-center justify-between gap-3 border-b border-border px-4 py-3">
          <div>
            <CardTitle className="text-sm font-semibold">Agent and control-plane events</CardTitle>
            <CardDescription className="text-xs">Last 24 h · newest first · <Link href={`/observability/logs?node_id=${nodeId}`} className="underline underline-offset-2">open in Logs</Link></CardDescription>
          </div>
          <Select value={level || 'all'} onValueChange={(v) => setLevel(v === 'all' ? '' : String(v))}>
            <SelectTrigger size="sm" className="w-32">
              <SelectValue>{(v: unknown) => (v === 'all' ? 'All levels' : `${String(v)} +`)}</SelectValue>
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All levels</SelectItem>
              <SelectItem value="info">info +</SelectItem>
              <SelectItem value="warn">warn +</SelectItem>
              <SelectItem value="error">error</SelectItem>
            </SelectContent>
          </Select>
        </CardHeader>
        <CardContent className="max-h-[520px] overflow-y-auto px-0">
          {events.isLoading ? (
            <div className="space-y-2 p-4">{Array.from({ length: 6 }).map((_, i) => <div key={i} className="h-8 animate-pulse rounded bg-muted" />)}</div>
          ) : chunks.length === 0 ? (
            <EmptyState icon={Activity} title="No events" description="Nothing logged for this node in the last 24 hours at this level." />
          ) : (
            <ul className="divide-y divide-border font-mono text-[11.5px] leading-relaxed">
              {chunks.map((c) => (
                <li key={c.id} className="grid grid-cols-[72px_44px_1fr] gap-3 px-4 py-2">
                  <time className="text-muted-foreground tabular-nums" dateTime={c.ts} title={new Date(c.ts).toLocaleString()}>
                    {new Date(c.ts).toLocaleTimeString([], { hour12: false })}
                  </time>
                  <span className={cn('font-semibold uppercase', LEVEL_STYLE[c.level])}>{c.level}</span>
                  <span className="min-w-0 break-words whitespace-pre-wrap">
                    <span className="text-muted-foreground">[{c.source}{c.logger ? `:${c.logger}` : ''}] </span>
                    {c.content.split('\n').slice(0, 4).join('\n')}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
      <Card className="gap-0 rounded-md py-0 shadow-premium xl:col-span-2">
        <CardHeader className="border-b border-border px-4 py-3">
          <CardTitle className="text-sm font-semibold">Agent commands</CardTitle>
          <CardDescription className="text-xs">Sent over the hub; idempotent and replayed on reconnect.</CardDescription>
        </CardHeader>
        <CardContent className="max-h-[520px] overflow-y-auto px-0">
          {(commands.data?.items.length ?? 0) === 0 ? (
            <p className="p-6 text-center text-sm text-muted-foreground">No commands yet.</p>
          ) : (
            <ul className="divide-y divide-border">
              {commands.data?.items.map((cmd) => (
                <li key={cmd.id} className="flex items-start justify-between gap-3 px-4 py-2.5">
                  <div className="min-w-0">
                    <div className="font-mono text-xs font-medium">{cmd.command_type}</div>
                    <div className="text-[11px] text-muted-foreground">
                      {formatRelativeTime(cmd.created_at)}
                      {cmd.completed_at && cmd.sent_at ? ` · ${((Date.parse(cmd.completed_at) - Date.parse(cmd.sent_at)) / 1000).toFixed(1)} s` : ''}
                      {cmd.attempts && cmd.attempts > 1 ? ` · ${cmd.attempts} attempts` : ''}
                    </div>
                    {cmd.error_message && <div className="mt-0.5 text-[11px] text-error-dark dark:text-error-light">{cmd.error_code}: {cmd.error_message}</div>}
                  </div>
                  <StatusBadge variant={COMMAND_BADGE[cmd.state]} className="shrink-0">
                    {cmd.state === 'running' && cmd.progress_pct ? `${Math.round(cmd.progress_pct)}%` : AGENT_COMMAND_STATE_LABELS[cmd.state]}
                  </StatusBadge>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function CertificatesPanel({ node }: { node: Node }) {
  const certs = useNodeCertificates(node.id);
  const rotate = useRotateNodeCertificate();
  const [revoking, setRevoking] = useState<NodeCertificate | null>(null);
  const [rotateOpen, setRotateOpen] = useState(false);
  const now = useNow(60_000);
  return (
    <Card className="rounded-md shadow-premium">
      <CardHeader className="flex flex-row items-start justify-between gap-4">
        <div>
          <CardTitle>Certificates</CardTitle>
          <CardDescription>Client certificates signed by the internal CA for the agent&apos;s mTLS connection to the hub. Revoking the active one cuts the node off.</CardDescription>
        </div>
        <Button variant="outline" size="sm" className="rounded-sm" disabled={node.status === 'offline' || node.status === 'pending'} onClick={() => setRotateOpen(true)}>
          <RotateCw className="size-4" />
          Rotate now
        </Button>
      </CardHeader>
      <CardContent>
        <div className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Serial</TableHead>
                <TableHead>Fingerprint (SHA-256)</TableHead>
                <TableHead>Valid</TableHead>
                <TableHead>State</TableHead>
                <TableHead className="w-24" />
              </TableRow>
            </TableHeader>
            <TableBody>
              {(certs.data?.items ?? []).map((cert) => {
                const expiresIn = Date.parse(cert.not_after) - now;
                return (
                  <TableRow key={cert.id}>
                    <TableCell className="font-mono text-[11px]">{cert.serial}</TableCell>
                    <TableCell className="max-w-[280px]">
                      <span className="block truncate font-mono text-[11px] text-muted-foreground" title={cert.fingerprint_sha256}>
                        {cert.fingerprint_sha256.match(/.{1,2}/g)?.slice(0, 12).join(':')}…
                      </span>
                    </TableCell>
                    <TableCell className="text-xs">
                      {formatDate(cert.not_before)} → {formatDate(cert.not_after)}
                      {!cert.revoked_at && expiresIn < 30 * 86_400_000 && <span className="ml-1 text-warning">· renews soon</span>}
                    </TableCell>
                    <TableCell>
                      {cert.revoked_at ? (
                        <div>
                          <StatusBadge variant="revoked">Revoked</StatusBadge>
                          <div className="mt-0.5 text-[11px] text-muted-foreground">{cert.revocation_reason ?? ''} · {formatRelativeTime(cert.revoked_at)}</div>
                        </div>
                      ) : cert.is_active ? (
                        <StatusBadge variant="active">
                          <ShieldCheck className="mr-1 size-3" />
                          Active
                        </StatusBadge>
                      ) : (
                        <StatusBadge variant="inactive">Superseded</StatusBadge>
                      )}
                    </TableCell>
                    <TableCell className="text-right">
                      {!cert.revoked_at && (
                        <Button variant="ghost" size="xs" className="text-muted-foreground hover:text-destructive" onClick={() => setRevoking(cert)}>
                          <ShieldOff className="size-3" />
                          Revoke
                        </Button>
                      )}
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </div>
      </CardContent>
      {revoking && <RevokeCertificateDialog key={revoking.id} node={node} cert={revoking} open onOpenChange={(o) => !o && setRevoking(null)} />}
      <ConfirmationDialog
        open={rotateOpen}
        onOpenChange={setRotateOpen}
        title="Rotate the agent certificate?"
        description="The hub sends rotate_cert, the agent replies with a new CSR, gets a signed certificate and reconnects. The old certificate is revoked 10 minutes after the new one connects."
        confirmText="Rotate"
        isLoading={rotate.isPending}
        onConfirm={() => rotate.mutate(node.id)}
      />
    </Card>
  );
}

export function NodeDetail() {
  const router = useRouter();
  const { id } = useParams<{ id: string }>();
  const { params, setParams } = useUrlState(DEFAULTS);
  const tab = (TABS as readonly string[]).includes(params.tab) ? (params.tab as Tab) : 'overview';
  const { data: node, isLoading, isError, refetch } = useNode(id);
  const devices = useNodeDevices(id);
  const instances = useNodeInstances(id);
  const cache = useCacheItems({ node_id: id, page_size: 200 });
  const cluster = useCluster(node?.cluster_id ?? '');
  const undrain = useUndrainNode();
  const [editOpen, setEditOpen] = useState(false);
  const [drainOpen, setDrainOpen] = useState(false);
  const [removeOpen, setRemoveOpen] = useState(false);
  const [updateOpen, setUpdateOpen] = useState(false);

  const deviceLabels = useMemo(() => new Map((devices.data?.items ?? []).map((d) => [d.id, `${d.name.replace(/^NVIDIA |^AMD /, '')} #${d.index}`])), [devices.data]);
  const allSlices = useMemo(() => (devices.data?.items ?? []).flatMap((d) => d.slices ?? []), [devices.data]);
  const colors = useDeploymentColors();

  if (isLoading) return <ListPageSkeleton stats={4} rows={5} />;
  if (isError || !node) {
    return (
      <PageContainer>
        <ErrorState title="Node not found" description="It may have been removed, or the control plane is unreachable." onRetry={() => void refetch()} />
      </PageContainer>
    );
  }

  const deviceList = devices.data?.items ?? [];
  const activity = nodeActivity(node.activity, deviceList);
  const vramTotal = deviceList.reduce((s, d) => s + d.vram_total_bytes, 0);
  const vramReserved = deviceList.reduce((s, d) => s + (d.vram_reserved_bytes ?? 0), 0);
  const canDrain = node.status === 'online' || node.status === 'unhealthy';
  const canUndrain = node.status === 'draining' || node.status === 'drained';
  const engines = nodeEngines(node.capabilities);
  const cacheItems = (cache.data?.items ?? []).filter((c) => c.state !== 'evicted');
  // Same figure as the Model cache page: every file the node keeps for models, including Ollama /
  // LM Studio files used in place (the agent's cache_used_bytes counts only its own cache dir).
  const cacheBytes = Math.max(node.cache_used_bytes ?? 0, cacheItems.reduce((sum, c) => sum + (c.size_bytes ?? 0), 0));

  return (
    <PageContainer>
      <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div className="space-y-2">
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="text-2xl font-semibold tracking-tight sm:text-[28px]">{node.name}</h1>
            {node.agent_update?.status === 'updating' ? <AgentUpdateBadge info={node.agent_update} /> : <NodeStatusBadge node={nodeStateInput(node)} />}
            {node.agent_update?.status !== 'updating' && <AgentUpdateBadge info={node.agent_update} />}
            {!isNodeDetached(nodeStateInput(node)) && <ActivityBadge activity={activity} outside={node.outside_processes ?? 0} />}
            <span className="text-xs">
              <Heartbeat at={node.last_heartbeat_at} />
            </span>
          </div>
          <p className="text-sm text-muted-foreground">
            {node.hostname ?? node.ip_address} ·{' '}
            {cluster.data ? (
              <Link href={`/clusters/${cluster.data.id}`} className="hover:underline">
                {cluster.data.name}
              </Link>
            ) : (
              'cluster'
            )}{' '}
            · {node.os ? NODE_OS_LABELS[node.os] : 'unknown OS'} {node.arch ?? ''} · agent <span className="font-mono">{node.agent_version ?? '—'}</span>
          </p>
          <LabelChips labels={node.labels} max={8} />
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {node.agent_update?.can_update && (
            <Button size="sm" className="rounded-sm" onClick={() => setUpdateOpen(true)}>
              <ArrowUpCircle className="size-4" />
              Update agent
            </Button>
          )}
          {canDrain && (
            <Button variant="outline" size="sm" className="rounded-sm" onClick={() => setDrainOpen(true)}>
              <ArrowDownToLine className="size-4" />
              Drain
            </Button>
          )}
          {canUndrain && (
            <Button variant="outline" size="sm" className="rounded-sm" disabled={undrain.isPending} onClick={() => undrain.mutate({ id: node.id })}>
              <ArrowUpFromLine className="size-4" />
              Undrain
            </Button>
          )}
          <Button variant="outline" size="sm" className="rounded-sm" onClick={() => setEditOpen(true)}>
            <Pencil className="size-4" />
            Edit
          </Button>
          <DropdownMenu>
            <DropdownMenuTrigger className="inline-flex h-9 cursor-pointer items-center justify-center gap-2 rounded-sm border border-border bg-background px-3 text-xs font-medium hover:bg-muted dark:border-input dark:bg-input/30" aria-label="More actions">
              <MoreVertical className="h-4 w-4" />
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem onClick={() => router.push(`/deployments/new?node_id=${node.id}`)}>
                <Rocket className="mr-2 h-4 w-4" />
                Deploy to this node
              </DropdownMenuItem>
              <DropdownMenuItem onClick={() => setParams({ tab: 'files' })}>
                <FolderSearch className="mr-2 h-4 w-4" />
                Scan path
              </DropdownMenuItem>
              <DropdownMenuItem onClick={() => setParams({ tab: 'files' })}>
                <Fingerprint className="mr-2 h-4 w-4" />
                Hash files
              </DropdownMenuItem>
              <DropdownMenuItem onClick={() => setParams({ tab: 'certificates' })}>
                <ShieldCheck className="mr-2 h-4 w-4" />
                Certificates
              </DropdownMenuItem>
              <DropdownMenuItem
                onClick={() => {
                  void navigator.clipboard.writeText(node.id);
                  toast.success('Node ID copied');
                }}
              >
                <Copy className="mr-2 h-4 w-4" />
                Copy node ID
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem variant="destructive" onClick={() => setRemoveOpen(true)}>
                <Trash2 className="mr-2 h-4 w-4" />
                Remove from fleet
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </div>

      <StatusCallout node={node} activity={activity} />

      <div className="grid gap-4 md:grid-cols-4">
        <StatCard label="GPUs" value={deviceList.length} icon={Cpu} footnote={deviceList.length ? `${deviceList.filter((d) => d.health === 'healthy').length} healthy · ${[...new Set(deviceList.map((d) => d.vendor))].join(', ')}` : 'Waiting for inventory'} />
        <StatCard label="VRAM reserved" value={vramTotal ? `${Math.round((vramReserved / vramTotal) * 100)}%` : '—'} icon={MemoryStick} footnote={vramTotal ? `${formatBytes(vramReserved)} of ${formatBytes(vramTotal)} by slices` : 'No devices'} />
        <StatCard label="Instances" value={instances.data?.items.length ?? node.instance_count ?? 0} icon={Rocket} footnote={`${(instances.data?.items ?? []).filter((i) => i.state === 'ready').length} ready`} />
        <StatCard label="Model cache" value={formatBytes(cacheBytes)} icon={HardDrive} footnote={node.cache_limit_bytes ? `limit ${formatBytes(node.cache_limit_bytes)} · ${cacheItems.length} files` : `${cacheItems.length} files`} />
      </div>

      <Tabs value={tab} onValueChange={(v) => setParams({ tab: String(v) })}>
        <TabsList className="bg-muted">
          <TabsTrigger value="overview" className="px-3 text-xs">Overview</TabsTrigger>
          <TabsTrigger value="gpus" className="px-3 text-xs">GPUs · {deviceList.length}</TabsTrigger>
          <TabsTrigger value="instances" className="px-3 text-xs">Instances · {instances.data?.items.length ?? 0}</TabsTrigger>
          <TabsTrigger value="cache" className="px-3 text-xs">Model cache</TabsTrigger>
          <TabsTrigger value="files" className="px-3 text-xs">Files</TabsTrigger>
          <TabsTrigger value="network" className="px-3 text-xs">Network</TabsTrigger>
          <TabsTrigger value="events" className="px-3 text-xs">Events</TabsTrigger>
          <TabsTrigger value="certificates" className="px-3 text-xs">Certificates</TabsTrigger>
        </TabsList>

        <TabsContent value="overview" className="mt-4">
          <div className="grid gap-6 lg:grid-cols-3">
            <Card className="rounded-md shadow-premium">
              <CardHeader>
                <CardTitle>System</CardTitle>
              </CardHeader>
              <CardContent>
                <DetailList
                  items={[
                    { label: 'Hostname', value: node.hostname, mono: true },
                    { label: 'IP address', value: node.ip_address, mono: true },
                    { label: 'OS', value: node.os ? `${NODE_OS_LABELS[node.os]} ${node.os_version ?? ''}` : undefined },
                    { label: 'Kernel', value: node.kernel_version, mono: true },
                    { label: 'Architecture', value: node.arch, mono: true },
                    { label: 'CPU cores', value: node.cpu_cores },
                    { label: 'Memory', value: node.memory_total_bytes ? formatBytes(node.memory_total_bytes) : undefined },
                    ...(node.kubernetes_node_name ? [{ label: 'Kubernetes node', value: node.kubernetes_node_name, mono: true }] : []),
                    ...(node.cloud_instance_type ? [{ label: 'Cloud instance', value: `${node.cloud_instance_type} · ${node.cloud_instance_id ?? ''}`, mono: true }] : []),
                  ]}
                />
              </CardContent>
            </Card>
            <Card className="rounded-md shadow-premium">
              <CardHeader>
                <CardTitle>Agent</CardTitle>
              </CardHeader>
              <CardContent className="space-y-4">
                <DetailList
                  items={[
                    { label: 'Version', value: node.agent_version, mono: true },
                    { label: 'Protocol', value: node.protocol_version ? `v${node.protocol_version}` : undefined },
                    node.metadata?.serving_mode === 'tunnel'
                      ? { label: 'Serving', value: 'Outbound tunnel · no inbound port' }
                      : { label: 'Serving (direct, mTLS)', value: `${node.proxy_address ?? node.ip_address ?? '—'}:${node.proxy_port}`, mono: true },
                    isNodeDetached(nodeStateInput(node))
                      ? { label: 'Disconnected', value: node.disconnected_at ?? node.last_heartbeat_at ? formatRelativeTime(node.disconnected_at ?? node.last_heartbeat_at) : 'unknown' }
                      : { label: 'Connected', value: node.connected_at ? formatRelativeTime(node.connected_at) : undefined },
                    { label: 'Last inventory', value: node.last_inventory_at ? formatRelativeTime(node.last_inventory_at) : 'never' },
                    { label: 'Cert fingerprint', value: node.cert_fingerprint, mono: true },
                    { label: 'Joined', value: formatDate(node.created_at) },
                  ]}
                />
                {engines.length > 0 && (
                  <div className="space-y-1.5">
                    <SectionLabel>Engines available</SectionLabel>
                    <div className="flex flex-wrap gap-1">
                      {engines.map((e) => (
                        <span
                          key={`${e.name}:${e.runtime ?? ''}`}
                          title={e.path ? `${e.runtime ?? 'native'} · ${e.path}` : e.runtime === 'container' ? 'Container image, pulled on first use' : undefined}
                          className={cn(
                            'inline-flex items-center gap-1 rounded-sm border px-1.5 py-0.5 font-mono text-[11px]',
                            e.runtime === 'container' ? 'border-dashed border-border text-muted-foreground' : 'border-border',
                          )}
                        >
                          {ENGINE_LABELS[e.name as EngineName] ?? e.name}
                          {e.runtime && <span className="font-sans text-[9.5px] tracking-wide text-muted-foreground uppercase">{e.runtime === 'container' ? 'ctr' : e.runtime}</span>}
                          {e.versions[0] && <span className="text-muted-foreground">{e.versions[0]}</span>}
                        </span>
                      ))}
                    </div>
                  </div>
                )}
              </CardContent>
            </Card>
            <Card className="rounded-md shadow-premium">
              <CardHeader>
                <CardTitle>Disks</CardTitle>
                <CardDescription>
                  Cache dir <span className="font-mono">{node.cache_dir ?? '—'}</span>
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">
                {(node.disks ?? []).map((disk) => (
                  <div key={disk.mount} className="space-y-1">
                    <div className="flex items-center justify-between text-xs">
                      <span className="font-mono">{disk.mount}</span>
                      {(disk.is_cache_dir || (node.cache_dir ?? '').startsWith(disk.mount === '/' ? '/__none' : disk.mount)) && <span className="text-[10px] font-medium tracking-wide text-muted-foreground uppercase">model cache</span>}
                    </div>
                    <VramMeter used={disk.total_bytes - disk.free_bytes} total={disk.total_bytes} label={`${formatBytes(disk.free_bytes)} free of ${formatBytes(disk.total_bytes)}`} />
                  </div>
                ))}
                {node.cache_limit_bytes ? (
                  <div className="space-y-1 border-t border-border pt-4">
                    <div className="text-xs font-medium">Cache limit</div>
                    <VramMeter used={node.cache_used_bytes ?? 0} total={node.cache_limit_bytes} />
                  </div>
                ) : null}
              </CardContent>
            </Card>
          </div>
          <NodeAgentUpdateCard nodeId={node.id} nodeName={node.name} className="mt-6" />
        </TabsContent>

        <TabsContent value="gpus" className="mt-4">
          {deviceList.length === 0 ? (
            <Card className="rounded-md shadow-premium">
              <CardContent>
                <EmptyState icon={Cpu} title="No GPUs reported" description="The agent reports devices in its inventory message right after it connects." />
              </CardContent>
            </Card>
          ) : (
            <div className="space-y-3">
              <SliceLegend slices={allSlices} colors={colors} />
              <div className="grid gap-4 xl:grid-cols-2">
                {deviceList.map((d) => (
                  <Card key={d.id} className="rounded-md shadow-premium">
                    <CardHeader className="flex flex-row items-start justify-between gap-3">
                      <div className="min-w-0">
                        <Link href={`/gpus/${d.id}`} className="text-sm font-semibold hover:underline">
                          {d.name} <span className="text-muted-foreground">#{d.index}</span>
                        </Link>
                        <div className="mt-0.5 flex flex-wrap items-center gap-2 text-[11px] text-muted-foreground">
                          <VendorBadge vendor={d.vendor} />
                          <span className="font-mono">{d.driver_version ?? ''}</span>
                          {d.runtime_version && <span>{d.runtime_version}</span>}
                        </div>
                      </div>
                      <div className="flex flex-wrap justify-end gap-1">
                        <StatusBadge variant={d.health === 'healthy' ? 'healthy' : d.health === 'degraded' ? 'degraded' : d.health === 'unhealthy' ? 'down' : 'unknown'}>{DEVICE_HEALTH_LABELS[d.health]}</StatusBadge>
                        <ActivityBadge
                          activity={d.activity}
                          outside={d.outside_processes ?? 0}
                          lastSeenAt={d.live?.ts ?? d.last_seen_at}
                          usedBytes={d.vram_usage_stale ? null : (d.vram_used_bytes ?? d.live?.memory_used_bytes ?? null)}
                          reservedBytes={d.vram_reserved_bytes ?? 0}
                          unified={d.is_unified_memory}
                        />
                      </div>
                    </CardHeader>
                    <CardContent className="space-y-4">
                      <SliceBar device={d} colors={colors} />
                      <GpuLive device={d} />
                    </CardContent>
                  </Card>
                ))}
              </div>
            </div>
          )}
        </TabsContent>

        <TabsContent value="instances" className="mt-4">
          <Card className="rounded-md shadow-premium">
            <CardContent>
              {(instances.data?.items.length ?? 0) === 0 ? (
                <EmptyState
                  icon={Rocket}
                  title={activity === 'external' ? 'No GPU Deck instances' : 'Nothing runs here'}
                  description={
                    activity === 'external'
                      ? 'Processes outside GPU Deck use this node\'s GPUs (see the GPUs tab). Deploy a model and the scheduler places instances in the VRAM that is still free.'
                      : 'Deploy a model and the scheduler places instances on the free GPUs, or pin a deployment to this node.'
                  }
                  action={
                    <Button size="sm" className="rounded-sm" onClick={() => router.push(`/deployments/new?node_id=${node.id}`)}>
                      <Rocket className="size-4" />
                      Deploy here
                    </Button>
                  }
                />
              ) : (
                <InstancesTable instances={instances.data?.items ?? []} columns={['deployment', 'engine']} deviceLabels={deviceLabels} />
              )}
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="cache" className="mt-4">
          <Card className="rounded-md shadow-premium">
            <CardHeader className="flex flex-row items-start justify-between gap-4">
              <div>
                <CardTitle>Files on this node</CardTitle>
                <CardDescription>Weights kept on local disk so swaps only cost load time. Ollama and LM Studio files are referenced in place, never copied.</CardDescription>
              </div>
              <Button variant="outline" size="sm" className="rounded-sm" onClick={() => router.push(`/model-cache?node=${node.id}`)}>
                <HardDrive className="size-4" />
                Manage cache
              </Button>
            </CardHeader>
            <CardContent>
              {cacheItems.length === 0 ? (
                <EmptyState icon={HardDrive} title="Cache is empty" description="Prefetch a model version to this node from the Model cache page." />
              ) : (
                <div className="overflow-x-auto">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>Model · file</TableHead>
                        <TableHead className="text-right">Size</TableHead>
                        <TableHead>State</TableHead>
                        <TableHead>Last used</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {cacheItems.map((item) => (
                        <TableRow key={item.id}>
                          <TableCell>
                            <div className="font-medium">
                              {item.model_name} <span className="font-mono text-[11px] text-muted-foreground">{item.version}</span>
                            </div>
                            <div className="max-w-[460px] truncate font-mono text-[10.5px] text-muted-foreground" title={item.local_path}>
                              {item.local_path}
                            </div>
                          </TableCell>
                          <TableCell className="text-right font-mono text-xs tabular-nums">{formatBytes(item.size_bytes)}</TableCell>
                          <TableCell>
                            <div className="flex flex-wrap items-center gap-1">
                              <StatusBadge variant={CACHE_BADGE[item.state]}>{item.state === 'downloading' ? `${Math.round(item.progress_pct ?? 0)}%` : CACHE_STATE_LABELS[item.state]}</StatusBadge>
                              {item.in_use && <StatusBadge variant="running">in use</StatusBadge>}
                              {item.pinned && <StatusBadge variant="info">pinned</StatusBadge>}
                            </div>
                          </TableCell>
                          <TableCell className="text-xs text-muted-foreground">{item.last_used_at ? formatRelativeTime(item.last_used_at) : 'never'}</TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="files" className="mt-4">
          <NodeFilesPanel node={node} />
        </TabsContent>

        <TabsContent value="network" className="mt-4">
          {tab === 'network' && <NodeLatencyPanel nodeId={node.id} />}
        </TabsContent>

        <TabsContent value="events" className="mt-4">
          <EventsPanel nodeId={node.id} />
        </TabsContent>

        <TabsContent value="certificates" className="mt-4">
          <CertificatesPanel node={node} />
        </TabsContent>
      </Tabs>

      {editOpen && <EditNodeDialog node={node} open onOpenChange={setEditOpen} />}
      {drainOpen && <DrainNodeDialog node={node} open onOpenChange={setDrainOpen} />}
      {removeOpen && <RemoveNodeDialog node={node} open onOpenChange={setRemoveOpen} onRemoved={() => router.push('/nodes')} />}
      {updateOpen && <UpdateAgentDialog nodeId={node.id} nodeName={node.name} info={node.agent_update} open onOpenChange={setUpdateOpen} />}
    </PageContainer>
  );
}

