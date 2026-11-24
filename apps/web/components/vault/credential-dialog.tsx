'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import { ArrowLeft, ExternalLink, Fingerprint, Loader2, Lock, Replace, ShieldCheck, Upload } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import { PasswordInput } from '@/components/shared/PasswordInput';
import { KindGlyph } from '@/components/vault/kind-icon';
import { useCreateCredential, useCredentialKinds, useCredentialOptionsAll, useUpdateCredential, vaultErrorMessage } from '@/hooks/use-vault';
import { parseSshKey, sshFingerprint } from '@/lib/ssh-keys';
import { cn } from '@/lib/utils';
import { buildFields, gcpKeyInfo, initialValues, suggestName, validateCredential, type FieldValue, type FormErrors, type FormValues } from '@/lib/vault/credential-form';
import { CATEGORY_LABELS, CATEGORY_ORDER, type Credential, type CredentialKind, type CredentialKindField } from '@/types/vault';

export interface CredentialDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Edit this credential (its kind is fixed). */
  credential?: Credential | null;
  /** Start with every secret field in "replace" mode (rotate). */
  rotate?: boolean;
  /** Kinds the caller accepts (pickers); omit for every kind. */
  allowedKinds?: string[];
  /** Preselected kind (skips the type step). */
  initialKind?: string | null;
  /** Called with the saved credential (pickers select it). */
  onSaved?: (credential: Credential) => void;
}

/**
 * Create / edit / rotate a typed vault credential. The form is generated from
 * the kind's fields in GET /vault/metadata; secret fields are write-only (on
 * edit they show as stored and are replaced only when typed again), GCP keys
 * and kubeconfigs can be uploaded as files, SSH keys from a .pub file.
 */
export function CredentialDialog(props: CredentialDialogProps) {
  const { open, onOpenChange } = props;
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] min-w-0 gap-0 overflow-x-hidden overflow-y-auto p-0 sm:max-w-2xl [&>*]:min-w-0">{open && <DialogBody {...props} />}</DialogContent>
    </Dialog>
  );
}

function DialogBody({ onOpenChange, credential, rotate, allowedKinds, initialKind, onSaved }: CredentialDialogProps) {
  const { kinds, byId, isLoading } = useCredentialKinds();
  const existing = useCredentialOptionsAll();
  const editing = !!credential;
  const choices = useMemo(() => kinds.filter((k) => !allowedKinds || allowedKinds.includes(k.id)), [kinds, allowedKinds]);
  const fixedKind = credential?.kind ?? initialKind ?? (choices.length === 1 ? choices[0].id : null);
  const [kindId, setKindId] = useState<string | null>(fixedKind);
  const kind = kindId ? byId[kindId] : undefined;

  if (isLoading) {
    return (
      <div className="flex items-center justify-center gap-2 p-10 text-sm text-muted-foreground">
        <Loader2 className="size-4 animate-spin" /> Loading credential types…
      </div>
    );
  }
  if (editing && !credential?.kind) {
    return <LegacyNotice credential={credential as Credential} onClose={() => onOpenChange(false)} />;
  }
  if (!kind) {
    return <KindStep choices={choices} onPick={setKindId} onCancel={() => onOpenChange(false)} />;
  }
  return (
    <DetailsStep
      key={kind.id}
      kind={kind}
      credential={credential ?? null}
      rotate={!!rotate}
      takenNames={(existing.data ?? []).map((c) => c.name)}
      onBack={!editing && choices.length > 1 && !initialKind ? () => setKindId(null) : undefined}
      onCancel={() => onOpenChange(false)}
      onSaved={(c) => {
        onSaved?.(c);
        onOpenChange(false);
      }}
    />
  );
}

