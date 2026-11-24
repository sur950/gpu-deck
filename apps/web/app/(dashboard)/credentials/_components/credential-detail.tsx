'use client';

import { useState } from 'react';
import Link from 'next/link';
import { useParams, useRouter } from 'next/navigation';
import { Copy, ExternalLink, Eye, FlaskConical, History, Link2, Loader2, Lock, PenLine, Replace, Trash2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { PageContainer } from '@/components/shared/PageContainer';
import { ErrorState } from '@/components/shared/ErrorState';
import { ListPageSkeleton } from '@/components/shared/ListPageSkeleton';
import { StatusBadge, toBadgeVariant } from '@/components/shared/StatusBadge';
import { DetailList, SectionLabel } from '@/components/shared/detail-list';
import { RelativeTime } from '@/components/shared/relative-time';
import { CopyButton } from '@/components/shared/copy-button';
import { CredentialDialog } from '@/components/vault/credential-dialog';
import { DeleteCredentialFlow, RevealFlow, TestResultPanel } from '@/components/vault/credential-actions';
import { KindGlyph } from '@/components/vault/kind-icon';
import { useCredential, useCredentialKinds, useCredentialUsage, useTestCredential } from '@/hooks/use-vault';
import { ExpiryNote, UsageCell } from '@/app/(dashboard)/credentials/_components/credentials-page';
import { REFERENCE_LABELS, referenceHref, type CredentialTestResult } from '@/types/vault';

const PURPOSE_LABELS: Record<string, string> = { reveal: 'Revealed in the UI', test: 'Tested', internal: 'Read internally' };

function purposeLabel(purpose: string | null | undefined): string {
  if (!purpose) return '—';
  if (PURPOSE_LABELS[purpose]) return PURPOSE_LABELS[purpose];
  const cloud = /^cloud:(\w+):(\w+)$/.exec(purpose);
  if (cloud) return `Cloud ${cloud[1]} · ${cloud[2]}`;
  return purpose.replace(/[_:]/g, ' ');
}

export function CredentialDetail() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const { data: c, isLoading, isError, refetch } = useCredential(id);
  const usage = useCredentialUsage(id);
  const { byId } = useCredentialKinds();
  const test = useTestCredential();
  const [result, setResult] = useState<CredentialTestResult | null>(null);
  const [edit, setEdit] = useState<'edit' | 'rotate' | null>(null);
  const [revealing, setRevealing] = useState(false);
  const [deleting, setDeleting] = useState(false);

  if (isLoading) return <ListPageSkeleton stats={0} />;
  if (isError || !c) {
    return (
      <PageContainer>
        <ErrorState title="Credential not found" description="It may have been deleted." onRetry={() => void refetch()} />
      </PageContainer>
    );
  }

  const kind = c.kind ? byId[c.kind] : undefined;
  const fields = (c.fields ?? {}) as Record<string, unknown>;
  const publicKey = c.kind === 'ssh_key' ? String(fields.public_key ?? '') : '';
  const kindFieldLabels = new Map((kind?.fields ?? []).map((f) => [f.name, f.label]));
  const derivedLabels: Record<string, string> = { project_id: 'Project', client_email: 'Service account', private_key_id: 'Key id', key_type: 'Key type', fingerprint: 'Fingerprint', comment: 'Comment', current_context: 'Context', server: 'API server' };
  const publicItems = Object.entries(fields)
    .filter(([k]) => k !== 'public_key')
    .map(([k, v]) => ({ label: kindFieldLabels.get(k) ?? derivedLabels[k] ?? k, value: typeof v === 'boolean' ? (v ? 'yes' : 'no') : String(v), mono: typeof v !== 'boolean' }));
  const secretFields = kind ? kind.fields.filter((f) => f.secret) : [{ name: 'value', label: 'Value' }];

  return (
    <PageContainer>
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-start gap-4">
          <KindGlyph kind={c.kind ?? c.provider} category={c.system ? 'system' : (kind?.category ?? 'legacy')} size="lg" />
          <div>
            <h1 className="text-2xl font-semibold tracking-tight sm:text-[28px]">{c.name}</h1>
            <p className="text-muted-foreground">{c.description ?? kind?.description ?? 'Untyped secret stored before credentials had types.'}</p>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <StatusBadge variant={toBadgeVariant(c.status)}>{c.status}</StatusBadge>
          {!c.system && (
            <>
              <Button variant="outline" size="sm" className="rounded-sm" disabled={test.isPending} onClick={() => test.mutate(c.id, { onSuccess: setResult })}>
                {test.isPending ? <Loader2 className="size-4 animate-spin" /> : <FlaskConical className="size-4" />} Test
              </Button>
              {publicKey ? (
                <CopyButton value={publicKey} label="Copy public key" successText="Public key copied" size="sm" variant="outline" className="rounded-sm" />
              ) : (
                <Button variant="outline" size="sm" className="rounded-sm" onClick={() => setRevealing(true)}>
                  <Eye className="size-4" /> Reveal
                </Button>
              )}
              <Button variant="outline" size="sm" className="rounded-sm" onClick={() => setEdit('edit')}>
                <PenLine className="size-4" /> Edit
              </Button>
              {kind && (kind.secret_fields?.length ?? 0) > 0 && (
                <Button variant="outline" size="sm" className="rounded-sm" onClick={() => setEdit('rotate')}>
                  <Replace className="size-4" /> Replace secret
                </Button>
              )}
              <Button variant="destructive" size="sm" className="rounded-sm" onClick={() => setDeleting(true)}>
                <Trash2 className="size-4" /> Delete
              </Button>
            </>
          )}
        </div>
      </div>

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_380px]">
        <div className="space-y-6">
          {result && (
            <Card className="gap-0 rounded-xl py-0">
              <CardHeader className="border-b border-border px-5 py-4">
                <CardTitle className="flex items-center gap-2 text-sm font-semibold">
                  <FlaskConical className="size-4" /> {result.ok ? 'Test passed' : 'Test found problems'}
                </CardTitle>
                <CardDescription>Decryption, status and the type&apos;s schema, checked inside the control plane.</CardDescription>
              </CardHeader>
              <CardContent className="px-5 py-4">
                <TestResultPanel result={result} credentialId={c.id} />
              </CardContent>
            </Card>
          )}

          <Card className="gap-0 rounded-xl py-0">
            <CardHeader className="border-b border-border px-5 py-4">
              <CardTitle className="flex items-center gap-2 text-sm font-semibold">
                <Link2 className="size-4" /> Used by
              </CardTitle>
              <CardDescription>Everything that points at this credential. It cannot be deleted while this list is not empty.</CardDescription>
            </CardHeader>
            <CardContent className="px-5 py-4">
              {usage.isLoading ? (
                <p className="flex items-center gap-2 text-xs text-muted-foreground"><Loader2 className="size-3.5 animate-spin" /> Loading…</p>
              ) : (usage.data?.references.length ?? 0) === 0 && (usage.data?.default_for.length ?? 0) === 0 ? (
                <p className="text-xs text-muted-foreground">Nothing references it yet.{kind?.used_for?.length ? ` Pick it where it is needed: ${kind.used_for.join(' · ')}.` : ''}</p>
              ) : (
                <div className="space-y-3">
                  {(usage.data?.default_for ?? []).map((d) => (
                    <p key={d} className="rounded-md border border-info/30 bg-info/10 px-3 py-2 text-xs text-info-dark dark:text-info-light">
                      Implicit default: {d}
                    </p>
                  ))}
                  {(usage.data?.references.length ?? 0) > 0 && (
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <TableHead>Kind</TableHead>
                          <TableHead>Name</TableHead>
                          <TableHead>Field</TableHead>
                          <TableHead>Detail</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {usage.data?.references.map((ref) => (
                          <TableRow key={`${ref.resource_type}:${ref.resource_id}:${ref.field}`}>
                            <TableCell className="text-xs text-muted-foreground">{REFERENCE_LABELS[ref.resource_type]}</TableCell>
                            <TableCell>
                              <Link href={referenceHref(ref)} className="text-[13px] font-medium hover:underline">
                                {ref.name}
                              </Link>
                            </TableCell>
                            <TableCell className="font-mono text-[11px] text-muted-foreground">{ref.field}</TableCell>
                            <TableCell className="text-xs">{ref.detail?.replace(/_/g, ' ')}</TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  )}
                </div>
              )}
            </CardContent>
          </Card>

          <Card className="gap-0 rounded-xl py-0">
            <CardHeader className="border-b border-border px-5 py-4">
              <CardTitle className="flex items-center gap-2 text-sm font-semibold">
                <History className="size-4" /> Recent use
              </CardTitle>
              <CardDescription>Every decryption by a consumer, test or reveal (latest 20). Reveals are also in the audit log.</CardDescription>
            </CardHeader>
            <CardContent className="px-5 py-4">
              {(usage.data?.history.length ?? 0) === 0 ? (
                <p className="text-xs text-muted-foreground">Not used yet.</p>
              ) : (
                <ul className="divide-y divide-border">
                  {usage.data?.history.map((h, i) => (
                    <li key={`${h.used_at}:${i}`} className="flex items-center justify-between gap-3 py-2 text-xs">
                      <span className="flex items-center gap-2">
                        {h.purpose === 'reveal' ? <Eye className="size-3.5 text-warning" /> : <Lock className="size-3.5 text-muted-foreground" />}
                        {purposeLabel(h.purpose)}
                        {h.used_by_username && <span className="text-muted-foreground">by {h.used_by_username}</span>}
                      </span>
                      <RelativeTime value={h.used_at} className="text-muted-foreground" />
                    </li>
                  ))}
                </ul>
              )}
              <Link href={`/audit-log?category=credential,vault&search=${encodeURIComponent(c.name)}`} className="mt-3 inline-flex items-center gap-1 text-[11px] font-medium text-muted-foreground hover:text-foreground">
                Audit trail <ExternalLink className="size-3" />
              </Link>
            </CardContent>
          </Card>
        </div>

        <div className="space-y-4">
          <Card className="rounded-xl">
            <CardContent className="space-y-3">
              <SectionLabel>Credential</SectionLabel>
              <DetailList
                items={[
                  { label: 'Type', value: kind?.label ?? `Untyped (${c.type.replace(/_/g, ' ')})` },
                  { label: 'Preview', value: c.masked_value ?? '••••', mono: true },
                  { label: 'Summary', value: c.summary ?? '—' },
                  { label: 'Used by', value: <UsageCell c={c} /> },
                  { label: 'Expires', value: c.expires_at ? <>{new Date(c.expires_at).toLocaleDateString()} <ExpiryNote value={c.expires_at} /></> : 'never' },
                  { label: 'Last used', value: c.last_used_at ? <RelativeTime value={c.last_used_at} /> : 'never' },
                  { label: 'Uses', value: (c.usage_count ?? 0).toLocaleString() },
                  { label: 'Created', value: new Date(c.created_at).toLocaleString() },
                  { label: 'Updated', value: c.updated_at ? <RelativeTime value={c.updated_at} /> : '—' },
                ]}
              />
            </CardContent>
          </Card>
          {(publicItems.length > 0 || publicKey) && (
            <Card className="rounded-xl">
              <CardContent className="space-y-3">
                <SectionLabel>Public fields</SectionLabel>
                {publicKey && <pre className="overflow-x-auto rounded-md border border-border bg-muted/40 p-3 font-mono text-[11px] leading-relaxed whitespace-pre-wrap break-all">{publicKey}</pre>}
                {publicItems.length > 0 && <DetailList items={publicItems} />}
              </CardContent>
            </Card>
          )}
          <Card className="rounded-xl">
            <CardContent className="space-y-3">
              <SectionLabel>Secret fields</SectionLabel>
              {secretFields.length === 0 ? (
                <p className="text-xs text-muted-foreground">None — this type stores public data only.</p>
              ) : (
                <ul className="space-y-1.5">
                  {secretFields.map((f) => {
                    const set = (c.secret_fields ?? []).includes(f.name);
                    return (
                      <li key={f.name} className="flex items-center justify-between gap-3 rounded-lg bg-muted/50 px-3 py-2 text-xs">
                        <span className="flex items-center gap-1.5 text-muted-foreground">
                          <Lock className="size-3" /> {f.label}
                        </span>
                        <span className="font-mono text-[11px]">{set ? '•••••••• stored' : <span className="text-muted-foreground">not set</span>}</span>
                      </li>
                    );
                  })}
                </ul>
              )}
              {c.system ? (
                <p className="text-[11px] text-muted-foreground">Managed by GPU Deck (PKI). It cannot be revealed, edited or deleted here.</p>
              ) : (
                <Button size="xs" variant="ghost" className="-ml-2" onClick={() => setRevealing(true)}>
                  <Copy className="size-3" /> Reveal and copy…
                </Button>
              )}
            </CardContent>
          </Card>
        </div>
      </div>

      <CredentialDialog open={!!edit} onOpenChange={(o) => !o && setEdit(null)} credential={c} rotate={edit === 'rotate'} />
      <RevealFlow credential={revealing ? c : null} open={revealing} onOpenChange={setRevealing} />
      <DeleteCredentialFlow credential={deleting ? c : null} onOpenChange={(o) => !o && setDeleting(false)} onDeleted={() => router.push('/credentials')} />
    </PageContainer>
  );
}
