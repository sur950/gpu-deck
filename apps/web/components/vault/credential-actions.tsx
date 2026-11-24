'use client';

import { useState } from 'react';
import Link from 'next/link';
import { BadgeCheck, CircleCheck, CircleX, CloudCog, Eye, Link2, Loader2, ShieldAlert, TriangleAlert } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { ConfirmationDialog } from '@/components/shared/ConfirmationDialog';
import { CopyButton } from '@/components/shared/copy-button';
import { DeleteConfirmationDialog } from '@/components/shared/DeleteConfirmationDialog';
import { KindGlyph } from '@/components/vault/kind-icon';
import { useCheckCloudCredential } from '@/hooks/use-cloud';
import { useCredentialKinds, useCredentialUsage, useDeleteCredential, useRevealCredential } from '@/hooks/use-vault';
import { cn } from '@/lib/utils';
import { REFERENCE_LABELS, referenceHref, usageSummary, type Credential, type CredentialRevealed, type CredentialTestResult } from '@/types/vault';
import type { CloudProviderId } from '@/types/cloud';

type CredentialRef = Pick<Credential, 'id' | 'name' | 'kind' | 'provider'>;

/** Reveal = confirm (audited) → plaintext dialog. The value lives only in this component's state. */
export function RevealFlow({ credential, open, onOpenChange }: { credential: CredentialRef | null; open: boolean; onOpenChange: (open: boolean) => void }) {
  const reveal = useRevealCredential();
  const [revealed, setRevealed] = useState<CredentialRevealed | null>(null);
  return (
    <>
      <ConfirmationDialog
        open={open}
        onOpenChange={onOpenChange}
        icon={<Eye className="size-4" />}
        title={`Reveal “${credential?.name ?? ''}”?`}
        description="The plaintext is shown once in this browser. The reveal is recorded in the audit log (credential.revealed) and in the credential's use history with your account."
        confirmText="Reveal secret"
        isLoading={reveal.isPending}
        onConfirm={() => credential && reveal.mutate(credential.id, { onSuccess: setRevealed })}
      />
      <RevealedDialog credential={credential} revealed={revealed} onClose={() => setRevealed(null)} />
    </>
  );
}

