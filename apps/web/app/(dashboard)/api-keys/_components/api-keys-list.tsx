'use client';

import { useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { Ban, CalendarClock, KeyRound, MoreVertical, Pause, Play, Plus, RotateCw, ShieldCheck, Trash2, TriangleAlert } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader } from '@/components/ui/card';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from '@/components/ui/dropdown-menu';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { PageContainer } from '@/components/shared/PageContainer';
import { PageHeader } from '@/components/shared/PageHeader';
import { ListPageSkeleton } from '@/components/shared/ListPageSkeleton';
import { ErrorState } from '@/components/shared/ErrorState';
import { EmptyState } from '@/components/shared/EmptyState';
import { StatusBadge, toBadgeVariant } from '@/components/shared/StatusBadge';
import { StatCard } from '@/components/shared/stat-card';
import { SearchInput } from '@/components/shared/search-input';
import { TablePagination } from '@/components/shared/table-pagination';
import { RelativeTime } from '@/components/shared/relative-time';
import { ConfirmationDialog } from '@/components/shared/ConfirmationDialog';
import { DeleteConfirmationDialog } from '@/components/shared/DeleteConfirmationDialog';
import { useActivateApiKey, useApiKeys, useApiKeyStats, useDeleteApiKey, useRevokeApiKey, useRotateApiKey, useSuspendApiKey, useUpdateApiKey } from '@/hooks/api/security';
import type { ApiKey, ApiKeyCreated, ApiKeyStatus } from '@/hooks/api/security-api';
import { useUrlState } from '@/hooks/use-url-state';
import { useDebounce } from '@/hooks/use-debounce';
import { cn } from '@/lib/utils';
import { KeyReveal } from '@/components/security/key-reveal';
import { useNow } from '@/components/automation/use-now';

const DEFAULTS = { search: '', status: '', page: 1, pageSize: 25 };
const DAY = 86_400_000;

export function ExpiryCell({ value, now }: { value: string | null; now: number }) {
  if (!value) return <span className="text-muted-foreground">never</span>;
  const left = Date.parse(value) - now;
  const days = Math.ceil(left / DAY);
  return (
    <span className={cn('inline-flex items-center gap-1', left <= 0 ? 'text-muted-foreground' : days <= 14 ? 'text-warning-dark dark:text-warning-light' : '')} title={new Date(value).toLocaleString()}>
      {left > 0 && days <= 14 && <TriangleAlert className="size-3" />}
      {left <= 0 ? `expired ${new Date(value).toLocaleDateString()}` : days <= 1 ? 'in < 1 day' : `in ${days} days`}
    </span>
  );
}

