'use client';

import { useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { KeyRound, Loader2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { PageContainer } from '@/components/shared/PageContainer';
import { PageHeader } from '@/components/shared/PageHeader';
import { KeyValueEditor, type KeyValueRow } from '@/components/shared/key-value-editor';
import { useCreateApiKey } from '@/hooks/api/security';
import type { ApiKeyCreated } from '@/hooks/api/security-api';
import { Segmented } from '@/components/automation/segmented';
import { KeyReveal } from '@/components/security/key-reveal';

type Expiry = 'never' | '30' | '90' | '365' | 'custom';

/** Create a key; on success the plaintext is shown once on this page and nowhere else. */
export function ApiKeyNew() {
  const router = useRouter();
  const create = useCreateApiKey();
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [expiry, setExpiry] = useState<Expiry>('90');
  const [customDate, setCustomDate] = useState('');
  const [metadata, setMetadata] = useState<KeyValueRow[]>([]);
  const [submitted, setSubmitted] = useState(false);
  const [created, setCreated] = useState<ApiKeyCreated | null>(null);
  const [stored, setStored] = useState(false);
  const [today] = useState(() => new Date().toISOString().slice(0, 10));

  const nameError = submitted && !name.trim() ? 'Name the key after the client that uses it' : null;
  const dateError = submitted && expiry === 'custom' && !customDate ? 'Pick a date' : null;

  function expiresAt(): string | null {
    if (expiry === 'never') return null;
    if (expiry === 'custom') return customDate ? new Date(`${customDate}T00:00:00Z`).toISOString() : null;
    return new Date(Date.now() + Number(expiry) * 86_400_000).toISOString();
  }

  function submit() {
    setSubmitted(true);
    if (!name.trim() || (expiry === 'custom' && !customDate)) return;
    create.mutate(
      { name: name.trim(), description: description.trim() || null, expires_at: expiresAt(), metadata: Object.fromEntries(metadata.filter((r) => r.key.trim()).map((r) => [r.key.trim(), r.value])) },
      { onSuccess: setCreated },
    );
  }

  if (created) {
    return (
      <PageContainer className="max-w-3xl">
        <PageHeader title="Key created" description="Copy the secret before leaving this page." />
        <Card className="rounded-xl">
          <CardContent className="space-y-5">
            <KeyReveal secret={created.key} name={created.name} onConfirmedChange={setStored} />
            <div className="flex justify-end gap-2 border-t border-border pt-4">
              <Button variant="outline" size="sm" disabled={!stored} onClick={() => router.push(`/api-keys/${created.id}`)}>
                Open key
              </Button>
              <Button size="sm" disabled={!stored} onClick={() => router.push('/api-keys')}>
                Done
              </Button>
            </div>
          </CardContent>
        </Card>
      </PageContainer>
    );
  }

  return (
    <PageContainer className="max-w-3xl">
      <PageHeader title="New API key" description="One key per client makes usage, cost and revocation per client." />
      <Card className="gap-0 rounded-xl py-0">
        <CardHeader className="border-b border-border px-5 py-4">
          <CardTitle className="flex items-center gap-2 text-sm font-semibold">
            <KeyRound className="size-4" /> Key details
          </CardTitle>
          <CardDescription>
            Keys look like <span className="font-mono text-foreground">gd-</span> followed by 48 hex characters; only a SHA-256 of the secret is stored.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-5 px-5 py-5">
          <label className="block space-y-1.5 text-[13px] font-medium">
            Name
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="gateway-prod" maxLength={255} aria-invalid={!!nameError} autoFocus />
            {nameError && <span className="block text-[11px] font-medium text-destructive">{nameError}</span>}
          </label>
          <label className="block space-y-1.5 text-[13px] font-medium">
            Description <span className="text-[11px] font-normal text-muted-foreground">optional</span>
            <Textarea value={description} onChange={(e) => setDescription(e.target.value)} rows={2} className="text-xs" placeholder="Who uses it and for what." />
          </label>
          <div className="space-y-2 text-[13px] font-medium">
            Expires
            <Segmented
              value={expiry}
              onChange={setExpiry}
              options={[
                { value: 'never', label: 'Never' },
                { value: '30', label: '30 days' },
                { value: '90', label: '90 days' },
                { value: '365', label: '1 year' },
                { value: 'custom', label: 'On a date' },
              ]}
              aria-label="Expiry"
            />
            {expiry === 'custom' && <Input type="date" min={today} value={customDate} onChange={(e) => setCustomDate(e.target.value)} className="w-48" aria-invalid={!!dateError} />}
            {dateError && <span className="block text-[11px] font-medium text-destructive">{dateError}</span>}
            <p className="text-[11px] font-normal text-muted-foreground">Expired keys fail like revoked ones, but can be extended later.</p>
          </div>
          <div className="space-y-2 text-[13px] font-medium">
            Metadata <span className="text-[11px] font-normal text-muted-foreground">optional labels (owner, env…)</span>
            <KeyValueEditor rows={metadata} onChange={setMetadata} keyPlaceholder="owner" valuePlaceholder="platform" addLabel="Add label" />
          </div>
        </CardContent>
        <div className="flex justify-end gap-2 border-t border-border bg-muted/20 px-5 py-4">
          <Link href="/api-keys" className="self-center px-3 text-xs text-muted-foreground hover:text-foreground">
            Cancel
          </Link>
          <Button size="sm" disabled={create.isPending} onClick={submit}>
            {create.isPending && <Loader2 className="size-4 animate-spin" />}
            Create key
          </Button>
        </div>
      </Card>
    </PageContainer>
  );
}
