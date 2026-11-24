'use client';

import { useMemo, useState } from 'react';
import Link from 'next/link';
import { BellRing, CheckCheck, CircleAlert, CircleCheck, Info, MoreVertical, PenLine, Plus, RefreshCw, Send, Trash2, TriangleAlert, XCircle } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Switch } from '@/components/ui/switch';
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from '@/components/ui/dropdown-menu';
import { Skeleton } from '@/components/ui/skeleton';
import { PageContainer } from '@/components/shared/PageContainer';
import { PageHeader } from '@/components/shared/PageHeader';
import { ErrorState } from '@/components/shared/ErrorState';
import { EmptyState } from '@/components/shared/EmptyState';
import { StatusBadge } from '@/components/shared/StatusBadge';
import { StatCard } from '@/components/shared/stat-card';
import { TablePagination } from '@/components/shared/table-pagination';
import { RelativeTime } from '@/components/shared/relative-time';
import { JsonView } from '@/components/shared/json-view';
import { DeleteConfirmationDialog } from '@/components/shared/DeleteConfirmationDialog';
import { ListPageSkeleton } from '@/components/shared/ListPageSkeleton';
import { useAcknowledgeAlert, useAlertChannels, useAlertEvent, useAlertEvents, useDeleteAlertChannel, useResendAlert, useResolveAlert, useTestAlertChannel, useUpdateAlertChannel } from '@/hooks/api/automation';
import type { AlertChannel, AlertDeliveryStatus, AlertEvent, AlertEventStatus, AlertSeverity } from '@/hooks/api/automation-api';
import { useUrlState } from '@/hooks/use-url-state';
import { cn } from '@/lib/utils';
import { humanize } from '@/components/automation/rule-meta';
import { CHANNEL_ICONS, ChannelDialog, EVENT_TYPES } from '@/app/(dashboard)/alerts/_components/channel-dialog';

const DEFAULTS = { tab: 'events', status: '', severity: '', page: 1, pageSize: 25 };

const SEVERITY: Record<AlertSeverity, { icon: typeof Info; text: string; bar: string; badge: 'info' | 'warning' | 'error' }> = {
  info: { icon: Info, text: 'text-info-dark dark:text-info-light', bar: 'bg-info', badge: 'info' },
  warning: { icon: TriangleAlert, text: 'text-warning-dark dark:text-warning-light', bar: 'bg-warning', badge: 'warning' },
  critical: { icon: CircleAlert, text: 'text-error-dark dark:text-error-light', bar: 'bg-error', badge: 'error' },
};

const STATUS_BADGE: Record<AlertEventStatus, 'error' | 'warning' | 'success'> = { open: 'error', acknowledged: 'warning', resolved: 'success' };

const DELIVERY: Record<AlertDeliveryStatus, { icon: typeof Info; className: string }> = {
  sent: { icon: CircleCheck, className: 'text-success-dark dark:text-success-light' },
  failed: { icon: XCircle, className: 'text-error-dark dark:text-error-light' },
  pending: { icon: RefreshCw, className: 'text-muted-foreground' },
  suppressed: { icon: Info, className: 'text-muted-foreground' },
};

function resourceHref(e: AlertEvent): string | null {
  if (!e.resourceId) return null;
  switch (e.resourceType) {
    case 'swap_operation':
      return `/swaps/${e.resourceId}`;
    case 'rule':
      return `/rules/${e.resourceId}`;
    case 'layout':
      return `/layouts/${e.resourceId}`;
    case 'node':
      return `/nodes/${e.resourceId}`;
    case 'cluster':
      return '/billing';
    default:
      return null;
  }
}

function channelTarget(c: AlertChannel): string {
  const cfg = c.config as Record<string, unknown>;
  if (c.type === 'slack') return String(cfg.channel ?? 'Slack webhook');
  if (c.type === 'email') return Array.isArray(cfg.recipients) ? `${(cfg.recipients as string[]).length} recipient${(cfg.recipients as string[]).length === 1 ? '' : 's'}: ${(cfg.recipients as string[]).join(', ')}` : 'email';
  try {
    return new URL(String(cfg.url)).host;
  } catch {
    return String(cfg.url ?? 'webhook');
  }
}