function LegacyNotice({ credential, onClose }: { credential: Credential; onClose: () => void }) {
  return (
    <>
      <DialogHeader className="gap-1.5 border-b border-border p-5">
        <DialogTitle className="text-base font-semibold">“{credential.name}” has no type</DialogTitle>
        <DialogDescription className="text-[13px] leading-relaxed">
          It was stored before credentials were typed (for example an alert channel&apos;s inline secret). It keeps working where it is used. To give it fields and validation, create a typed credential, point the consumer at it, then delete this one.
        </DialogDescription>
      </DialogHeader>
      <DialogFooter className="m-0 rounded-none border-t border-border p-4">
        <Button size="sm" variant="outline" onClick={onClose}>
          Close
        </Button>
      </DialogFooter>
    </>
  );
}

function KindStep({ choices, onPick, onCancel }: { choices: CredentialKind[]; onPick: (id: string) => void; onCancel: () => void }) {
  const groups = CATEGORY_ORDER.map((cat) => ({ cat, items: choices.filter((k) => k.category === cat) })).filter((g) => g.items.length > 0);
  return (
    <>
      <DialogHeader className="gap-1.5 border-b border-border p-5">
        <DialogTitle className="text-base font-semibold">Add a credential</DialogTitle>
        <DialogDescription className="text-[13px] leading-relaxed">Pick what it is. Each type knows its fields, validates them, and only shows up where it can be used.</DialogDescription>
      </DialogHeader>
      <div className="space-y-5 p-5">
        {groups.map(({ cat, items }) => (
          <section key={cat} className="space-y-2">
            <h3 className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">{CATEGORY_LABELS[cat]}</h3>
            <div className="grid gap-2 sm:grid-cols-2">
              {items.map((k) => (
                <button
                  key={k.id}
                  type="button"
                  onClick={() => onPick(k.id)}
                  className="group flex items-start gap-3 rounded-lg border border-border p-3 text-left transition-colors outline-none hover:border-foreground/30 hover:bg-muted/30 focus-visible:ring-2 focus-visible:ring-ring/30"
                >
                  <KindGlyph kind={k.id} category={k.category} size="md" />
                  <span className="min-w-0">
                    <span className="block text-[13px] font-medium">{k.label}</span>
                    <span className="line-clamp-2 block text-[11px] leading-snug text-muted-foreground">{k.description}</span>
                  </span>
                </button>
              ))}
            </div>
          </section>
        ))}
        {groups.length === 0 && <p className="py-6 text-center text-sm text-muted-foreground">No credential type fits here.</p>}
      </div>
      <DialogFooter className="m-0 items-center rounded-none border-t border-border p-4 sm:justify-between">
        <span className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
          <Lock className="size-3" /> Encrypted at rest; secrets never come back to the browser.
        </span>
        <Button size="sm" variant="outline" onClick={onCancel}>
          Cancel
        </Button>
      </DialogFooter>
    </>
  );
}

interface DetailsStepProps {
  kind: CredentialKind;
  credential: Credential | null;
  rotate: boolean;
  takenNames: string[];
  onBack?: () => void;
  onCancel: () => void;
  onSaved: (credential: Credential) => void;
}

