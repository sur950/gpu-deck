'use client';

import { useState } from 'react';
import Link from 'next/link';
import { useParams, useRouter } from 'next/navigation';
import { CloudUpload, Copy, Cpu, Edit, MoreVertical, Power, Rocket, Server, Trash2 } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from '@/components/ui/dropdown-menu';
import { PageContainer } from '@/components/shared/PageContainer';
import { ListPageSkeleton } from '@/components/shared/ListPageSkeleton';
import { ErrorState } from '@/components/shared/ErrorState';
import { EmptyState } from '@/components/shared/EmptyState';
import { StatusBadge } from '@/components/shared/StatusBadge';
import { StatCard } from '@/components/shared/stat-card';
import { DetailList } from '@/components/shared/detail-list';
import { LabelChips } from '@/components/shared/label-chips';
import { DeleteConfirmationDialog } from '@/components/shared/DeleteConfirmationDialog';
import { ConfirmationDialog } from '@/components/shared/ConfirmationDialog';
import { ClusterTypeBadge } from '@/components/fleet/cluster-type-badge';
import { VramMeter } from '@/components/fleet/vram-meter';
import { Heartbeat } from '@/components/fleet/heartbeat';
import { CLUSTER_BADGE } from '@/components/fleet/status';
import { NodeStatusBadge, nodeStateInput } from '@/components/fleet/node-status';
import { useCluster, useClusterNodes, useCredentialOptions, useDeleteCluster, usePricePlans, useUpdateCluster } from '@/hooks/api/fleet';
import { formatBytes, formatDate } from '@/utils/format';
import { NODE_OS_LABELS } from '@/types/fleet';
import { KindGlyph } from '@/components/vault/kind-icon';
import { useCredentialKinds } from '@/hooks/use-vault';