function EventSheet({ id, onClose, channels }: { id: string | null; onClose: () => void; channels: AlertChannel[] }) {
  const { data: event, isLoading } = useAlertEvent(id ?? undefined);
  const ack = useAcknowledgeAlert();
  const resolve = useResolveAlert();
  const resend = useResendAlert();
  const sev = event ? SEVERITY[event.severity] : null;
  const href = event ? resourceHref(event) : null;
  return (
    <Sheet open={!!id} onOpenChange={(o) => !o && onClose()}>
      <SheetContent className="w-full overflow-y-auto sm:max-w-lg">
        {isLoading || !event || !sev ? (
          <div className="space-y-3 p-4">
            <Skeleton className="h-6 w-2/3" />
            <Skeleton className="h-20" />
            <Skeleton className="h-32" />
          </div>
        ) : (
          <>
            <SheetHeader className="border-b border-border">
              <div className="flex items-center gap-2 pr-8">
                <sev.icon className={cn('size-4', sev.text)} />
                <StatusBadge variant={sev.badge}>{event.severity}</StatusBadge>
                <StatusBadge variant={STATUS_BADGE[event.status]}>{event.status}</StatusBadge>
                <span className="font-mono text-[11px] text-muted-foreground">{event.eventType}</span>
              </div>
              <SheetTitle className="text-base">{event.title}</SheetTitle>
              {event.message && <SheetDescription className="text-xs leading-relaxed">{event.message}</SheetDescription>}
            </SheetHeader>
            <div className="space-y-5 p-4">
              <div className="flex flex-wrap gap-2">
                {event.status === 'open' && (
                  <Button size="sm" variant="outline" disabled={ack.isPending} onClick={() => ack.mutate(event.id)}>
                    <CheckCheck className="size-3.5" /> Acknowledge
                  </Button>
                )}
                {event.status !== 'resolved' && (
                  <Button size="sm" variant="outline" disabled={resolve.isPending} onClick={() => resolve.mutate(event.id)}>
                    <CircleCheck className="size-3.5" /> Resolve
                  </Button>
                )}
                <Button size="sm" variant="outline" disabled={resend.isPending} onClick={() => resend.mutate({ id: event.id })}>
                  <Send className="size-3.5" /> Re-send to all
                </Button>
                {href && (
                  <Link href={href} className="ml-auto self-center text-xs font-medium underline-offset-4 hover:underline">
                    Open {humanize(event.resourceType).toLowerCase()}
                  </Link>
                )}
              </div>
              <dl className="grid grid-cols-2 gap-1.5 text-xs">
                {[
                  ['Resource', event.resourceName ?? '—'],
                  ['Occurrences', String(event.occurrences)],
                  ['First seen', new Date(event.firstSeenAt).toLocaleString()],
                  ['Last seen', new Date(event.lastSeenAt).toLocaleString()],
                  ['Acknowledged', event.acknowledgedAt ? new Date(event.acknowledgedAt).toLocaleString() : '—'],
                  ['Resolved', event.resolvedAt ? new Date(event.resolvedAt).toLocaleString() : '—'],
                ].map(([k, v]) => (
                  <div key={k} className="rounded-lg bg-muted/50 px-3 py-2">
                    <dt className="text-[11px] text-muted-foreground">{k}</dt>
                    <dd className="font-medium">{v}</dd>
                  </div>
                ))}
              </dl>
              <div className="space-y-2">
                <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">Deliveries</p>
                {event.deliveries.length === 0 ? (
                  <p className="text-xs text-muted-foreground">No channel matched this event.</p>
                ) : (
                  <ul className="divide-y divide-border rounded-lg border border-border">
                    {event.deliveries.map((d) => {
                      const meta = DELIVERY[d.status];
                      const channel = channels.find((c) => c.id === d.channelId);
                      return (
                        <li key={d.id} className="flex items-start gap-3 px-3 py-2.5 text-xs">
                          <meta.icon className={cn('mt-px size-4 shrink-0', meta.className)} />
                          <div className="min-w-0 flex-1">
                            <p className="font-medium">{d.channelName ?? channel?.name ?? 'Channel'}</p>
                            <p className="text-[11px] text-muted-foreground">
                              {humanize(d.status)} · {d.attempts} attempt{d.attempts === 1 ? '' : 's'}
                              {d.responseCode !== null ? ` · HTTP ${d.responseCode}` : ''}
                              {d.sentAt ? ` · ${new Date(d.sentAt).toLocaleTimeString()}` : ''}
                            </p>
                            {d.errorMessage && <p className="text-[11px] text-error-dark dark:text-error-light">{d.errorMessage}</p>}
                          </div>
                          {d.status !== 'sent' && (
                            <Button size="xs" variant="ghost" onClick={() => resend.mutate({ id: event.id, channelId: d.channelId })}>
                              Retry
                            </Button>
                          )}
                        </li>
                      );
                    })}
                  </ul>
                )}
              </div>
              {Object.keys(event.payload).length > 0 && (
                <div className="space-y-2">
                  <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">Payload</p>
                  <JsonView value={event.payload} maxHeight={220} />
                </div>
              )}
            </div>
          </>
        )}
      </SheetContent>
    </Sheet>
  );
}

