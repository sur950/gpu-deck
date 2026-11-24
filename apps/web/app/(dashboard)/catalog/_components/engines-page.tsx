'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { ArrowLeft, Check, Container, Cpu, Minus, MoreVertical, Pencil, Plus, Star, Trash2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Switch } from '@/components/ui/switch';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from '@/components/ui/dropdown-menu';
import { PageContainer } from '@/components/shared/PageContainer';
import { PageHeader } from '@/components/shared/PageHeader';
import { ListPageSkeleton } from '@/components/shared/ListPageSkeleton';
import { ErrorState } from '@/components/shared/ErrorState';
import { StatusBadge } from '@/components/shared/StatusBadge';
import { DeleteConfirmationDialog } from '@/components/shared/DeleteConfirmationDialog';
import { VendorBadge } from '@/components/fleet/vendor-badge';
import { FormatBadge, MODEL_TYPE_ICONS } from '@/components/models/model-badges';
import { EngineVersionDialog } from '@/components/models/engine-version-dialog';
import { useDeleteEngineVersion, useEngines, useUpdateEngine } from '@/hooks/api/models';
import { cn } from '@/lib/utils';
import { MODEL_TYPE_LABELS, type Engine, type EngineVersion } from '@/types/models';

const CAPABILITY_LABELS: [string, string][] = [
  ['router_mode', 'Router mode (hot swap)'],
  ['tensor_parallel', 'Tensor parallel'],
  ['pipeline_parallel', 'Pipeline parallel'],
  ['pd_split', 'Prefill / decode split'],
  ['speculative_decoding', 'Speculative decoding'],
  ['kv_sharing', 'Shared KV cache'],
  ['slices', 'GPU slices'],
  ['control_vectors', 'Control vectors'],
];

function artifactPlatforms(v: EngineVersion): string {
  if (v.image) return v.image;
  const keys = Object.keys(v.artifacts ?? {});
  return keys.length ? keys.join(' · ') : 'preinstalled binary (engine_paths)';
}