function DetailsStep({ kind, credential, rotate, takenNames, onBack, onCancel, onSaved }: DetailsStepProps) {
  const editing = !!credential;
  const create = useCreateCredential();
  const update = useUpdateCredential();
  const stored = credential?.secret_fields ?? [];
  const [name, setName] = useState(credential?.name ?? suggestName(kind, takenNames));
  const [description, setDescription] = useState(credential?.description ?? '');
  const [expires, setExpires] = useState(credential?.expires_at ? credential.expires_at.slice(0, 10) : '');
  const [values, setValues] = useState<FormValues>(() => initialValues(kind, credential));
  // Secret fields that are open for typing on edit ("Replace"); a rotate opens all of them.
  const [replacing, setReplacing] = useState<string[]>(() => (rotate ? kind.secret_fields ?? [] : []));
  const [submitted, setSubmitted] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);
  const [today] = useState(() => new Date().toISOString().slice(0, 10));

  const ctx = { editing, storedSecrets: stored };
  const errors: FormErrors = validateCredential(kind, values, ctx);
  if (!name.trim()) errors.__name = 'Name the credential';
  else if (name.trim().length > 255) errors.__name = 'At most 255 characters';
  const show = (k: string) => (submitted ? errors[k] : undefined);
  const busy = create.isPending || update.isPending;

  function set(field: string, value: FieldValue) {
    setValues((prev) => ({ ...prev, [field]: value }));
  }

  function submit() {
    setSubmitted(true);
    setServerError(null);
    if (Object.keys(errors).length > 0) return;
    const fields = buildFields(kind, values, ctx);
    const common = {
      description: description.trim() || null,
      expires_at: expires ? new Date(`${expires}T00:00:00Z`).toISOString() : null,
    };
    const onError = (error: unknown) => setServerError(vaultErrorMessage(error));
    if (editing && credential) {
      update.mutate({ id: credential.id, body: { name: name.trim(), fields, ...common } }, { onSuccess: onSaved, onError });
    } else {
      create.mutate({ name: name.trim(), provider: kind.id, fields, ...common }, { onSuccess: onSaved, onError });
    }
  }

  const title = editing ? (rotate ? `Rotate “${credential?.name}”` : `Edit “${credential?.name}”`) : `New ${kind.label}`;
  return (
    <form
      onSubmit={(e) => {
        // The dialog is portaled but React still bubbles the submit to an enclosing form
        // (pickers live inside cluster / channel forms): keep it here.
        e.preventDefault();
        e.stopPropagation();
        submit();
      }}
    >
      <DialogHeader className="gap-0 border-b border-border p-5">
        <div className="flex items-start gap-3 pr-8">
          <KindGlyph kind={kind.id} category={kind.category} size="lg" />
          <div className="min-w-0 space-y-1">
            <DialogTitle className="text-base font-semibold">{title}</DialogTitle>
            <DialogDescription className="text-[13px] leading-relaxed">
              {rotate ? 'Paste the new secret values; the old ones stop being used as soon as you save.' : kind.description}
              {kind.docs_url && (
                <a href={kind.docs_url} target="_blank" rel="noreferrer" className="ml-1 inline-flex items-center gap-0.5 font-medium text-foreground underline-offset-2 hover:underline">
                  How to get one <ExternalLink className="size-3" />
                </a>
              )}
            </DialogDescription>
          </div>
        </div>
      </DialogHeader>

      <div className="space-y-5 p-5">
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="block space-y-1.5 text-xs font-medium">
            Name
            <Input value={name} onChange={(e) => setName(e.target.value)} autoComplete="off" className="font-mono text-sm" aria-invalid={!!show('__name')} />
            {show('__name') ? <FieldError message={show('__name') as string} /> : <span className="block text-[11px] font-normal text-muted-foreground">How pickers and the CLI refer to it.</span>}
          </label>
          <label className="block space-y-1.5 text-xs font-medium">
            Description <span className="font-normal text-muted-foreground">optional</span>
            <Input value={description} onChange={(e) => setDescription(e.target.value)} placeholder="Who owns it, what it can reach" />
          </label>
        </div>

        <div className="space-y-4 rounded-lg border border-border bg-muted/20 p-4">
          {kind.fields.map((field) => (
            <CredentialFieldInput
              key={field.name}
              field={field}
              value={values[field.name]}
              onChange={(v) => set(field.name, v)}
              error={show(field.name)}
              storedSecret={editing && field.secret && stored.includes(field.name) && !replacing.includes(field.name)}
              onReplace={() => setReplacing((r) => [...r, field.name])}
              autoFocus={rotate && field.secret && field.name === kind.secret_fields?.[0]}
            />
          ))}
          <DerivedFacts kind={kind} values={values} />
        </div>

        <label className="block max-w-xs space-y-1.5 text-xs font-medium">
          Expires <span className="font-normal text-muted-foreground">optional — marked expired after this date</span>
          <Input type="date" min={editing ? undefined : today} value={expires} onChange={(e) => setExpires(e.target.value)} />
        </label>

        {serverError && <p className="rounded-md border border-error/30 bg-error/10 p-3 text-xs text-error-dark dark:text-error-light">{serverError}</p>}
      </div>

      <DialogFooter className="m-0 items-center rounded-none border-t border-border p-4 sm:justify-between">
        <span className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
          <ShieldCheck className="size-3.5" />
          {kind.secret_fields?.length ? 'Secret fields are write-only and encrypted with the vault keys.' : 'Public key only — nothing secret is stored.'}
        </span>
        <div className="flex gap-2">
          {onBack && (
            <Button type="button" size="sm" variant="ghost" onClick={onBack} disabled={busy}>
              <ArrowLeft className="size-4" /> Type
            </Button>
          )}
          <Button type="button" size="sm" variant="outline" onClick={onCancel} disabled={busy}>
            Cancel
          </Button>
          <Button type="submit" size="sm" disabled={busy}>
            {busy && <Loader2 className="size-4 animate-spin" />}
            {editing ? (rotate ? 'Save new secret' : 'Save changes') : 'Add credential'}
          </Button>
        </div>
      </DialogFooter>
    </form>
  );
}

