'use client';

import { useEffect, useMemo, useState } from 'react';
import { Bot, CircleCheck, CircleX, Download, History, KeyRound, Server, User, Workflow } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader } from '@/components/ui/card';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { PageContainer } from '@/components/shared/PageContainer';
import { PageHeader } from '@/components/shared/PageHeader';
import { ListPageSkeleton } from '@/components/shared/ListPageSkeleton';
import { ErrorState } from '@/components/shared/ErrorState';
import { EmptyState } from '@/components/shared/EmptyState';
import { StatusBadge } from '@/components/shared/StatusBadge';
import { SearchInput } from '@/components/shared/search-input';
import { TablePagination } from '@/components/shared/table-pagination';
import { RelativeTime } from '@/components/shared/relative-time';
import { DetailList, SectionLabel } from '@/components/shared/detail-list';
import { JsonView } from '@/components/shared/json-view';
import { CopyButton } from '@/components/shared/copy-button';
import { DateRangeFilter, USAGE_PRESETS } from '@/components/shared/DateRangeFilter';
import { downloadFromApi } from '@/components/shared/download';
import { useAuditLogs } from '@/hooks/api/security';
import { auditExportPath, type AuditActorType, type AuditLog, type AuditOutcome } from '@/hooks/api/security-api';
import { useUrlState } from '@/hooks/use-url-state';
import { useDebounce } from '@/hooks/use-debounce';
import { cn } from '@/lib/utils';
import { StateDiff } from '@/components/security/state-diff';
import { useRangeFromUrl } from '@/hooks/use-range-from-url';

const DEFAULTS = { search: '', actor: '', category: '', outcome: '', preset: 'last_30d', from: '', to: '', page: 1, pageSize: 50 };

const ACTORS: Record<AuditActorType, { label: string; icon: typeof User }> = {
  admin: { label: 'Admin', icon: User },
  api_key: { label: 'API key', icon: KeyRound },
  system: { label: 'System', icon: Bot },
  rule: { label: 'Rule', icon: Workflow },
  node_agent: { label: 'Node agent', icon: Server },
};

/** Event-type prefixes (the backend matches `category` as `category.*`). */
const CATEGORIES = [
  { value: 'auth', label: 'Sign-ins & sessions' },
  { value: 'api_key', label: 'API keys' },
  { value: 'rule', label: 'Rules' },
  { value: 'layout', label: 'Layouts' },
  { value: 'swap', label: 'Swaps' },
  { value: 'instance', label: 'Loads & unloads' },
  { value: 'deployment', label: 'Deployments' },
  { value: 'node', label: 'Nodes' },
  { value: 'ip_rule', label: 'IP rules' },
  { value: 'alert_channel', label: 'Alert channels' },
  { value: 'credential,vault', label: 'Vault & credentials' },
  { value: 'pki', label: 'Certificates' },
  { value: 'system', label: 'System jobs' },
];

function Actor({ entry }: { entry: AuditLog }) {
  const meta = ACTORS[entry.actorType];
  return (
    <span className="inline-flex items-center gap-1.5 text-xs">
      <meta.icon className="size-3.5 text-muted-foreground" />
      <span className="font-medium">{entry.actorName ?? meta.label}</span>
      {entry.actorName && entry.actorType !== 'admin' && <span className="text-[11px] text-muted-foreground">{meta.label.toLowerCase()}</span>}
    </span>
  );
}