/** Rotate + reveal flow shared by the list and the detail page. */
export function RevealDialog({ created, onClose }: { created: ApiKeyCreated | null; onClose: () => void }) {
  const [ok, setOk] = useState(false);
  return (
    <Dialog open={!!created} onOpenChange={(o) => !o && ok && onClose()}>
      <DialogContent className="sm:max-w-xl" showCloseButton={false}>
        <DialogHeader>
          <DialogTitle>New secret for “{created?.name}”</DialogTitle>
          <DialogDescription>The previous secret stopped working the moment this one was issued.</DialogDescription>
        </DialogHeader>
        {created && <KeyReveal secret={created.key} name={created.name} onConfirmedChange={setOk} />}
        <DialogFooter>
          <Button size="sm" disabled={!ok} onClick={onClose}>
            Done
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function ExpiryDialog({ apiKey, onClose }: { apiKey: ApiKey | null; onClose: () => void }) {
  const update = useUpdateApiKey();
  const [date, setDate] = useState(apiKey?.expiresAt ? apiKey.expiresAt.slice(0, 10) : '');
  const [today] = useState(() => new Date().toISOString().slice(0, 10));
  return (
    <Dialog open={!!apiKey} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-sm">
        <DialogHeader>
          <DialogTitle>Expiry for “{apiKey?.name}”</DialogTitle>
          <DialogDescription>The key stops working at 00:00 UTC on this date. Leave empty for no expiry.</DialogDescription>
        </DialogHeader>
        <Input type="date" min={today} value={date} onChange={(e) => setDate(e.target.value)} />
        <DialogFooter>
          <Button variant="outline" size="sm" onClick={() => setDate('')}>
            No expiry
          </Button>
          <Button size="sm" disabled={update.isPending || !apiKey} onClick={() => apiKey && update.mutate({ id: apiKey.id, body: { expires_at: date ? new Date(`${date}T00:00:00Z`).toISOString() : null } }, { onSuccess: onClose })}>
            Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function ApiKeysList() {
  const router = useRouter();
  const now = useNow(60_000);
  const { params, setParams } = useUrlState(DEFAULTS);
  const [search, setSearch] = useState(params.search);
  const debounced = useDebounce(search, 300);
  const urlSearch = params.search;
  useEffect(() => {
    if (debounced !== urlSearch) setParams({ search: debounced, page: 1 });
  }, [debounced, urlSearch, setParams]);

  const query = useMemo(() => ({ search: debounced, status: params.status as ApiKeyStatus | '', page: params.page, pageSize: params.pageSize }), [debounced, params.status, params.page, params.pageSize]);
  const { data, isLoading, isError, refetch } = useApiKeys(query);
  const stats = useApiKeyStats();
  const revoke = useRevokeApiKey();
  const suspend = useSuspendApiKey();
  const activate = useActivateApiKey();
  const rotate = useRotateApiKey();
  const del = useDeleteApiKey();
  const [revoking, setRevoking] = useState<ApiKey | null>(null);
  const [rotating, setRotating] = useState<ApiKey | null>(null);
  const [deleting, setDeleting] = useState<ApiKey | null>(null);
  const [expiry, setExpiry] = useState<ApiKey | null>(null);
  const [revealed, setRevealed] = useState<ApiKeyCreated | null>(null);

  if (isLoading && !data) return <ListPageSkeleton />;
  const keys = data?.items ?? [];
  const s = stats.data;
  const filtered = !!debounced || !!params.status;

  return (
    <PageContainer>
      <PageHeader
        title="API keys"
        description="Keys your AI gateway or any other client uses against the API layer. The secret is shown once; revoking takes effect immediately."
        actions={
          <Button size="sm" className="rounded-sm" onClick={() => router.push('/api-keys/new')}>
            <Plus className="size-4" /> New API key
          </Button>
        }
      />
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard label="Active" value={s?.active ?? '—'} icon={ShieldCheck} footnote={`${s?.total ?? 0} keys in total`} />
        <StatCard label="Suspended" value={s?.suspended ?? '—'} icon={Pause} footnote="Paused, can be re-activated" />
        <StatCard label="Expired" value={s?.expired ?? '—'} icon={CalendarClock} footnote="Extend the expiry to re-use" />
        <StatCard label="Revoked" value={s?.revoked ?? '—'} icon={Ban} footnote="Permanently disabled" />
      </div>
      {isError ? (
        <ErrorState title="Could not load API keys" onRetry={() => void refetch()} />
      ) : (
        <Card className="rounded-md shadow-premium">
          <CardHeader>
            <div className="flex flex-wrap items-center gap-3">
              <SearchInput value={search} onChange={setSearch} placeholder="Search by name or prefix…" />
              <Select value={params.status || 'all'} onValueChange={(v) => setParams({ status: v === 'all' ? '' : String(v), page: 1 })}>
                <SelectTrigger className="w-40">
                  <SelectValue>{(v: unknown) => (v && v !== 'all' ? String(v).charAt(0).toUpperCase() + String(v).slice(1) : 'All statuses')}</SelectValue>
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">All statuses</SelectItem>
                  {['active', 'suspended', 'expired', 'revoked'].map((st) => (
                    <SelectItem key={st} value={st}>
                      {st.charAt(0).toUpperCase() + st.slice(1)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </CardHeader>
          <CardContent>
            {keys.length === 0 ? (
              <EmptyState
                icon={KeyRound}
                title={filtered ? 'No keys match' : 'No API keys yet'}
                description={filtered ? 'Try a different search or status.' : 'Create a key for your AI gateway or any OpenAI, Anthropic or Ollama client.'}
                action={!filtered && <Button size="sm" onClick={() => router.push('/api-keys/new')}><Plus className="size-4" /> New API key</Button>}
              />
            ) : (
              <>
                <div className="overflow-x-auto">
                  <Table>
                    <TableHeader className="sticky top-0 z-10 bg-card">
                      <TableRow>
                        <TableHead>Name</TableHead>
                        <TableHead>Key</TableHead>
                        <TableHead>Status</TableHead>
                        <TableHead className="text-right">Requests</TableHead>
                        <TableHead>Last used</TableHead>
                        <TableHead>Expires</TableHead>
                        <TableHead>Created</TableHead>
                        <TableHead className="w-10" />
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {keys.map((k) => (
                        <TableRow key={k.id} className={cn('cursor-pointer', (k.status === 'revoked' || k.status === 'expired') && 'opacity-60')} onClick={() => router.push(`/api-keys/${k.id}`)}>
                          <TableCell className="max-w-[300px]">
                            <Link href={`/api-keys/${k.id}`} onClick={(e) => e.stopPropagation()} className="text-[13px] font-semibold hover:underline">
                              {k.name}
                            </Link>
                            {k.description && <p className="truncate text-[11px] text-muted-foreground">{k.description}</p>}
                          </TableCell>
                          <TableCell className="font-mono text-xs">
                            {k.keyPrefix}
                            <span className="text-muted-foreground">••••••••</span>
                          </TableCell>
                          <TableCell>
                            <StatusBadge variant={toBadgeVariant(k.status)}>{k.status}</StatusBadge>
                          </TableCell>
                          <TableCell className="text-right text-xs tabular-nums">{k.totalRequests.toLocaleString()}</TableCell>
                          <TableCell className="text-xs">{k.lastUsedAt ? <RelativeTime value={k.lastUsedAt} /> : <span className="text-muted-foreground">never</span>}</TableCell>
                          <TableCell className="text-xs">
                            <ExpiryCell value={k.expiresAt} now={now} />
                          </TableCell>
                          <TableCell className="text-xs text-muted-foreground">{new Date(k.createdAt).toLocaleDateString()}</TableCell>
                          <TableCell onClick={(e) => e.stopPropagation()}>
                            <DropdownMenu>
                              <DropdownMenuTrigger render={<Button variant="ghost" size="icon-sm" aria-label={`Actions for ${k.name}`} />}>
                                <MoreVertical className="size-4" />
                              </DropdownMenuTrigger>
                              <DropdownMenuContent align="end" className="w-48">
                                <DropdownMenuItem disabled={k.status === 'revoked'} onClick={() => setRotating(k)}>
                                  <RotateCw className="size-4" /> Rotate secret
                                </DropdownMenuItem>
                                <DropdownMenuItem disabled={k.status === 'revoked'} onClick={() => setExpiry(k)}>
                                  <CalendarClock className="size-4" /> Change expiry
                                </DropdownMenuItem>
                                {k.status === 'active' ? (
                                  <DropdownMenuItem onClick={() => suspend.mutate(k)}>
                                    <Pause className="size-4" /> Suspend
                                  </DropdownMenuItem>
                                ) : k.status === 'suspended' ? (
                                  <DropdownMenuItem onClick={() => activate.mutate(k)}>
                                    <Play className="size-4" /> Activate
                                  </DropdownMenuItem>
                                ) : null}
                                <DropdownMenuSeparator />
                                {k.status !== 'revoked' ? (
                                  <DropdownMenuItem className="text-destructive" onClick={() => setRevoking(k)}>
                                    <Ban className="size-4" /> Revoke
                                  </DropdownMenuItem>
                                ) : (
                                  <DropdownMenuItem className="text-destructive" onClick={() => setDeleting(k)}>
                                    <Trash2 className="size-4" /> Delete
                                  </DropdownMenuItem>
                                )}
                              </DropdownMenuContent>
                            </DropdownMenu>
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
                <TablePagination page={params.page} pageSize={params.pageSize} total={data?.total ?? 0} onPageChange={(p) => setParams({ page: p })} onPageSizeChange={(sz) => setParams({ pageSize: sz, page: 1 })} noun="keys" />
              </>
            )}
          </CardContent>
        </Card>
      )}

      <ConfirmationDialog open={!!revoking} onOpenChange={(o) => !o && setRevoking(null)} title={`Revoke “${revoking?.name ?? ''}”?`} description="Requests with this key fail immediately (the API layer cache is invalidated). Revoked keys cannot be re-activated." confirmText="Revoke key" variant="destructive" onConfirm={() => revoking && revoke.mutate(revoking)} isLoading={revoke.isPending} />
      <ConfirmationDialog open={!!rotating} onOpenChange={(o) => !o && setRotating(null)} title={`Rotate “${rotating?.name ?? ''}”?`} description="A new secret is issued and the current one stops working at once. Update every client that uses it." confirmText="Rotate now" onConfirm={() => rotating && rotate.mutate(rotating.id, { onSuccess: setRevealed })} isLoading={rotate.isPending} />
      <DeleteConfirmationDialog open={!!deleting} onOpenChange={(o) => !o && setDeleting(null)} itemName={deleting?.name ?? ''} resourceType="API key" warningMessage="Usage history keeps the key name." isDeleting={del.isPending} onConfirm={() => { if (deleting) del.mutate(deleting.id, { onSuccess: () => setDeleting(null) }); }} />
      {expiry && <ExpiryDialog key={expiry.id} apiKey={expiry} onClose={() => setExpiry(null)} />}
      <RevealDialog created={revealed} onClose={() => setRevealed(null)} />
    </PageContainer>
  );
}
