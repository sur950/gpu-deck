'use client';

import { useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { CalendarClock, Copy, Eye, FlaskConical, KeyRound, Loader2, Lock, MoreVertical, PenLine, Plus, Replace, ShieldCheck, Trash2, TriangleAlert, Vault } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader } from '@/components/ui/card';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from '@/components/ui/dropdown-menu';
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
import { CredentialDialog } from '@/components/vault/credential-dialog';
import { DeleteCredentialFlow, RevealFlow, TestResultPanel } from '@/components/vault/credential-actions';
import { KindGlyph } from '@/components/vault/kind-icon';
import { useCredentialKinds, useCredentials, useTestCredential, useVaultStats } from '@/hooks/use-vault';
import { useUrlState } from '@/hooks/use-url-state';
import { useDebounce } from '@/hooks/use-debounce';
import { cn } from '@/lib/utils';
import { CATEGORY_LABELS, CATEGORY_ORDER, usageSummary, type Credential, type CredentialCategory, type CredentialKind, type CredentialTestResult } from '@/types/vault';

const DEFAULTS = { search: '', category: '', kind: '', status: '', add: '', page: 1, pageSize: 25 };
const DAY = 86_400_000;
const QUICK_START = ['aws', 'huggingface', 'slack', 'ssh_key'];

export function ExpiryNote({ value }: { value: string | null | undefined }) {
  const [now] = useState(() => Date.now());
  if (!value) return null;
  const left = Date.parse(value) - now;
  const days = Math.ceil(left / DAY);
  return (
    <span className={cn('inline-flex items-center gap-1 text-[11px]', left <= 0 ? 'text-error-dark dark:text-error-light' : days <= 14 ? 'text-warning-dark dark:text-warning-light' : 'text-muted-foreground')} title={new Date(value).toLocaleString()}>
      {left > 0 && days <= 14 && <TriangleAlert className="size-3" />}
      {left <= 0 ? 'expired' : days <= 1 ? 'expires in < 1 day' : `expires in ${days} days`}
    </span>
  );
}

/** "2 clusters · 1 alert channel", or when it was last used. */
export function UsageCell({ c }: { c: Credential }) {
  const summary = usageSummary(c.used_by);
  return (
    <div className="space-y-0.5">
      {summary ? <span className="text-xs font-medium">{summary}</span> : <span className="text-xs text-muted-foreground">Not referenced</span>}
      {(c.default_for ?? []).map((d) => (
        <span key={d} className="block text-[11px] text-info-dark dark:text-info-light">
          Default · {d.replace(/ \(default.*\)$/, '')}
        </span>
      ))}
    </div>
  );
}