export function AuditLogPage() {
  const { params, setParams } = useUrlState(DEFAULTS);
  const [search, setSearch] = useState(params.search);
  const debounced = useDebounce(search, 300);
  const [selected, setSelected] = useState<AuditLog | null>(null);
  const [exporting, setExporting] = useState(false);
  const range = useRangeFromUrl(params.preset, params.from, params.to);
  const urlSearch = params.search;
  useEffect(() => {
    if (debounced !== urlSearch) setParams({ search: debounced, page: 1 });
  }, [debounced, urlSearch, setParams]);

  const query = useMemo(
    () => ({ search: debounced, actorType: params.actor as AuditActorType | '', eventType: params.category || undefined, outcome: params.outcome as AuditOutcome | '', from: range.from, to: range.to, page: params.page, pageSize: params.pageSize }),
    [debounced, params.actor, params.category, params.outcome, range.from, range.to, params.page, params.pageSize],
  );
  const { data, isLoading, isError, refetch } = useAuditLogs(query);

  async function exportCsv() {
    setExporting(true);
    try {
      await downloadFromApi(auditExportPath(query), 'gpu-deck-audit.csv');
      toast.success('Audit log exported');
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Export failed');
    } finally {
      setExporting(false);
    }
  }

  if (isLoading && !data) return <ListPageSkeleton stats={0} />;
  const entries = data?.items ?? [];
  const filtered = !!debounced || !!params.actor || !!params.category || !!params.outcome;

  return (
    <PageContainer>
      <PageHeader
        title="Audit log"
        description="Every sign-in, key change, rule action and load or unload — who, what, when and from where."
        actions={
          <>
            <DateRangeFilter value={range} onChange={(v) => setParams({ preset: v.preset, from: v.preset === 'custom' ? v.from : '', to: v.preset === 'custom' ? v.to : '', page: 1 })} presets={USAGE_PRESETS} />
            <Button variant="outline" size="sm" className="rounded-sm" disabled={exporting} onClick={() => void exportCsv()}>
              <Download className="size-4" /> Export CSV
            </Button>
          </>
        }
      />
      {isError ? (
        <ErrorState title="Could not load the audit log" onRetry={() => void refetch()} />
      ) : (
        <Card className="rounded-md shadow-premium">
          <CardHeader>
            <div className="flex flex-wrap items-center gap-3">
              <SearchInput value={search} onChange={setSearch} placeholder="Search events, names, messages, IPs…" />
              <Select value={params.category || 'all'} onValueChange={(v) => setParams({ category: v === 'all' ? '' : String(v), page: 1 })}>
                <SelectTrigger className="w-48">
                  <SelectValue>{(v: unknown) => CATEGORIES.find((c) => c.value === v)?.label ?? 'All events'}</SelectValue>
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">All events</SelectItem>
                  {CATEGORIES.map((c) => (
                    <SelectItem key={c.value} value={c.value}>
                      {c.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Select value={params.actor || 'all'} onValueChange={(v) => setParams({ actor: v === 'all' ? '' : String(v), page: 1 })}>
                <SelectTrigger className="w-40">
                  <SelectValue>{(v: unknown) => (v && v !== 'all' ? ACTORS[v as AuditActorType].label : 'Any actor')}</SelectValue>
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">Any actor</SelectItem>
                  {(Object.keys(ACTORS) as AuditActorType[]).map((a) => (
                    <SelectItem key={a} value={a}>
                      {ACTORS[a].label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Select value={params.outcome || 'all'} onValueChange={(v) => setParams({ outcome: v === 'all' ? '' : String(v), page: 1 })}>
                <SelectTrigger className="w-36">
                  <SelectValue>{(v: unknown) => (v === 'success' ? 'Succeeded' : v === 'failure' ? 'Failed' : 'Any outcome')}</SelectValue>
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">Any outcome</SelectItem>
                  <SelectItem value="success">Succeeded</SelectItem>
                  <SelectItem value="failure">Failed</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </CardHeader>
          <CardContent>
            {entries.length === 0 ? (
              <EmptyState icon={History} title={filtered ? 'No entries match' : 'Nothing recorded yet'} description={filtered ? 'Try a wider date range or fewer filters.' : 'Actions appear here as soon as they happen.'} />
            ) : (
              <>
                <div className="overflow-x-auto">
                  <Table>
                    <TableHeader className="sticky top-0 z-10 bg-card">
                      <TableRow>
                        <TableHead>When</TableHead>
                        <TableHead>Event</TableHead>
                        <TableHead>Actor</TableHead>
                        <TableHead>Resource</TableHead>
                        <TableHead>Source IP</TableHead>
                        <TableHead className="text-right">Outcome</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {entries.map((e) => (
                        <TableRow key={e.id} className={cn('cursor-pointer', e.outcome === 'failure' && 'bg-error/[0.03]')} onClick={() => setSelected(e)}>
                          <TableCell className="whitespace-nowrap">
                            <span className="block font-mono text-[11px] tabular-nums">{new Date(e.createdAt).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23' })}</span>
                            <RelativeTime value={e.createdAt} className="text-[11px] text-muted-foreground" />
                          </TableCell>
                          <TableCell className="max-w-[340px]">
                            <span className="font-mono text-[11px] font-medium">{e.eventType}</span>
                            {e.message && <p className="truncate text-[11px] text-muted-foreground">{e.message}</p>}
                          </TableCell>
                          <TableCell>
                            <Actor entry={e} />
                          </TableCell>
                          <TableCell className="max-w-[220px] text-xs">
                            <span className="block truncate">{e.resourceName ?? '—'}</span>
                            {e.resourceType && <span className="text-[11px] text-muted-foreground">{e.resourceType.replace(/_/g, ' ')}</span>}
                          </TableCell>
                          <TableCell className="font-mono text-[11px] text-muted-foreground">{e.ipAddress ?? '—'}</TableCell>
                          <TableCell className="text-right">
                            {e.outcome === 'success' ? <CircleCheck className="ml-auto size-4 text-success" aria-label="succeeded" /> : <StatusBadge variant="error">failed</StatusBadge>}
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
                <TablePagination page={params.page} pageSize={params.pageSize} total={data?.total ?? 0} onPageChange={(p) => setParams({ page: p })} onPageSizeChange={(s) => setParams({ pageSize: s, page: 1 })} noun="entries" />
              </>
            )}
          </CardContent>
        </Card>
      )}

      <Sheet open={!!selected} onOpenChange={(o) => !o && setSelected(null)}>
        <SheetContent className="w-full overflow-y-auto sm:max-w-xl">
          {selected && (
            <>
              <SheetHeader className="border-b border-border">
                <div className="flex items-center gap-2 pr-8">
                  {selected.outcome === 'success' ? <CircleCheck className="size-4 text-success" /> : <CircleX className="size-4 text-error" />}
                  <SheetTitle className="font-mono text-sm">{selected.eventType}</SheetTitle>
                </div>
                <SheetDescription className="text-xs">{selected.message ?? `${ACTORS[selected.actorType].label} action on ${selected.resourceType?.replace(/_/g, ' ') ?? 'the system'}`}</SheetDescription>
              </SheetHeader>
              <div className="space-y-5 p-4">
                <DetailList
                  items={[
                    { label: 'When', value: new Date(selected.createdAt).toLocaleString() },
                    { label: 'Actor', value: <Actor entry={selected} /> },
                    { label: 'Resource', value: selected.resourceName ? `${selected.resourceName}${selected.resourceType ? ` (${selected.resourceType.replace(/_/g, ' ')})` : ''}` : null },
                    { label: 'Resource id', value: selected.resourceId, mono: true },
                    { label: 'Source IP', value: selected.ipAddress, mono: true },
                    {
                      label: 'Request id',
                      value: selected.requestId ? (
                        <span className="inline-flex items-center gap-1">
                          {selected.requestId}
                          <CopyButton value={selected.requestId} successText="Request id copied" />
                        </span>
                      ) : null,
                      mono: true,
                    },
                    { label: 'User agent', value: selected.userAgent, wide: true },
                  ]}
                />
                <div className="space-y-2">
                  <SectionLabel>Change</SectionLabel>
                  <StateDiff before={selected.beforeState} after={selected.afterState} />
                </div>
                {Object.keys(selected.metadata).length > 0 && (
                  <div className="space-y-2">
                    <SectionLabel>Metadata</SectionLabel>
                    <JsonView value={selected.metadata} maxHeight={200} />
                  </div>
                )}
              </div>
            </>
          )}
        </SheetContent>
      </Sheet>
    </PageContainer>
  );
}