export function ClusterDetail() {
  const router = useRouter();
  const { id } = useParams<{ id: string }>();
  const { data: cluster, isLoading, isError, refetch } = useCluster(id);
  const nodes = useClusterNodes(id);
  const credentials = useCredentialOptions();
  const { byId } = useCredentialKinds();
  const pricePlans = usePricePlans();
  const deleteCluster = useDeleteCluster();
  const updateCluster = useUpdateCluster();
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [toggleOpen, setToggleOpen] = useState(false);

  if (isLoading) return <ListPageSkeleton stats={4} rows={4} />;
  if (isError || !cluster) {
    return (
      <PageContainer>
        <ErrorState title="Cluster not found" description="It may have been deleted, or the control plane is unreachable." onRetry={() => void refetch()} />
      </PageContainer>
    );
  }

  const counts = cluster.counts ?? {};
  const credential = credentials.data?.find((c) => c.id === cluster.credential_id);
  const plan = pricePlans.data?.find((p) => p.id === cluster.price_plan_id);
  const config = cluster.config ?? {};
  const configEntries = Object.entries(config).filter(([, v]) => v !== null && v !== undefined && v !== '');

  return (
    <PageContainer>
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="text-2xl font-semibold tracking-tight sm:text-[28px]">{cluster.name}</h1>
          <ClusterTypeBadge type={cluster.type} />
          <StatusBadge variant={CLUSTER_BADGE[cluster.status]}>{cluster.status === 'active' ? 'Active' : 'Disabled'}</StatusBadge>
        </div>
        <div className="flex items-center gap-3">
          {cluster.type.startsWith('cloud_') && (
            <Button size="sm" className="rounded-sm" onClick={() => router.push(`/cloud/new?cluster_id=${cluster.id}`)}>
              <CloudUpload className="size-4" />
              Provision GPU VM
            </Button>
          )}
          <Button variant="outline" size="sm" className="rounded-sm" onClick={() => router.push(`/nodes/join?cluster_id=${cluster.id}`)}>
            <Server className="size-4" />
            Join a node
          </Button>
          <DropdownMenu>
            <DropdownMenuTrigger className="inline-flex h-9 cursor-pointer items-center justify-center gap-2 rounded-sm border border-border bg-background px-3.5 text-xs font-medium hover:bg-muted dark:border-input dark:bg-input/30">
              <MoreVertical className="h-4 w-4" />
              Actions
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem onClick={() => router.push(`/clusters/${cluster.id}/edit`)}>
                <Edit className="mr-2 h-4 w-4" />
                Edit cluster
              </DropdownMenuItem>
              <DropdownMenuItem onClick={() => router.push(`/deployments/new?cluster_id=${cluster.id}`)}>
                <Rocket className="mr-2 h-4 w-4" />
                New deployment here
              </DropdownMenuItem>
              <DropdownMenuItem
                onClick={() => {
                  void navigator.clipboard.writeText(cluster.id);
                  toast.success('Cluster ID copied');
                }}
              >
                <Copy className="mr-2 h-4 w-4" />
                Copy cluster ID
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem onClick={() => setToggleOpen(true)}>
                <Power className="mr-2 h-4 w-4" />
                {cluster.status === 'active' ? 'Disable scheduling' : 'Enable scheduling'}
              </DropdownMenuItem>
              <DropdownMenuItem variant="destructive" onClick={() => setDeleteOpen(true)}>
                <Trash2 className="mr-2 h-4 w-4" />
                Delete cluster
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </div>

      <div className="grid gap-4 md:grid-cols-4">
        <StatCard label="Nodes online" value={`${counts.nodes_online ?? 0} / ${counts.nodes ?? 0}`} icon={Server} footnote="Heartbeat within the last minute" />
        <StatCard label="GPUs" value={counts.devices ?? 0} icon={Cpu} footnote="Accelerators in inventory" />
        <StatCard label="Instances" value={counts.instances ?? 0} icon={Rocket} footnote="Engine processes placed here" />
        <StatCard
          label="VRAM in use"
          value={counts.vram_total_bytes ? `${(((counts.vram_used_bytes ?? 0) / counts.vram_total_bytes) * 100).toFixed(0)}%` : '—'}
          icon={Cpu}
          footnote={counts.vram_total_bytes ? `${formatBytes(counts.vram_used_bytes ?? 0)} of ${formatBytes(counts.vram_total_bytes)}` : 'No inventory yet'}
        />
      </div>

      <div className="grid gap-6 lg:grid-cols-3">
        <Card className="rounded-md shadow-premium lg:col-span-1">
          <CardHeader>
            <CardTitle>Overview</CardTitle>
            {cluster.description && <CardDescription>{cluster.description}</CardDescription>}
          </CardHeader>
          <CardContent className="space-y-4">
            <DetailList
              items={[
                { label: 'Region', value: cluster.region ?? undefined },
                {
                  label: 'Credential',
                  value: cluster.credential_id ? (
                    <Link href={`/credentials/${cluster.credential_id}`} className="inline-flex items-center gap-1.5 hover:underline">
                      <KindGlyph kind={credential?.kind ?? credential?.provider} category={credential?.kind ? (byId[credential.kind]?.category ?? 'other') : 'legacy'} size="xs" />
                      {credential?.name ?? 'Vault credential'}
                    </Link>
                  ) : (
                    'None'
                  ),
                },
                { label: 'Price plan', value: plan ? `${plan.name} · $${plan.usd_per_gpu_minute}/GPU-min` : 'Default plan' },
                { label: 'Created', value: formatDate(cluster.created_at) },
                { label: 'Updated', value: cluster.updated_at ? formatDate(cluster.updated_at) : undefined },
                { label: 'ID', value: cluster.id, mono: true },
              ]}
            />
            <div className="space-y-1.5">
              <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">Labels</p>
              <LabelChips labels={cluster.labels} max={12} emptyText="No labels" />
            </div>
            <div className="space-y-1.5">
              <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">Egress allowlist</p>
              {cluster.egress_allowlist && cluster.egress_allowlist.length > 0 ? (
                <div className="flex flex-wrap gap-1">
                  {cluster.egress_allowlist.map((host) => (
                    <Badge key={host} variant="outline" className="font-mono text-[11px] font-normal">
                      {host}
                    </Badge>
                  ))}
                </div>
              ) : (
                <p className="text-xs text-muted-foreground">Control plane and model sources only.</p>
              )}
            </div>
            {configEntries.length > 0 && (
              <div className="space-y-1.5">
                <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">Provider settings</p>
                <DetailList
                  items={configEntries.map(([key, value]) => ({
                    label: key.replace(/_/g, ' '),
                    value: Array.isArray(value) ? value.join(', ') : typeof value === 'object' ? JSON.stringify(value) : String(value),
                    mono: true,
                  }))}
                />
              </div>
            )}
          </CardContent>
        </Card>

        <Card className="rounded-md shadow-premium lg:col-span-2">
          <CardHeader>
            <CardTitle>Nodes</CardTitle>
            <CardDescription>Machines running the node agent in this cluster.</CardDescription>
          </CardHeader>
          <CardContent>
            {nodes.isLoading ? (
              <div className="space-y-2">
                {Array.from({ length: 3 }).map((_, i) => (
                  <div key={i} className="h-10 animate-pulse rounded bg-muted" />
                ))}
              </div>
            ) : (nodes.data?.items.length ?? 0) === 0 ? (
              <EmptyState
                icon={Server}
                title="No nodes yet"
                description="Generate a join token and run the agent on a GPU machine."
                action={
                  <Button size="sm" className="rounded-sm" onClick={() => router.push(`/nodes/join?cluster_id=${cluster.id}`)}>
                    <Server className="size-4" />
                    Join a node
                  </Button>
                }
              />
            ) : (
              <div className="overflow-x-auto">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Node</TableHead>
                      <TableHead>Status</TableHead>
                      <TableHead>OS</TableHead>
                      <TableHead className="text-right">GPUs</TableHead>
                      <TableHead className="text-right">Instances</TableHead>
                      <TableHead>Cache</TableHead>
                      <TableHead>Heartbeat</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {nodes.data?.items.map((node) => (
                      <TableRow key={node.id} className="cursor-pointer" onClick={() => router.push(`/nodes/${node.id}`)}>
                        <TableCell>
                          <Link href={`/nodes/${node.id}`} className="font-semibold hover:underline" onClick={(e) => e.stopPropagation()}>
                            {node.name}
                          </Link>
                          <div className="text-xs text-muted-foreground">{node.ip_address ?? node.hostname ?? '—'}</div>
                        </TableCell>
                        <TableCell>
                          <NodeStatusBadge node={nodeStateInput(node)} />
                        </TableCell>
                        <TableCell className="text-muted-foreground">
                          {node.os ? NODE_OS_LABELS[node.os] : '—'}
                          {node.arch ? ` · ${node.arch}` : ''}
                        </TableCell>
                        <TableCell className="text-right tabular-nums">{node.device_count ?? 0}</TableCell>
                        <TableCell className="text-right tabular-nums">{node.instance_count ?? 0}</TableCell>
                        <TableCell>
                          {node.cache_limit_bytes ? (
                            <VramMeter used={node.cache_used_bytes ?? 0} total={node.cache_limit_bytes} />
                          ) : (
                            <span className="text-xs text-muted-foreground">{node.cache_used_bytes ? formatBytes(node.cache_used_bytes) : '—'}</span>
                          )}
                        </TableCell>
                        <TableCell>
                          <Heartbeat at={node.last_heartbeat_at} className="text-xs" />
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      <DeleteConfirmationDialog
        open={deleteOpen}
        onOpenChange={setDeleteOpen}
        itemName={cluster.name}
        resourceType="cluster"
        warningMessage={counts.nodes ? `${counts.nodes} node(s) and their instances will be removed from the fleet.` : undefined}
        isDeleting={deleteCluster.isPending}
        onConfirm={() => deleteCluster.mutate(cluster.id, { onSuccess: () => router.push('/clusters') })}
      />
      <ConfirmationDialog
        open={toggleOpen}
        onOpenChange={setToggleOpen}
        title={cluster.status === 'active' ? `Disable ${cluster.name}?` : `Enable ${cluster.name}?`}
        description={
          cluster.status === 'active'
            ? 'The scheduler stops placing new instances in this cluster. Running instances keep serving.'
            : 'The scheduler can place instances in this cluster again.'
        }
        confirmText={cluster.status === 'active' ? 'Disable' : 'Enable'}
        variant={cluster.status === 'active' ? 'destructive' : 'default'}
        isLoading={updateCluster.isPending}
        onConfirm={() => updateCluster.mutate({ id: cluster.id, body: { status: cluster.status === 'active' ? 'disabled' : 'active' } })}
      />
    </PageContainer>
  );
}