export function AlertsPage() {
  const { params, setParams } = useUrlState(DEFAULTS);
  const [selected, setSelected] = useState<string | null>(null);
  const [dialog, setDialog] = useState<{ open: boolean; channel: AlertChannel | null }>({ open: false, channel: null });
  const [deleting, setDeleting] = useState<AlertChannel | null>(null);
  const eventsQuery = useMemo(
    () => ({ status: params.status as AlertEventStatus | '', severity: params.severity as AlertSeverity | '', page: params.page, pageSize: params.pageSize }),
    [params.status, params.severity, params.page, params.pageSize],
  );
  const events = useAlertEvents(eventsQuery, { refetchInterval: 15_000 });
  const openEvents = useAlertEvents({ status: 'open', pageSize: 200 }, { refetchInterval: 15_000 });
  const channels = useAlertChannels({ pageSize: 100 });
  const test = useTestAlertChannel();
  const updateChannel = useUpdateAlertChannel();
  const del = useDeleteAlertChannel();

  const open = openEvents.data?.items ?? [];
  const channelList = channels.data?.items ?? [];
  const failing = channelList.filter((c) => c.lastTestOk === false);

  if (events.isLoading && !events.data) return <ListPageSkeleton />;

  return (
    <PageContainer>
      <PageHeader
        title="Alerts"
        description="Rule actions, failed swaps, auto-heal and node / GPU health — sent to Slack, email or a signed webhook."
        actions={
          <Button size="sm" className="rounded-sm" onClick={() => setDialog({ open: true, channel: null })}>
            <Plus className="size-4" /> New channel
          </Button>
        }
      />

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard label="Open critical" value={open.filter((e) => e.severity === 'critical').length} icon={CircleAlert} footnote="Need someone now" />
        <StatCard label="Open warnings" value={open.filter((e) => e.severity === 'warning').length} icon={TriangleAlert} footnote={`${open.filter((e) => e.severity === 'info').length} informational open`} />
        <StatCard label="Channels" value={`${channelList.filter((c) => c.enabled).length} / ${channelList.length}`} icon={BellRing} footnote="enabled / total" />
        <StatCard label="Failing channels" value={failing.length} icon={XCircle} footnote={failing.length ? failing.map((c) => c.name).join(', ') : 'Last test sends succeeded'} />
      </div>

      <Tabs value={params.tab} onValueChange={(v) => setParams({ tab: String(v), page: 1 })}>
        <TabsList className="bg-muted">
          <TabsTrigger value="events" className="px-3 text-xs">
            Events
          </TabsTrigger>
          <TabsTrigger value="channels" className="px-3 text-xs">
            Channels <span className="ml-1 text-muted-foreground">{channelList.length}</span>
          </TabsTrigger>
        </TabsList>
      </Tabs>

      {params.tab === 'channels' ? (
        channels.isError ? (
          <ErrorState title="Could not load channels" onRetry={() => void channels.refetch()} />
        ) : channelList.length === 0 && !channels.isLoading ? (
          <Card className="rounded-md shadow-premium">
            <CardContent>
              <EmptyState icon={BellRing} title="No channels yet" description="Add a Slack webhook, an email list or a signed webhook to hear about rule actions and failures." action={<Button size="sm" onClick={() => setDialog({ open: true, channel: null })}><Plus className="size-4" /> New channel</Button>} />
            </CardContent>
          </Card>
        ) : (
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            {channelList.map((c) => {
              const Icon = CHANNEL_ICONS[c.type];
              return (
                <Card key={c.id} className={cn('gap-0 rounded-xl py-0', !c.enabled && 'opacity-70')}>
                  <CardHeader className="flex flex-row items-start gap-3 px-5 pb-3 pt-4">
                    <span className="flex size-9 shrink-0 items-center justify-center rounded-lg border border-border bg-muted/60">
                      <Icon className="size-4" />
                    </span>
                    <div className="min-w-0 flex-1">
                      <CardTitle className="truncate text-sm font-semibold">{c.name}</CardTitle>
                      <CardDescription className="truncate">{channelTarget(c)}</CardDescription>
                    </div>
                    <Switch checked={c.enabled} onCheckedChange={(v) => updateChannel.mutate({ id: c.id, body: { enabled: v } })} aria-label={c.enabled ? 'Disable channel' : 'Enable channel'} />
                    <DropdownMenu>
                      <DropdownMenuTrigger render={<Button variant="ghost" size="icon-xs" aria-label={`Actions for ${c.name}`} />}>
                        <MoreVertical className="size-4" />
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end" className="w-40">
                        <DropdownMenuItem onClick={() => setDialog({ open: true, channel: c })}>
                          <PenLine className="size-4" /> Edit
                        </DropdownMenuItem>
                        <DropdownMenuSeparator />
                        <DropdownMenuItem className="text-destructive" onClick={() => setDeleting(c)}>
                          <Trash2 className="size-4" /> Delete
                        </DropdownMenuItem>
                      </DropdownMenuContent>
                    </DropdownMenu>
                  </CardHeader>
                  <CardContent className="space-y-3 px-5 pb-4">
                    <div className="flex flex-wrap gap-1.5">
                      <StatusBadge variant="default">≥ {c.minSeverity}</StatusBadge>
                      {c.eventFilter.length === 0 ? (
                        <span className="rounded-md bg-muted px-2 py-1 text-[11px] text-muted-foreground">all events</span>
                      ) : (
                        c.eventFilter.map((f) => (
                          <span key={f} className="rounded-md bg-muted px-2 py-1 font-mono text-[11px]" title={EVENT_TYPES.find((e) => e.value === f)?.label}>
                            {f}
                          </span>
                        ))
                      )}
                    </div>
                    <p className={cn('flex items-start gap-1.5 text-[11px]', c.lastTestOk === false ? 'text-error-dark dark:text-error-light' : 'text-muted-foreground')}>
                      {c.lastTestOk === false ? <XCircle className="mt-px size-3.5 shrink-0" /> : c.lastTestOk ? <CircleCheck className="mt-px size-3.5 shrink-0 text-success" /> : <Info className="mt-px size-3.5 shrink-0" />}
                      <span>
                        {c.lastTestAt ? (
                          <>
                            Last test <RelativeTime value={c.lastTestAt} /> — {c.lastTestOk ? 'delivered' : (c.lastError ?? 'failed')}
                          </>
                        ) : (
                          'Never tested'
                        )}
                      </span>
                    </p>
                  </CardContent>
                  <div className="mt-auto border-t border-border bg-muted/20 px-5 py-3">
                    <Button variant="outline" size="sm" className="w-full" disabled={test.isPending && test.variables === c.id} onClick={() => test.mutate(c.id)}>
                      <Send className="size-3.5" /> {test.isPending && test.variables === c.id ? 'Sending…' : 'Send a test message'}
                    </Button>
                  </div>
                </Card>
              );
            })}
          </div>
        )
      ) : events.isError ? (
        <ErrorState title="Could not load alert events" onRetry={() => void events.refetch()} />
      ) : (
        <Card className="rounded-md shadow-premium">
          <CardHeader>
            <div className="flex flex-wrap items-center gap-3">
              <Select value={params.status || 'all'} onValueChange={(v) => setParams({ status: v === 'all' ? '' : String(v), page: 1 })}>
                <SelectTrigger className="w-40">
                  <SelectValue>{(v: unknown) => (v && v !== 'all' ? humanize(String(v)) : 'Any status')}</SelectValue>
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">Any status</SelectItem>
                  <SelectItem value="open">Open</SelectItem>
                  <SelectItem value="acknowledged">Acknowledged</SelectItem>
                  <SelectItem value="resolved">Resolved</SelectItem>
                </SelectContent>
              </Select>
              <Select value={params.severity || 'all'} onValueChange={(v) => setParams({ severity: v === 'all' ? '' : String(v), page: 1 })}>
                <SelectTrigger className="w-40">
                  <SelectValue>{(v: unknown) => (v && v !== 'all' ? humanize(String(v)) : 'Any severity')}</SelectValue>
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">Any severity</SelectItem>
                  <SelectItem value="critical">Critical</SelectItem>
                  <SelectItem value="warning">Warning</SelectItem>
                  <SelectItem value="info">Info</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </CardHeader>
          <CardContent>
            {(events.data?.items.length ?? 0) === 0 ? (
              <EmptyState icon={CircleCheck} title="All quiet" description="No alert events match these filters." />
            ) : (
              <>
                <ul className="divide-y divide-border">
                  {events.data?.items.map((e) => {
                    const sev = SEVERITY[e.severity];
                    return (
                      <li key={e.id}>
                        <button type="button" onClick={() => setSelected(e.id)} className={cn('group/event flex w-full items-start gap-3 py-3 pl-0 pr-2 text-left outline-none transition-colors hover:bg-muted/40 focus-visible:ring-2 focus-visible:ring-ring/30', e.status === 'resolved' && 'opacity-75')}>
                          <span className={cn('mt-0.5 h-10 w-[3px] shrink-0 rounded-full', sev.bar, e.status === 'resolved' && 'opacity-40')} />
                          <sev.icon className={cn('mt-0.5 size-4 shrink-0', sev.text)} />
                          <span className="min-w-0 flex-1">
                            <span className="flex flex-wrap items-center gap-2">
                              <span className="text-[13px] font-semibold">{e.title}</span>
                              {e.occurrences > 1 && <span className="rounded-[4px] bg-foreground/10 px-1.5 font-mono text-[10px] tabular-nums">×{e.occurrences}</span>}
                            </span>
                            {e.message && <span className="mt-0.5 line-clamp-1 block text-xs text-muted-foreground">{e.message}</span>}
                            <span className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-muted-foreground">
                              <span className="font-mono">{e.eventType}</span>
                              {e.resourceName && <span>{e.resourceName}</span>}
                              {e.deliveries.map((d) => {
                                const meta = DELIVERY[d.status];
                                return (
                                  <span key={d.id} className={cn('inline-flex items-center gap-1', meta.className)} title={`${d.channelName}: ${d.status}${d.errorMessage ? ` — ${d.errorMessage}` : ''}`}>
                                    <meta.icon className="size-3" />
                                    {d.channelName}
                                  </span>
                                );
                              })}
                            </span>
                          </span>
                          <span className="flex shrink-0 flex-col items-end gap-1">
                            <StatusBadge variant={STATUS_BADGE[e.status]}>{e.status}</StatusBadge>
                            <RelativeTime value={e.lastSeenAt} className="text-[11px] text-muted-foreground" />
                          </span>
                        </button>
                      </li>
                    );
                  })}
                </ul>
                <TablePagination page={params.page} pageSize={params.pageSize} total={events.data?.total ?? 0} onPageChange={(p) => setParams({ page: p })} onPageSizeChange={(s) => setParams({ pageSize: s, page: 1 })} noun="events" />
              </>
            )}
          </CardContent>
        </Card>
      )}

      <EventSheet id={selected} onClose={() => setSelected(null)} channels={channelList} />
      {dialog.open && <ChannelDialog key={dialog.channel?.id ?? 'new'} open={dialog.open} onOpenChange={(o) => setDialog((d) => ({ ...d, open: o }))} channel={dialog.channel} onCreated={() => setParams({ tab: 'channels', page: 1 })} />}
      <DeleteConfirmationDialog
        open={!!deleting}
        onOpenChange={(o) => !o && setDeleting(null)}
        itemName={deleting?.name ?? ''}
        resourceType="channel"
        warningMessage="Past deliveries stay on the events. Its vault credential is kept — delete it on the Credentials page if nothing else uses it."
        isDeleting={del.isPending}
        onConfirm={() => {
          if (deleting) del.mutate(deleting.id, { onSuccess: () => setDeleting(null) });
        }}
      />
    </PageContainer>
  );
}