function EngineCard({ engine }: { engine: Engine }) {
  const update = useUpdateEngine();
  const remove = useDeleteEngineVersion();
  const [editing, setEditing] = useState<EngineVersion | null>(null);
  const [adding, setAdding] = useState(false);
  const [deleting, setDeleting] = useState<EngineVersion | null>(null);
  const caps = engine.capabilities ?? {};
  const versions = engine.versions ?? [];
  const formats = Array.isArray(caps.native_formats) ? (caps.native_formats as string[]) : [];

  return (
    <Card className={cn('gap-0 rounded-md py-0 shadow-premium', !engine.is_enabled && 'opacity-75')}>
      <CardHeader className="flex flex-row items-start justify-between gap-4 border-b border-border px-4 py-4">
        <div className="min-w-0 space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            <CardTitle className="text-[15px] font-semibold">{engine.display_name}</CardTitle>
            <span className="inline-flex h-5 items-center gap-1 rounded-sm border border-border px-1.5 text-[10.5px] text-muted-foreground">
              {engine.runtime === 'container' ? <Container className="size-3" /> : <Cpu className="size-3" />}
              {engine.runtime}
            </span>
            {!engine.is_builtin && <span className="rounded-sm border border-info/40 bg-info/10 px-1.5 text-[10.5px]">custom</span>}
          </div>
          <CardDescription className="text-xs leading-relaxed">{engine.description}</CardDescription>
        </div>
        <label className="flex shrink-0 items-center gap-2 text-xs text-muted-foreground">
          {engine.is_enabled ? 'Enabled' : 'Disabled'}
          <Switch checked={engine.is_enabled} disabled={update.isPending} onCheckedChange={(v) => update.mutate({ id: engine.id, body: { is_enabled: v } })} aria-label={`Enable ${engine.display_name}`} />
        </label>
      </CardHeader>
      <CardContent className="space-y-4 px-4 py-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-1.5">
            <p className="text-[10.5px] font-medium tracking-wide text-muted-foreground uppercase">Serves</p>
            <div className="flex flex-wrap gap-1">
              {(engine.supported_model_types ?? []).map((t) => {
                const Icon = MODEL_TYPE_ICONS[t];
                return (
                  <span key={t} className="inline-flex h-5 items-center gap-1 rounded-sm border border-border px-1.5 text-[11px]">
                    <Icon className="size-3 text-muted-foreground" />
                    {MODEL_TYPE_LABELS[t]}
                  </span>
                );
              })}
            </div>
            <div className="flex flex-wrap gap-1 pt-1">
              {(engine.supported_formats ?? []).map((f) => (
                <FormatBadge key={f} format={f} />
              ))}
            </div>
          </div>
          <div className="space-y-1.5">
            <p className="text-[10.5px] font-medium tracking-wide text-muted-foreground uppercase">Runs on</p>
            <div className="flex flex-wrap items-center gap-1.5">
              {(engine.supported_vendors ?? []).map((v) => (
                <VendorBadge key={v} vendor={v} withLabel={false} />
              ))}
              <span className="text-[11px] text-muted-foreground">· {(engine.supported_os ?? []).map((o) => (o === 'macos' ? 'macOS' : 'Linux')).join(', ')}</span>
            </div>
            {formats.length > 0 && <p className="text-[11px] text-muted-foreground">Native API: {formats.join(', ')}</p>}
          </div>
        </div>
        <ul className="grid grid-cols-2 gap-x-4 gap-y-1 text-[11.5px] sm:grid-cols-4">
          {CAPABILITY_LABELS.map(([key, label]) => {
            const on = caps[key] === true;
            return (
              <li key={key} className={cn('flex items-center gap-1.5', on ? 'text-foreground' : 'text-muted-foreground/60')}>
                {on ? <Check className="size-3 text-success" /> : <Minus className="size-3" />}
                {label}
              </li>
            );
          })}
        </ul>
        <div className="rounded-lg border border-border">
          <div className="flex items-center justify-between border-b border-border px-3 py-2">
            <span className="text-xs font-medium">Versions</span>
            <Button variant="ghost" size="xs" onClick={() => setAdding(true)}>
              <Plus className="size-3" />
              Add version
            </Button>
          </div>
          {versions.length === 0 ? (
            <p className="px-3 py-4 text-center text-xs text-muted-foreground">
              {engine.name === 'custom' ? 'Register an OpenAI-compatible server image to use it in deployments.' : 'No versions registered.'}
            </p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Version</TableHead>
                  <TableHead>{engine.runtime === 'container' ? 'Image' : 'Artifacts'}</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead className="w-10" />
                </TableRow>
              </TableHeader>
              <TableBody>
                {versions.map((v) => (
                  <TableRow key={v.id}>
                    <TableCell>
                      <span className="inline-flex items-center gap-1.5 font-mono text-xs font-medium">
                        {v.version}
                        {v.is_default && <Star className="size-3 fill-current text-brand" aria-label="default" />}
                      </span>
                      {v.min_driver_version && <div className="text-[10.5px] text-muted-foreground">driver ≥ {v.min_driver_version}</div>}
                    </TableCell>
                    <TableCell className="max-w-[260px]">
                      <span className="block truncate font-mono text-[10.5px] text-muted-foreground" title={artifactPlatforms(v)}>
                        {artifactPlatforms(v)}
                      </span>
                    </TableCell>
                    <TableCell>
                      <StatusBadge variant={v.status === 'available' ? 'active' : v.status === 'deprecated' ? 'warning' : 'inactive'} className="py-0">
                        {v.status}
                      </StatusBadge>
                    </TableCell>
                    <TableCell>
                      <DropdownMenu>
                        <DropdownMenuTrigger className="inline-flex h-7 w-7 cursor-pointer items-center justify-center rounded-md hover:bg-accent" aria-label={`Actions for ${v.version}`}>
                          <MoreVertical className="h-4 w-4" />
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="end">
                          <DropdownMenuItem onClick={() => setEditing(v)}>
                            <Pencil className="mr-2 h-4 w-4" />
                            Edit
                          </DropdownMenuItem>
                          <DropdownMenuSeparator />
                          <DropdownMenuItem variant="destructive" onClick={() => setDeleting(v)}>
                            <Trash2 className="mr-2 h-4 w-4" />
                            Delete
                          </DropdownMenuItem>
                        </DropdownMenuContent>
                      </DropdownMenu>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </div>
      </CardContent>
      {(adding || editing) && <EngineVersionDialog key={editing?.id ?? 'new'} engine={engine} version={editing} open onOpenChange={(o) => { if (!o) { setAdding(false); setEditing(null); } }} />}
      <DeleteConfirmationDialog
        open={!!deleting}
        onOpenChange={(o) => !o && setDeleting(null)}
        itemName={deleting ? `${engine.display_name} ${deleting.version}` : ''}
        resourceType="engine version"
        warningMessage="Refused while a deployment runs on it."
        isDeleting={remove.isPending}
        onConfirm={() => {
          if (deleting) remove.mutate({ engineId: engine.id, versionId: deleting.id }, { onSuccess: () => setDeleting(null) });
        }}
      />
    </Card>
  );
}

export function EnginesPage() {
  const router = useRouter();
  const engines = useEngines();
  if (engines.isLoading) return <ListPageSkeleton stats={0} rows={6} />;
  return (
    <PageContainer>
      <PageHeader
        title="Engines"
        description="The inference engines nodes can run, their pinned versions and what each one supports. Auto-pick and compatibility checks read from here."
        actions={
          <Button variant="outline" size="sm" className="rounded-sm" onClick={() => router.push('/catalog')}>
            <ArrowLeft className="size-4" />
            Back to catalog
          </Button>
        }
      />
      {engines.isError ? (
        <ErrorState title="Could not load engines" description="The control plane did not answer." onRetry={() => void engines.refetch()} />
      ) : (
        <div className="grid items-start gap-4 xl:grid-cols-2">
          {(engines.data ?? []).map((e) => (
            <EngineCard key={e.id} engine={e} />
          ))}
        </div>
      )}
    </PageContainer>
  );
}
