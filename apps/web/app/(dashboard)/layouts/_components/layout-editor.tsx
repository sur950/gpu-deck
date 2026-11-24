'use client';

import { useMemo, useState } from 'react';
import Link from 'next/link';
import { useParams, useRouter } from 'next/navigation';
import { CalendarClock, GitCompareArrows, Loader2, MoreVertical, Play, RotateCcw, Save, Trash2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from '@/components/ui/dropdown-menu';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { Skeleton } from '@/components/ui/skeleton';
import { PageContainer } from '@/components/shared/PageContainer';
import { PageHeader } from '@/components/shared/PageHeader';
import { ErrorState } from '@/components/shared/ErrorState';
import { DeleteConfirmationDialog } from '@/components/shared/DeleteConfirmationDialog';
import { DashboardSkeleton } from '@/components/charts/dashboard-skeleton';
import { useCreateLayout, useDeleteLayout, useLayout, useRefDeployments, useRefDevices, useRefInstances, useRefNodes, useRules, useUpdateLayout } from '@/hooks/api/automation';
import type { Layout } from '@/hooks/api/automation-api';
import { GpuGrid } from '@/components/automation/gpu-grid';
import { ClientDiffList } from '@/components/automation/layout-diff-list';
import { ApplyLayoutDialog } from '@/components/automation/apply-layout-dialog';
import { Segmented } from '@/components/automation/segmented';
import { clientDiff, deploymentColors, deviceLoads, deviceShortName, itemsFromInstances, itemsFromLayout, toInput, type EditorItem } from '@/components/automation/layout-model';
import { describeDays } from '@/components/automation/schedule';

interface EditorProps {
  layout: Layout | null;
}

function snapshot(name: string, description: string, policy: string, items: EditorItem[]): string {
  return JSON.stringify({ name, description, policy, items: toInput(items) });
}

function Editor({ layout }: EditorProps) {
  const router = useRouter();
  const deployments = useRefDeployments();
  const nodes = useRefNodes();
  const devices = useRefDevices();
  const instances = useRefInstances();
  const rules = useRules({ pageSize: 500, type: 'time_schedule' });
  const create = useCreateLayout();
  const update = useUpdateLayout();
  const del = useDeleteLayout();

  const [name, setName] = useState(layout?.name ?? '');
  const [description, setDescription] = useState(layout?.description ?? '');
  const [policy, setPolicy] = useState<'keep' | 'unload'>(layout?.unlistedPolicy ?? 'keep');
  const [items, setItems] = useState<EditorItem[]>(() => (layout ? itemsFromLayout(layout) : []));
  const [saved, setSaved] = useState(() => (layout ? snapshot(layout.name, layout.description ?? '', layout.unlistedPolicy, itemsFromLayout(layout)) : ''));
  const [applyOpen, setApplyOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [showUnchanged, setShowUnchanged] = useState(false);

  const deploymentList = useMemo(() => deployments.data ?? [], [deployments.data]);
  const deviceList = useMemo(() => devices.data ?? [], [devices.data]);
  const instanceList = useMemo(() => instances.data ?? [], [instances.data]);
  const colors = useMemo(() => deploymentColors(deploymentList), [deploymentList]);
  const names = useMemo(() => new Map(deploymentList.map((d) => [d.id, d.name])), [deploymentList]);
  const nodeName = (id: string) => nodes.data?.find((n) => n.id === id)?.name ?? id.slice(0, 8);
  const deviceName = (id: string) => {
    const d = deviceList.find((x) => x.id === id);
    return d ? `${d.nodeName ?? nodeName(d.nodeId)} / ${deviceShortName(d)}` : id.slice(0, 8);
  };
  const diff = clientDiff(items, policy, instanceList, deploymentList, { node: nodeName, device: deviceName });
  const changes = diff.filter((e) => e.action !== 'noop');
  const overCommitted = [...deviceLoads(items, deviceList, instanceList).values()].filter((l) => l.over);
  const dirty = snapshot(name, description, policy, items) !== saved;
  const usedBy = (rules.data?.items ?? []).filter((r) => layout && r.params.layout_id === layout.id);
  const nameError = submitted && !name.trim() ? 'Give the layout a name' : null;
  const itemsError = submitted && items.length === 0 ? 'Place at least one deployment' : null;
  const busy = create.isPending || update.isPending;
  const loading = deployments.isLoading || nodes.isLoading || devices.isLoading || instances.isLoading;

  async function save(): Promise<Layout | null> {
    setSubmitted(true);
    if (!name.trim() || items.length === 0) return null;
    const body = { name: name.trim(), description: description.trim() || null, unlisted_policy: policy, items: toInput(items) };
    const result = layout ? await update.mutateAsync({ id: layout.id, body }).catch(() => null) : await create.mutateAsync(body).catch(() => null);
    if (result) {
      setSaved(snapshot(name.trim(), description.trim(), policy, items));
      if (!layout) router.replace(`/layouts/${result.id}`);
    }
    return result;
  }

  return (
    <PageContainer>
      <PageHeader
        title={layout ? layout.name : 'New layout'}
        description={layout ? (layout.isActive ? 'Running now. Edits take effect the next time it is applied.' : 'Edit the placement, then preview and apply — or let a schedule switch to it.') : 'Drag deployments onto GPUs. Nothing changes on the fleet until you apply.'}
        actions={
          <>
            {dirty && <span className="hidden text-[11px] text-muted-foreground sm:inline">Unsaved changes</span>}
            <Button variant="outline" size="sm" className="rounded-sm" disabled={busy || !dirty} onClick={() => void save()}>
              {busy ? <Loader2 className="size-4 animate-spin" /> : <Save className="size-4" />}
              {layout ? 'Save' : 'Create layout'}
            </Button>
            {layout && (
              <Button
                size="sm"
                className="rounded-sm"
                disabled={busy || overCommitted.length > 0}
                onClick={async () => {
                  if (dirty && !(await save())) return;
                  setApplyOpen(true);
                }}
              >
                <Play className="size-4" /> {dirty ? 'Save & apply…' : 'Preview & apply…'}
              </Button>
            )}
            {layout && (
              <DropdownMenu>
                <DropdownMenuTrigger render={<Button variant="ghost" size="icon-sm" aria-label="More actions" />}>
                  <MoreVertical className="size-4" />
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end" className="w-52">
                  <DropdownMenuItem onClick={() => setItems(itemsFromInstances(instanceList))}>
                    <RotateCcw className="size-4" /> Reset to what runs now
                  </DropdownMenuItem>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem className="text-destructive" onClick={() => setDeleteOpen(true)}>
                    <Trash2 className="size-4" /> Delete layout
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            )}
          </>
        }
      />

      <Card className="rounded-xl">
        <CardContent className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]">
          <div className="space-y-3">
            <label className="block space-y-1.5 text-[13px] font-medium">
              Name
              <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Night" maxLength={255} aria-invalid={!!nameError} className="h-9" />
              {nameError && <span className="block text-[11px] font-medium text-destructive">{nameError}</span>}
            </label>
            <label className="block space-y-1.5 text-[13px] font-medium">
              Description <span className="text-[11px] font-normal text-muted-foreground">optional</span>
              <Textarea value={description} onChange={(e) => setDescription(e.target.value)} rows={2} maxLength={2048} className="text-xs" placeholder="When this layout is used and why." />
            </label>
          </div>
          <div className="space-y-2">
            <p className="text-[13px] font-medium">Deployments not placed in this layout</p>
            <Segmented
              variant="block"
              value={policy}
              onChange={setPolicy}
              options={[
                { value: 'keep', label: 'Keep running', hint: 'Only the deployments placed below are touched.' },
                { value: 'unload', label: 'Unload them', hint: 'Anything not placed is drained and unloaded — always-on models excepted.' },
              ]}
              aria-label="Unlisted deployments"
            />
            {usedBy.length > 0 && (
              <p className="flex flex-wrap items-center gap-1.5 pt-1 text-[11px] text-muted-foreground">
                <CalendarClock className="size-3" /> Scheduled by
                {usedBy.map((r) => (
                  <Link key={r.id} href={`/rules/${r.id}`} className="rounded-[4px] bg-muted px-1.5 py-0.5 font-medium text-foreground hover:bg-muted/70">
                    {r.name} · {r.params.cron ? String(r.params.cron) : `${String(r.params.at)} ${describeDays(r.params.days as string[] | undefined)}`}
                  </Link>
                ))}
              </p>
            )}
          </div>
        </CardContent>
      </Card>

      <div className="grid items-start gap-6 2xl:grid-cols-[minmax(0,1fr)_360px]">
        <Card className="gap-0 rounded-xl py-0">
          <CardHeader className="flex flex-col gap-2 border-b border-border px-5 py-4 sm:flex-row sm:items-center sm:justify-between">
            <div className="space-y-0.5">
              <CardTitle className="text-sm font-semibold">Placement</CardTitle>
              <CardDescription>VRAM bars show the planned load per GPU; sizes come from the slice or the running instance.</CardDescription>
            </div>
            {!layout && items.length === 0 && !loading && (
              <Button variant="outline" size="sm" disabled={!instances.data} onClick={() => setItems(itemsFromInstances(instanceList))}>
                <RotateCcw className="size-3.5" /> Start from what runs now
              </Button>
            )}
          </CardHeader>
          <CardContent className="px-5 py-5">
            {itemsError && <p className="mb-3 rounded-md bg-destructive/10 p-2.5 text-xs text-destructive">{itemsError}</p>}
            {loading ? (
              <div className="grid gap-4 lg:grid-cols-[250px_minmax(0,1fr)]">
                <Skeleton className="h-72" />
                <Skeleton className="h-72" />
              </div>
            ) : (
              <GpuGrid items={items} onChange={setItems} deployments={deploymentList} nodes={nodes.data ?? []} devices={deviceList} instances={instanceList} colors={colors} />
            )}
          </CardContent>
        </Card>

        <Card className="gap-0 rounded-xl py-0 2xl:sticky 2xl:top-4">
          <CardHeader className="border-b border-border px-5 py-4">
            <CardTitle className="flex items-center gap-2 text-sm font-semibold">
              <GitCompareArrows className="size-4" /> Changes vs what runs now
            </CardTitle>
            <CardDescription>
              {changes.length === 0 ? 'Applying this layout changes nothing.' : `${changes.length} deployment${changes.length === 1 ? '' : 's'} would change when applied.`}
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3 px-4 py-4">
            {overCommitted.length > 0 && (
              <p className="rounded-md bg-destructive/10 p-2.5 text-xs text-destructive">
                {overCommitted.map((l) => deviceShortName(l.device)).join(', ')} {overCommitted.length === 1 ? 'is' : 'are'} over capacity — move something before applying.
              </p>
            )}
            {loading ? <Skeleton className="h-40" /> : <ClientDiffList entries={diff} names={names} colors={colors} showUnchanged={showUnchanged} />}
            <button type="button" className="text-[11px] font-medium text-muted-foreground hover:text-foreground" onClick={() => setShowUnchanged((v) => !v)}>
              {showUnchanged ? 'Hide unchanged' : 'Show unchanged too'}
            </button>
            <p className="border-t border-border pt-3 text-[11px] leading-relaxed text-muted-foreground">This preview is computed in the browser. Applying asks the control plane for the authoritative plan first, including prefetch sizes and placement checks.</p>
          </CardContent>
        </Card>
      </div>

      {layout && <ApplyLayoutDialog layout={applyOpen ? layout : null} open={applyOpen} onOpenChange={setApplyOpen} />}
      {layout && (
        <DeleteConfirmationDialog
          open={deleteOpen}
          onOpenChange={setDeleteOpen}
          itemName={layout.name}
          resourceType="layout"
          warningMessage={usedBy.length ? 'Time-schedule rules still point at this layout — the delete will be refused until they use another one.' : 'Deleting a layout does not change what is running.'}
          isDeleting={del.isPending}
          onConfirm={() => del.mutate(layout.id, { onSuccess: () => router.push('/layouts') })}
        />
      )}
    </PageContainer>
  );
}

/** /layouts/new and /layouts/[id] share the editor. */
export function LayoutEditor() {
  const params = useParams<{ id?: string }>();
  const id = params.id;
  const { data, isLoading, isError, refetch } = useLayout(id);
  if (!id) return <Editor layout={null} />;
  if (isLoading) return <DashboardSkeleton />;
  if (isError || !data) {
    return (
      <PageContainer>
        <ErrorState title="Layout not found" description="It may have been deleted, or the control plane did not answer." onRetry={() => void refetch()} />
      </PageContainer>
    );
  }
  return <Editor key={data.id} layout={data} />;
}
