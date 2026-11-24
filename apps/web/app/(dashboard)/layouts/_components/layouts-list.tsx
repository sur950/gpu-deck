'use client';

import { useMemo, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { CalendarClock, Camera, Copy, LayoutTemplate, MoreVertical, PenLine, Play, Plus, Trash2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from '@/components/ui/dropdown-menu';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { Skeleton } from '@/components/ui/skeleton';
import { PageContainer } from '@/components/shared/PageContainer';
import { PageHeader } from '@/components/shared/PageHeader';
import { ErrorState } from '@/components/shared/ErrorState';
import { EmptyState } from '@/components/shared/EmptyState';
import { RelativeTime } from '@/components/shared/relative-time';
import { DeleteConfirmationDialog } from '@/components/shared/DeleteConfirmationDialog';
import { DashboardSkeleton } from '@/components/charts/dashboard-skeleton';
import { useCaptureLayout, useCreateLayout, useDeleteLayout, useLayouts, useRefDeployments, useRefDevices, useRefInstances, useRules } from '@/hooks/api/automation';
import type { Layout } from '@/hooks/api/automation-api';
import { cn } from '@/lib/utils';
import { LayoutTimeline } from '@/components/automation/layout-timeline';
import { LayoutMini } from '@/components/automation/layout-mini';
import { ApplyLayoutDialog } from '@/components/automation/apply-layout-dialog';
import { Segmented } from '@/components/automation/segmented';
import { deploymentColors, layoutColors, toInput } from '@/components/automation/layout-model';
import { itemsFromLayout } from '@/components/automation/layout-model';
import { browserTimeZone, describeDays, formatInZone, nextOccurrences, zoneAbbreviation } from '@/components/automation/schedule';
import { formatCountdown } from '@/components/automation/run-meta';
import { useNow } from '@/components/automation/use-now';

export function LayoutsList() {
  const router = useRouter();
  const now = useNow(30_000);
  const layoutsQuery = useLayouts({ pageSize: 100 });
  const rulesQuery = useRules({ pageSize: 500, type: 'time_schedule' });
  const devices = useRefDevices();
  const instances = useRefInstances();
  const deployments = useRefDeployments();
  const capture = useCaptureLayout();
  const create = useCreateLayout();
  const del = useDeleteLayout();
  const [captureOpen, setCaptureOpen] = useState(false);
  const [captureName, setCaptureName] = useState('');
  const [captureDescription, setCaptureDescription] = useState('');
  const [applying, setApplying] = useState<Layout | null>(null);
  const [deleting, setDeleting] = useState<Layout | null>(null);
  const localTz = browserTimeZone();
  const scheduleRules = useMemo(() => (rulesQuery.data?.items ?? []).filter((r) => r.type === 'time_schedule'), [rulesQuery.data]);
  const scheduleTz = (scheduleRules.find((r) => r.enabled)?.params.timezone as string | undefined) ?? localTz;
  const [tzMode, setTzMode] = useState<'local' | 'schedule'>('local');
  const tz = tzMode === 'local' ? localTz : scheduleTz;

  const layouts = useMemo(() => layoutsQuery.data?.items ?? [], [layoutsQuery.data]);
  const lColors = useMemo(() => layoutColors(layouts), [layouts]);
  const dColors = useMemo(() => deploymentColors(deployments.data ?? []), [deployments.data]);
  const upcoming = scheduleRules
    .filter((r) => r.enabled)
    .flatMap((r) => nextOccurrences(r.params as { at?: string; days?: string[]; cron?: string | null; timezone?: string }, now, 3).map((at) => ({ at, rule: r, layoutId: r.params.layout_id as string })))
    .sort((a, b) => a.at - b.at)
    .slice(0, 4);

  if (layoutsQuery.isLoading) return <DashboardSkeleton />;

  function duplicate(l: Layout) {
    create.mutate(
      { name: `${l.name} (copy)`, description: l.description, unlisted_policy: l.unlistedPolicy, items: toInput(itemsFromLayout(l)) },
      { onSuccess: (copy) => router.push(`/layouts/${copy.id}`) },
    );
  }

  return (
    <PageContainer>
      <PageHeader
        title="Layouts"
        description="Named placements of deployments on GPUs — for example Day and Night — that schedules switch between."
        actions={
          <>
            <Button variant="outline" size="sm" className="rounded-sm" onClick={() => setCaptureOpen(true)}>
              <Camera className="size-4" /> Capture current placement
            </Button>
            <Button size="sm" className="rounded-sm" onClick={() => router.push('/layouts/new')}>
              <Plus className="size-4" /> New layout
            </Button>
          </>
        }
      />

      <Card className="gap-0 rounded-xl py-0">
        <CardHeader className="flex flex-col gap-3 border-b border-border px-5 py-4 sm:flex-row sm:items-center sm:justify-between">
          <div className="space-y-0.5">
            <CardTitle className="flex items-center gap-2 text-sm font-semibold">
              <CalendarClock className="size-4" /> Today’s schedule
            </CardTitle>
            <CardDescription>Which layout is in force through the day, from the enabled time-schedule rules.</CardDescription>
          </div>
          {zoneAbbreviation(scheduleTz, now) !== zoneAbbreviation(localTz, now) && (
            <Segmented
              size="sm"
              value={tzMode}
              onChange={setTzMode}
              options={[
                { value: 'local', label: `Your time (${zoneAbbreviation(localTz, now)})` },
                { value: 'schedule', label: `${scheduleTz} (${zoneAbbreviation(scheduleTz, now)})` },
              ]}
              aria-label="Timezone"
            />
          )}
        </CardHeader>
        <CardContent className="grid gap-6 px-5 py-5 lg:grid-cols-[minmax(0,1fr)_260px]">
          {rulesQuery.isLoading ? <Skeleton className="h-28" /> : <LayoutTimeline rules={scheduleRules} layouts={layouts} colors={lColors} timeZone={tz} now={now} />}
          <div className="space-y-2 lg:border-l lg:border-border lg:pl-6">
            <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">Next switches</p>
            {upcoming.length === 0 ? (
              <p className="text-xs text-muted-foreground">None scheduled.</p>
            ) : (
              <ul className="space-y-2">
                {upcoming.map((u) => (
                  <li key={`${u.rule.id}-${u.at}`} className="flex items-start gap-2.5 text-xs">
                    <span className="mt-1 size-2 shrink-0 rounded-full" style={{ backgroundColor: lColors.get(u.layoutId) }} />
                    <span className="min-w-0 flex-1">
                      <span className="flex justify-between gap-2">
                        <span className="font-semibold">{layouts.find((l) => l.id === u.layoutId)?.name ?? 'Layout'}</span>
                        <span className="tabular-nums text-muted-foreground">{formatCountdown(u.at - now)}</span>
                      </span>
                      <Link href={`/rules/${u.rule.id}`} className="block truncate text-[11px] text-muted-foreground hover:text-foreground">
                        {formatInZone(u.at, tz)} · {u.rule.name}
                      </Link>
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </CardContent>
      </Card>

      {layoutsQuery.isError ? (
        <ErrorState title="Could not load layouts" description="The control plane did not answer. Retry in a moment." onRetry={() => void layoutsQuery.refetch()} />
      ) : layouts.length === 0 ? (
        <Card className="rounded-md shadow-premium">
          <CardContent>
            <EmptyState
              icon={LayoutTemplate}
              title="No layouts yet"
              description="Capture what runs now as your first layout, then make a lean variant for nights and weekends."
              action={
                <Button size="sm" className="rounded-sm" onClick={() => setCaptureOpen(true)}>
                  <Camera className="size-4" /> Capture current placement
                </Button>
              }
            />
          </CardContent>
        </Card>
      ) : (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {layouts.map((l) => {
            const usedBy = scheduleRules.filter((r) => r.params.layout_id === l.id);
            const replicas = l.items.reduce((s, i) => s + i.replicas, 0);
            return (
              <Card key={l.id} className={cn('group/layout gap-0 overflow-hidden rounded-xl py-0 transition-all', l.isActive && 'ring-1 ring-success/40')}>
                <div className="h-1" style={{ backgroundColor: lColors.get(l.id) }} />
                <CardHeader className="px-5 pb-3 pt-4">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <CardTitle className="flex items-center gap-2 text-[15px] font-semibold tracking-tight">
                        <Link href={`/layouts/${l.id}`} className="truncate hover:underline">
                          {l.name}
                        </Link>
                        {l.isActive && (
                          <span className="inline-flex items-center gap-1 rounded-full border border-success/30 bg-success/10 px-2 py-0.5 text-[10px] font-semibold text-success-dark dark:text-success-light">
                            <span className="size-1.5 rounded-full bg-success" /> Running now
                          </span>
                        )}
                      </CardTitle>
                      <CardDescription className="mt-1 line-clamp-2 min-h-[2.5em]">{l.description ?? 'No description'}</CardDescription>
                    </div>
                    <DropdownMenu>
                      <DropdownMenuTrigger render={<Button variant="ghost" size="icon-sm" aria-label={`Actions for ${l.name}`} />}>
                        <MoreVertical className="size-4" />
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end" className="w-44">
                        <DropdownMenuItem onClick={() => router.push(`/layouts/${l.id}`)}>
                          <PenLine className="size-4" /> Edit
                        </DropdownMenuItem>
                        <DropdownMenuItem onClick={() => duplicate(l)}>
                          <Copy className="size-4" /> Duplicate
                        </DropdownMenuItem>
                        <DropdownMenuSeparator />
                        <DropdownMenuItem className="text-destructive" onClick={() => setDeleting(l)}>
                          <Trash2 className="size-4" /> Delete
                        </DropdownMenuItem>
                      </DropdownMenuContent>
                    </DropdownMenu>
                  </div>
                </CardHeader>
                <CardContent className="space-y-4 px-5 pb-4">
                  {devices.isLoading ? <Skeleton className="h-14" /> : <LayoutMini layout={l} devices={devices.data ?? []} instances={instances.data ?? []} colors={dColors} />}
                  <div className="flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-muted-foreground">
                    <span>
                      <b className="font-semibold text-foreground">{replicas}</b> replica{replicas === 1 ? '' : 's'} · {new Set(l.items.map((i) => i.deploymentId)).size} deployments
                    </span>
                    <span>unlisted: {l.unlistedPolicy === 'unload' ? 'unload' : 'keep running'}</span>
                    <span>{l.appliedAt ? <>applied <RelativeTime value={l.appliedAt} /></> : 'never applied'}</span>
                  </div>
                  <div className="flex min-h-6 flex-wrap gap-1.5">
                    {usedBy.length === 0 ? (
                      <span className="text-[11px] text-muted-foreground">Not scheduled — apply by hand</span>
                    ) : (
                      usedBy.map((r) => (
                        <Link key={r.id} href={`/rules/${r.id}`} className={cn('inline-flex h-6 items-center gap-1.5 rounded-[5px] bg-muted px-2 text-[11px] font-medium hover:bg-muted/70', !r.enabled && 'opacity-60')}>
                          <CalendarClock className="size-3 text-muted-foreground" />
                          {r.params.cron ? String(r.params.cron) : `${String(r.params.at ?? '00:00')} ${describeDays(r.params.days as string[] | undefined)}`}
                          {!r.enabled && ' (off)'}
                        </Link>
                      ))
                    )}
                  </div>
                </CardContent>
                <div className="mt-auto flex gap-2 border-t border-border bg-muted/20 px-5 py-3">
                  <Button variant="outline" size="sm" className="flex-1" onClick={() => router.push(`/layouts/${l.id}`)}>
                    <PenLine className="size-3.5" /> Open editor
                  </Button>
                  <Button size="sm" className="flex-1" onClick={() => setApplying(l)}>
                    <Play className="size-3.5" /> Preview &amp; apply
                  </Button>
                </div>
              </Card>
            );
          })}
        </div>
      )}

      <Dialog open={captureOpen} onOpenChange={setCaptureOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Capture the current placement</DialogTitle>
            <DialogDescription>Saves what runs right now — every deployment, node, GPU, slice and traffic weight — as a layout you can switch back to.</DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <label className="block space-y-1.5 text-xs font-medium">
              Name
              <Input value={captureName} onChange={(e) => setCaptureName(e.target.value)} placeholder="e.g. Weekday peak" autoFocus />
            </label>
            <label className="block space-y-1.5 text-xs font-medium">
              Description <span className="font-normal text-muted-foreground">optional</span>
              <Textarea value={captureDescription} onChange={(e) => setCaptureDescription(e.target.value)} rows={2} className="text-xs" />
            </label>
          </div>
          <DialogFooter>
            <Button variant="outline" size="sm" onClick={() => setCaptureOpen(false)}>
              Cancel
            </Button>
            <Button
              size="sm"
              disabled={!captureName.trim() || capture.isPending}
              onClick={() =>
                capture.mutate(
                  { name: captureName.trim(), description: captureDescription.trim() || null },
                  {
                    onSuccess: (l) => {
                      setCaptureOpen(false);
                      setCaptureName('');
                      setCaptureDescription('');
                      router.push(`/layouts/${l.id}`);
                    },
                  },
                )
              }
            >
              <Camera className="size-4" /> Capture
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <ApplyLayoutDialog layout={applying} open={!!applying} onOpenChange={(o) => !o && setApplying(null)} />
      <DeleteConfirmationDialog
        open={!!deleting}
        onOpenChange={(o) => !o && setDeleting(null)}
        itemName={deleting?.name ?? ''}
        resourceType="layout"
        warningMessage={deleting && scheduleRules.some((r) => r.params.layout_id === deleting.id) ? 'Time-schedule rules still point at this layout — the delete will be refused until they use another one.' : 'Deleting a layout does not change what is running.'}
        isDeleting={del.isPending}
        onConfirm={() => {
          if (deleting) del.mutate(deleting.id, { onSuccess: () => setDeleting(null) });
        }}
      />
    </PageContainer>
  );
}