function RevealedDialog({ credential, revealed, onClose }: { credential: CredentialRef | null; revealed: CredentialRevealed | null; onClose: () => void }) {
  const { byId } = useCredentialKinds();
  const kind = credential?.kind ? byId[credential.kind] : undefined;
  const fields = (revealed?.fields ?? {}) as Record<string, unknown>;
  const rows = kind
    ? kind.fields.filter((f) => fields[f.name] !== undefined && fields[f.name] !== null && fields[f.name] !== '').map((f) => ({ name: f.name, label: f.label, secret: f.secret, multiline: f.input === 'json' || f.input === 'textarea', value: String(fields[f.name]) }))
    : [{ name: 'value', label: 'Value', secret: true, multiline: (revealed?.value ?? '').includes('\n'), value: revealed?.value ?? '' }];
  return (
    <Dialog open={!!revealed} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-h-[88vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <KindGlyph kind={credential?.kind ?? credential?.provider} category={kind?.category ?? 'legacy'} size="sm" />
            {credential?.name}
          </DialogTitle>
          <DialogDescription>Revealed just now and recorded in the audit log. Close this dialog when you are done — the values are not kept.</DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          {rows.map((row) => (
            <div key={row.name} className="space-y-1">
              <p className="flex items-center justify-between text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                <span>
                  {row.label}
                  {!row.secret && <span className="ml-1.5 normal-case tracking-normal">· public</span>}
                </span>
                <CopyButton value={row.value} successText={`${row.label} copied`} />
              </p>
              {row.multiline ? (
                <pre className="max-h-56 overflow-auto rounded-md border border-border bg-muted/40 p-3 font-mono text-[11px] leading-relaxed whitespace-pre-wrap break-all">{row.value}</pre>
              ) : (
                <code className={cn('block break-all rounded-md border border-border bg-muted/40 px-3 py-2 font-mono text-[12.5px]', row.secret && 'font-medium')}>{row.value}</code>
              )}
            </div>
          ))}
        </div>
        <DialogFooter>
          <Button size="sm" onClick={onClose}>
            Done
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** Offline test result (decrypt / status / schema) + the live provider check for cloud kinds. */
export function TestResultPanel({ result, credentialId }: { result: CredentialTestResult; credentialId: string }) {
  const cloud = useCheckCloudCredential();
  return (
    <div className="space-y-2">
      <ul className="divide-y divide-border rounded-md border border-border">
        {result.checks.map((c) => (
          <li key={c.name} className="flex items-start gap-2.5 px-3 py-2 text-xs">
            {c.ok ? <CircleCheck className="mt-px size-3.5 shrink-0 text-success" /> : <CircleX className="mt-px size-3.5 shrink-0 text-error" />}
            <span className="w-14 shrink-0 font-medium capitalize">{c.name}</span>
            <span className={cn('min-w-0', c.ok ? 'text-muted-foreground' : 'text-error-dark dark:text-error-light')}>{c.message}</span>
          </li>
        ))}
      </ul>
      {result.cloud_provider && (
        <div className="flex flex-wrap items-center gap-2">
          <Button type="button" size="xs" variant="outline" disabled={cloud.isPending} onClick={() => cloud.mutate({ provider: result.cloud_provider as CloudProviderId, credentialId })}>
            {cloud.isPending ? <Loader2 className="size-3 animate-spin" /> : <CloudCog className="size-3" />}
            Check with {result.cloud_provider === 'digitalocean' ? 'DigitalOcean' : result.cloud_provider.toUpperCase()}
          </Button>
          {cloud.data && (
            <span className={cn('flex items-center gap-1.5 text-[11px]', cloud.data.ok ? 'text-success-dark dark:text-success-light' : 'text-error-dark dark:text-error-light')}>
              {cloud.data.ok ? <BadgeCheck className="size-3.5" /> : <ShieldAlert className="size-3.5" />}
              {cloud.data.ok ? `Authenticated as ${cloud.data.account ?? 'the account'}` : (cloud.data.detail ?? 'Rejected by the provider')}
            </span>
          )}
          {!cloud.data && <span className="text-[11px] text-muted-foreground">Calls the provider API with this key (read-only).</span>}
        </div>
      )}
    </div>
  );
}

/** Delete with "in use" protection: used credentials list where they are referenced instead. */
export function DeleteCredentialFlow({ credential, onOpenChange, onDeleted }: { credential: Credential | null; onOpenChange: (open: boolean) => void; onDeleted?: () => void }) {
  const del = useDeleteCredential();
  const used = (credential?.used_by?.total ?? 0) > 0;
  return (
    <>
      <DeleteConfirmationDialog
        open={!!credential && !used}
        onOpenChange={onOpenChange}
        itemName={credential?.name ?? ''}
        resourceType="credential"
        warningMessage={credential?.default_for?.length ? `It is the implicit default for: ${credential.default_for.join(', ')}. Those fall back to the next matching credential (or none).` : 'The ciphertext is removed from the vault; its audit trail stays.'}
        isDeleting={del.isPending}
        onConfirm={() => {
          if (credential)
            del.mutate(credential, {
              onSuccess: () => {
                onOpenChange(false);
                onDeleted?.();
              },
            });
        }}
      />
      <InUseDialog credential={used ? credential : null} onClose={() => onOpenChange(false)} />
    </>
  );
}

function InUseDialog({ credential, onClose }: { credential: Credential | null; onClose: () => void }) {
  const usage = useCredentialUsage(credential?.id);
  return (
    <Dialog open={!!credential} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <TriangleAlert className="size-4 text-warning" /> “{credential?.name}” is in use
          </DialogTitle>
          <DialogDescription>Used by {usageSummary(credential?.used_by)}. Point these at another credential (or remove them) before deleting it.</DialogDescription>
        </DialogHeader>
        {usage.isLoading ? (
          <div className="flex items-center gap-2 py-4 text-xs text-muted-foreground">
            <Loader2 className="size-3.5 animate-spin" /> Looking up references…
          </div>
        ) : (
          <ul className="divide-y divide-border rounded-md border border-border">
            {(usage.data?.references ?? []).map((ref) => (
              <li key={`${ref.resource_type}:${ref.resource_id}:${ref.field}`} className="flex items-center gap-3 px-3 py-2 text-xs">
                <span className="w-24 shrink-0 text-muted-foreground">{REFERENCE_LABELS[ref.resource_type]}</span>
                <Link href={referenceHref(ref)} className="min-w-0 flex-1 truncate font-medium hover:underline">
                  {ref.name}
                </Link>
                <span className="hidden font-mono text-[10.5px] text-muted-foreground sm:inline">{ref.field}</span>
                <Link2 className="size-3 shrink-0 text-muted-foreground" />
              </li>
            ))}
          </ul>
        )}
        <DialogFooter>
          <Button size="sm" variant="outline" onClick={onClose}>
            Close
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