function FieldError({ message }: { message: string }) {
  return <span className="block text-[11px] font-medium text-destructive">{message}</span>;
}

interface FieldInputProps {
  field: CredentialKindField;
  value: FieldValue | undefined;
  onChange: (value: FieldValue) => void;
  error?: string;
  /** Edit mode: the secret is stored and not being replaced. */
  storedSecret: boolean;
  onReplace: () => void;
  autoFocus?: boolean;
}

function CredentialFieldInput({ field, value, onChange, error, storedSecret, onReplace, autoFocus }: FieldInputProps) {
  const fileRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [fileError, setFileError] = useState<string | null>(null);
  const id = `cred-${field.name}`;

  async function load(file: File | undefined) {
    if (!file) return;
    setFileError(null);
    if (field.max_length && file.size > field.max_length * 4) {
      setFileError(`${file.name} is too large for this field`);
      return;
    }
    const content = (await file.text()).replace(/\r\n/g, '\n');
    onChange(field.input === 'textarea' && field.name === 'public_key' ? content.trim() : content);
  }

  if (field.input === 'boolean') {
    return (
      <label className="flex items-center justify-between gap-3 text-xs font-medium">
        <span>
          {field.label}
          {field.help && <span className="block text-[11px] font-normal text-muted-foreground">{field.help}</span>}
        </span>
        <Switch checked={Boolean(value)} onCheckedChange={(v) => onChange(v)} aria-label={field.label} />
      </label>
    );
  }

  const label = (
    <span className="flex items-center gap-1.5">
      {field.label}
      {field.secret && <Lock className="size-3 text-muted-foreground" aria-label="secret" />}
      {!field.required && <span className="font-normal text-muted-foreground">optional</span>}
    </span>
  );

  if (storedSecret) {
    return (
      <div className="space-y-1.5 text-xs font-medium">
        {label}
        <div className="flex items-center gap-2">
          <div className="flex h-9 min-w-0 flex-1 items-center gap-2 rounded-md border border-dashed border-border bg-background px-3 font-mono text-xs text-muted-foreground">
            <Lock className="size-3.5 shrink-0" /> •••••••••••• <span className="font-sans">stored</span>
          </div>
          <Button type="button" size="sm" variant="outline" onClick={onReplace} aria-label={`Replace ${field.label}`}>
            <Replace className="size-3.5" /> Replace
          </Button>
        </div>
      </div>
    );
  }

  const multiline = field.input === 'textarea' || field.input === 'json';
  const str = typeof value === 'string' ? value : '';
  return (
    <div className="space-y-1.5 text-xs font-medium">
      <label htmlFor={id}>{label}</label>
      {multiline ? (
        <div
          className={cn('relative rounded-md transition-shadow', dragging && 'ring-2 ring-brand/40')}
          onDragOver={(e) => {
            if (!field.upload_accept) return;
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => {
            if (!field.upload_accept) return;
            e.preventDefault();
            setDragging(false);
            void load(e.dataTransfer.files?.[0]);
          }}
        >
          <Textarea
            id={id}
            value={str}
            onChange={(e) => onChange(e.target.value)}
            rows={field.input === 'json' || field.name === 'kubeconfig' ? 7 : 3}
            spellCheck={false}
            autoComplete="off"
            autoFocus={autoFocus}
            placeholder={field.placeholder ?? (field.upload_accept ? 'Paste here, or drop the file' : undefined)}
            className="field-sizing-fixed w-full min-w-0 resize-y font-mono text-[11.5px] leading-relaxed break-all"
            aria-invalid={!!error}
          />
        </div>
      ) : field.secret || field.input === 'password' ? (
        <PasswordInput id={id} value={str} onChange={(e) => onChange(e.target.value)} placeholder={field.placeholder ?? undefined} autoComplete="new-password" autoFocus={autoFocus} className="font-mono text-sm" aria-invalid={!!error} />
      ) : (
        <Input
          id={id}
          type={field.input === 'number' ? 'number' : field.input === 'email' ? 'email' : field.input === 'url' ? 'url' : 'text'}
          value={str}
          onChange={(e) => onChange(e.target.value)}
          placeholder={field.placeholder ?? (field.default !== undefined && field.default !== null ? String(field.default) : undefined)}
          min={field.minimum ?? undefined}
          max={field.maximum ?? undefined}
          autoComplete="off"
          className={cn(field.input !== 'email' && 'font-mono text-sm', field.input === 'number' && 'w-32')}
          aria-invalid={!!error}
        />
      )}
      {field.upload_accept && (
        <div className="flex flex-wrap items-center gap-2">
          <input ref={fileRef} type="file" accept={field.upload_accept} hidden onChange={(e) => { void load(e.target.files?.[0]); e.target.value = ''; }} />
          <Button type="button" variant="outline" size="xs" onClick={() => fileRef.current?.click()}>
            <Upload className="size-3" /> Upload {field.name === 'public_key' ? '.pub' : field.input === 'json' ? '.json' : 'file'}
          </Button>
          {fileError && <span className="text-[11px] font-medium text-destructive">{fileError}</span>}
        </div>
      )}
      {error ? <FieldError message={error} /> : field.help ? <span className="block text-[11px] font-normal text-muted-foreground">{field.help}</span> : null}
    </div>
  );
}

/** Live preview of what the backend derives (GCP project / account, SSH fingerprint). */
function DerivedFacts({ kind, values }: { kind: CredentialKind; values: FormValues }) {
  const [fingerprint, setFingerprint] = useState<string | null>(null);
  const publicKey = kind.id === 'ssh_key' && typeof values.public_key === 'string' ? values.public_key.trim() : '';
  useEffect(() => {
    let cancelled = false;
    const parsed = publicKey ? parseSshKey(publicKey.split(/\r?\n/)[0]) : null;
    if (!parsed || !parsed.ok) {
      Promise.resolve().then(() => !cancelled && setFingerprint(null));
    } else {
      void sshFingerprint(parsed.key.blob).then((fp) => !cancelled && setFingerprint(`${fp}${parsed.key.comment ? ` · ${parsed.key.comment}` : ''}`));
    }
    return () => {
      cancelled = true;
    };
  }, [publicKey]);

  if (kind.id === 'gcp' && typeof values.service_account_json === 'string') {
    const info = gcpKeyInfo(values.service_account_json);
    if (!info?.projectId) return null;
    return (
      <p className="flex flex-wrap items-center gap-1.5 text-[11px] text-muted-foreground">
        <ShieldCheck className="size-3.5 text-success" /> Project <span className="font-mono text-foreground">{info.projectId}</span>
        {info.clientEmail && (
          <>
            · <span className="font-mono">{info.clientEmail}</span>
          </>
        )}
      </p>
    );
  }
  if (kind.id === 'ssh_key' && fingerprint) {
    return (
      <p className="flex min-w-0 items-start gap-1.5 text-[11px] text-muted-foreground">
        <Fingerprint className="mt-px size-3.5 shrink-0 text-success" /> <span className="min-w-0 font-mono break-all">{fingerprint}</span>
      </p>
    );
  }
  return null;
}