export function CredentialsPage() {
  const router = useRouter();
  const { params, setParams } = useUrlState(DEFAULTS);
  const [search, setSearch] = useState(params.search);
  const debounced = useDebounce(search, 300);
  const urlSearch = params.search;
  useEffect(() => {
    if (debounced !== urlSearch) setParams({ search: debounced, page: 1 });
  }, [debounced, urlSearch, setParams]);

  const { kinds, byId } = useCredentialKinds();
  const provider = params.kind || (params.category ? kinds.filter((k) => k.category === params.category).map((k) => k.id).join(',') : '');
  const query = useMemo(
    () => ({ search: debounced, provider, status: params.status as Credential['status'] | '', page: params.page, pageSize: params.pageSize }),
    [debounced, provider, params.status, params.page, params.pageSize],
  );
  const { data, isLoading, isError, refetch } = useCredentials(query);
  const stats = useVaultStats();
  const test = useTestCredential();

  const [dialog, setDialog] = useState<{ open: boolean; credential: Credential | null; kind: string | null; rotate: boolean }>({ open: false, credential: null, kind: null, rotate: false });
  const [revealing, setRevealing] = useState<Credential | null>(null);
  const [deleting, setDeleting] = useState<Credential | null>(null);
  const [tested, setTested] = useState<{ c: Credential; result: CredentialTestResult } | null>(null);

  // ?add=<kind> (links from pickers / docs) opens the dialog once.
  const addKind = params.add;
  useEffect(() => {
    if (!addKind) return;
    queueMicrotask(() => setDialog({ open: true, credential: null, kind: addKind === 'any' ? null : addKind, rotate: false }));
    setParams({ add: '' });
  }, [addKind, setParams]);

  if (isLoading && !data) return <ListPageSkeleton />;
  const items = data?.items ?? [];
  const total = data?.meta.total ?? 0;
  const s = stats.data;
  const filtered = !!debounced || !!params.category || !!params.kind || !!params.status;
  const openNew = (kind: string | null = null) => setDialog({ open: true, credential: null, kind, rotate: false });
  const categoryCounts = (cat: CredentialCategory) => kinds.filter((k) => k.category === cat).reduce((n, k) => n + (s?.by_kind?.[k.id] ?? 0), 0);

  return (
    <PageContainer>
      <PageHeader
        title="Credentials"
        description="Secrets GPU Deck uses to reach cloud accounts, model hubs, alert channels and machines. Encrypted at rest; only an audited reveal shows them again."
        actions={
          <Button size="sm" className="rounded-sm" onClick={() => openNew()}>
            <Plus className="size-4" /> Add credential
          </Button>
        }
      />

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard label="Credentials" value={s?.total ?? '—'} icon={Vault} footnote={s ? `${s.active} active` : 'Typed, encrypted with VAULT_KEYS'} />
        <StatCard label="Cloud accounts" value={s ? categoryCounts('cloud') : '—'} icon={ShieldCheck} footnote="AWS, Azure, Google Cloud, DigitalOcean" />
        <StatCard label="Expiring soon" value={s?.expiring_soon ?? '—'} icon={CalendarClock} footnote="Within 7 days" />
        <StatCard label="Expired" value={s?.expired ?? '—'} icon={TriangleAlert} footnote="Rotate or delete" />
      </div>

      {isError ? (
        <ErrorState title="Could not load credentials" onRetry={() => void refetch()} />
      ) : total === 0 && !filtered ? (
        <EmptyVault kinds={kinds} onAdd={openNew} />
      ) : (
        <Card className="rounded-md shadow-premium">
          <CardHeader>
            <div className="flex flex-wrap items-center gap-3">
              <SearchInput value={search} onChange={setSearch} placeholder="Search name, description or type…" />
              <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label="Category">
                {[{ id: '', label: 'All' }, ...CATEGORY_ORDER.map((c) => ({ id: c, label: CATEGORY_LABELS[c] }))].map((c) => {
                  const on = params.category === c.id && !params.kind;
                  return (
                    <button
                      key={c.id || 'all'}
                      type="button"
                      aria-pressed={on}
                      onClick={() => setParams({ category: c.id, kind: '', page: 1 })}
                      className={cn('h-8 rounded-full border px-3 text-xs outline-none focus-visible:ring-2 focus-visible:ring-ring/30', on ? 'border-foreground/30 bg-foreground/[0.06] text-foreground' : 'border-border text-muted-foreground hover:text-foreground')}
                    >
                      {c.label}
                    </button>
                  );
                })}
              </div>
              <Select value={params.kind || 'all'} onValueChange={(v) => setParams({ kind: v === 'all' ? '' : String(v), category: '', page: 1 })}>
                <SelectTrigger className="w-48">
                  <SelectValue>{(v: unknown) => (v && v !== 'all' ? (byId[String(v)]?.label ?? String(v)) : 'Every type')}</SelectValue>
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">Every type</SelectItem>
                  {kinds.map((k) => (
                    <SelectItem key={k.id} value={k.id}>
                      {k.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Select value={params.status || 'all'} onValueChange={(v) => setParams({ status: v === 'all' ? '' : String(v), page: 1 })}>
                <SelectTrigger className="w-36">
                  <SelectValue>{(v: unknown) => (v && v !== 'all' ? String(v).charAt(0).toUpperCase() + String(v).slice(1) : 'All statuses')}</SelectValue>
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">All statuses</SelectItem>
                  {['active', 'expired'].map((st) => (
                    <SelectItem key={st} value={st}>
                      {st.charAt(0).toUpperCase() + st.slice(1)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </CardHeader>
          <CardContent>
            {items.length === 0 ? (
              <EmptyState icon={KeyRound} title="No credentials match" description="Try a different search, type or status." />
            ) : (
              <>
                <div className="overflow-x-auto">
                  <Table>
                    <TableHeader className="sticky top-0 z-10 bg-card">
                      <TableRow>
                        <TableHead>Name</TableHead>
                        <TableHead>Type</TableHead>
                        <TableHead>Secret</TableHead>
                        <TableHead>Used by</TableHead>
                        <TableHead>Status</TableHead>
                        <TableHead>Last used</TableHead>
                        <TableHead className="w-10" />
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {items.map((c) => {
                        const kind = c.kind ? byId[c.kind] : undefined;
                        return (
                          <TableRow key={c.id} className={cn('cursor-pointer', c.status !== 'active' && 'opacity-70')} onClick={() => router.push(`/credentials/${c.id}`)}>
                            <TableCell className="max-w-[280px]">
                              <div className="flex items-center gap-3">
                                <KindGlyph kind={c.kind ?? c.provider} category={c.system ? 'system' : (kind?.category ?? 'legacy')} />
                                <div className="min-w-0">
                                  <Link href={`/credentials/${c.id}`} onClick={(e) => e.stopPropagation()} className="block truncate text-[13px] font-semibold hover:underline">
                                    {c.name}
                                  </Link>
                                  {c.description && <p className="truncate text-[11px] text-muted-foreground">{c.description}</p>}
                                </div>
                              </div>
                            </TableCell>
                            <TableCell className="text-xs">
                              {kind ? kind.label : <span className="text-muted-foreground">Untyped ({c.type.replace(/_/g, ' ')})</span>}
                            </TableCell>
                            <TableCell className="max-w-[260px]">
                              <span className="flex items-center gap-1.5 font-mono text-xs">
                                <Lock className="size-3 shrink-0 text-muted-foreground" />
                                <span className="truncate">{c.masked_value ?? '••••'}</span>
                              </span>
                              {c.summary && <p className="truncate text-[11px] text-muted-foreground">{c.summary}</p>}
                            </TableCell>
                            <TableCell>
                              <UsageCell c={c} />
                            </TableCell>
                            <TableCell>
                              <StatusBadge variant={toBadgeVariant(c.status)}>{c.status}</StatusBadge>
                              <div>
                                <ExpiryNote value={c.expires_at} />
                              </div>
                            </TableCell>
                            <TableCell className="text-xs">{c.last_used_at ? <RelativeTime value={c.last_used_at} /> : <span className="text-muted-foreground">never</span>}</TableCell>
                            <TableCell onClick={(e) => e.stopPropagation()}>
                              <RowMenu
                                c={c}
                                kind={kind}
                                testing={test.isPending && test.variables === c.id}
                                onEdit={() => setDialog({ open: true, credential: c, kind: null, rotate: false })}
                                onRotate={() => setDialog({ open: true, credential: c, kind: null, rotate: true })}
                                onTest={() => test.mutate(c.id, { onSuccess: (result) => setTested({ c, result }) })}
                                onReveal={() => setRevealing(c)}
                                onDelete={() => setDeleting(c)}
                              />
                            </TableCell>
                          </TableRow>
                        );
                      })}
                    </TableBody>
                  </Table>
                </div>
                <TablePagination page={params.page} pageSize={params.pageSize} total={total} onPageChange={(p) => setParams({ page: p })} onPageSizeChange={(sz) => setParams({ pageSize: sz, page: 1 })} noun="credentials" />
              </>
            )}
          </CardContent>
        </Card>
      )}

      <CredentialDialog
        open={dialog.open}
        onOpenChange={(o) => setDialog((d) => ({ ...d, open: o }))}
        credential={dialog.credential}
        initialKind={dialog.kind}
        rotate={dialog.rotate}
      />
      <RevealFlow credential={revealing} open={!!revealing} onOpenChange={(o) => !o && setRevealing(null)} />
      <DeleteCredentialFlow credential={deleting} onOpenChange={(o) => !o && setDeleting(null)} />
      <Dialog open={!!tested} onOpenChange={(o) => !o && setTested(null)}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <FlaskConical className="size-4" /> {tested?.result.ok ? 'Looks good' : 'Problems found'} — {tested?.c.name}
            </DialogTitle>
            <DialogDescription>Checked inside the control plane: decryption, status and the type&apos;s schema. Nothing left the server.</DialogDescription>
          </DialogHeader>
          {tested && <TestResultPanel result={tested.result} credentialId={tested.c.id} />}
          <DialogFooter>
            <Button size="sm" variant="outline" onClick={() => setTested(null)}>
              Close
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </PageContainer>
  );
}

function RowMenu({ c, kind, testing, onEdit, onRotate, onTest, onReveal, onDelete }: { c: Credential; kind?: CredentialKind; testing: boolean; onEdit: () => void; onRotate: () => void; onTest: () => void; onReveal: () => void; onDelete: () => void }) {
  const publicKey = c.kind === 'ssh_key' ? String((c.fields as Record<string, unknown> | undefined)?.public_key ?? '') : '';
  return (
    <DropdownMenu>
      <DropdownMenuTrigger render={<Button variant="ghost" size="icon-sm" aria-label={`Actions for ${c.name}`} />}>
        {testing ? <Loader2 className="size-4 animate-spin" /> : <MoreVertical className="size-4" />}
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-48">
        <DropdownMenuItem onClick={onEdit}>
          <PenLine className="size-4" /> Edit
        </DropdownMenuItem>
        {kind && (kind.secret_fields?.length ?? 0) > 0 && (
          <DropdownMenuItem onClick={onRotate}>
            <Replace className="size-4" /> Replace secret
          </DropdownMenuItem>
        )}
        <DropdownMenuItem onClick={onTest}>
          <FlaskConical className="size-4" /> Test
        </DropdownMenuItem>
        {publicKey ? (
          <DropdownMenuItem
            onClick={() => {
              void navigator.clipboard.writeText(publicKey).then(
                () => toast.success('Public key copied'),
                () => toast.error('Clipboard is not available'),
              );
            }}
          >
            <Copy className="size-4" /> Copy public key
          </DropdownMenuItem>
        ) : (
          <DropdownMenuItem onClick={onReveal}>
            <Eye className="size-4" /> Reveal…
          </DropdownMenuItem>
        )}
        <DropdownMenuSeparator />
        <DropdownMenuItem className="text-destructive" onClick={onDelete}>
          <Trash2 className="size-4" /> Delete
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

/** First-run state: what credentials are for, with one-click starts for the common types. */
function EmptyVault({ kinds, onAdd }: { kinds: CredentialKind[]; onAdd: (kind: string | null) => void }) {
  const quick = QUICK_START.map((id) => kinds.find((k) => k.id === id)).filter((k): k is CredentialKind => !!k);
  return (
    <Card className="rounded-md shadow-premium">
      <CardContent className="grid gap-8 py-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]">
        <div className="space-y-4">
          <div className="flex size-11 items-center justify-center rounded-xl border border-border bg-muted/60">
            <Vault className="size-5 text-muted-foreground" />
          </div>
          <div className="space-y-1.5">
            <h2 className="text-lg font-semibold tracking-tight">The vault is empty</h2>
            <p className="text-[13px] leading-relaxed text-muted-foreground">
              Credentials are the secrets GPU Deck needs to act for you. Store each once, then pick it wherever it is needed — forms only offer the types that fit.
            </p>
          </div>
          <ul className="space-y-2 text-[12px] text-muted-foreground">
            <li className="flex gap-2"><ShieldCheck className="mt-0.5 size-3.5 shrink-0 text-success" /> <span><b className="font-medium text-foreground">Cloud clusters</b> use an AWS, Azure, Google Cloud or DigitalOcean credential to browse GPU types and launch VMs.</span></li>
            <li className="flex gap-2"><ShieldCheck className="mt-0.5 size-3.5 shrink-0 text-success" /> <span><b className="font-medium text-foreground">The catalog</b> uses a Hugging Face or ModelScope token for gated repos and node downloads.</span></li>
            <li className="flex gap-2"><ShieldCheck className="mt-0.5 size-3.5 shrink-0 text-success" /> <span><b className="font-medium text-foreground">Alert channels</b> use a Slack webhook, an SMTP login or a signed-webhook secret.</span></li>
            <li className="flex gap-2"><ShieldCheck className="mt-0.5 size-3.5 shrink-0 text-success" /> <span><b className="font-medium text-foreground">SSH keys</b> (public half only) go onto SSH GPU instances and cloud VMs.</span></li>
          </ul>
        </div>
        <div className="space-y-3">
          <p className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">Start with</p>
          <div className="grid gap-2 sm:grid-cols-2">
            {quick.map((k) => (
              <button key={k.id} type="button" onClick={() => onAdd(k.id)} className="flex items-start gap-3 rounded-lg border border-border p-3 text-left transition-colors outline-none hover:border-foreground/30 hover:bg-muted/30 focus-visible:ring-2 focus-visible:ring-ring/30">
                <KindGlyph kind={k.id} category={k.category} size="md" />
                <span className="min-w-0">
                  <span className="block text-[13px] font-medium">{k.label}</span>
                  <span className="line-clamp-2 block text-[11px] leading-snug text-muted-foreground">{k.used_for?.join(' · ')}</span>
                </span>
              </button>
            ))}
          </div>
          <Button size="sm" variant="outline" className="rounded-sm" onClick={() => onAdd(null)}>
            <Plus className="size-4" /> Every type…
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
