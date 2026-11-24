'use client';

import { useEffect, useRef, useState } from 'react';
import { CircleAlert, KeyRound, Upload } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { CredentialPicker } from '@/components/vault/credential-picker';
import { cn } from '@/lib/utils';
import { MAX_SSH_KEYS, parseSshKeyLines, sshFingerprint, sshKeyTypeLabel } from '@/lib/ssh-keys';
import type { Credential } from '@/types/vault';

interface SshKeysFieldProps {
  value: string;
  onChange: (value: string) => void;
  invalid?: boolean;
  id?: string;
}

interface Row {
  lineNo: number;
  ok: boolean;
  label: string;
  detail: string;
}

/**
 * authorized_keys editor: paste keys or upload / drop `.pub` files; every
 * line is checked like the backend does and shows its `SHA256:` fingerprint,
 * so it can be compared with `ssh-keygen -lf ~/.ssh/id_ed25519.pub`.
 */
export function SshKeysField({ value, onChange, invalid, id }: SshKeysFieldProps) {
  const fileRef = useRef<HTMLInputElement>(null);
  const [rows, setRows] = useState<Row[]>([]);
  const [dragging, setDragging] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const lines = parseSshKeyLines(value);
    Promise.all(
      lines.map(async ({ lineNo, result }) =>
        result.ok
          ? { lineNo, ok: true, label: `${sshKeyTypeLabel(result.key.type)}${result.key.comment ? ` · ${result.key.comment}` : ''}`, detail: await sshFingerprint(result.key.blob) }
          : { lineNo, ok: false, label: `Line ${lineNo}`, detail: result.error },
      ),
    ).then((next) => {
      if (!cancelled) setRows(next);
    });
    return () => {
      cancelled = true;
    };
  }, [value]);

  function addFromVault(credential?: Credential) {
    const key = typeof credential?.fields?.public_key === 'string' ? credential.fields.public_key.trim() : '';
    if (!key) return;
    const lines = value.split(/\r?\n/).map((l) => l.trim());
    if (lines.includes(key)) return;
    onChange(value.trim() ? `${value.trim()}\n${key}` : key);
  }

  async function addFiles(files: FileList | null) {
    if (!files?.length) return;
    const texts = await Promise.all([...files].slice(0, MAX_SSH_KEYS).map((f) => f.text()));
    const added = texts.map((t) => t.trim()).filter(Boolean).join('\n');
    if (added) onChange(value.trim() ? `${value.trim()}\n${added}` : added);
  }

  return (
    <div className="space-y-2">
      <div
        className={cn('relative rounded-md transition-shadow', dragging && 'ring-2 ring-brand/40')}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          void addFiles(e.dataTransfer.files);
        }}
      >
        <Textarea
          id={id}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          rows={4}
          spellCheck={false}
          placeholder={'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAA… you@laptop\n# one key per line — paste, or drop .pub files here'}
          className="field-sizing-fixed min-h-24 w-full min-w-0 resize-y font-mono text-[11.5px] leading-relaxed break-all"
          aria-invalid={invalid}
        />
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <input ref={fileRef} type="file" accept=".pub,text/plain" multiple hidden onChange={(e) => { void addFiles(e.target.files); e.target.value = ''; }} />
        <Button type="button" variant="outline" size="xs" onClick={() => fileRef.current?.click()}>
          <Upload className="size-3" />
          Upload .pub
        </Button>
        <div className="w-64">
          <CredentialPicker value="" onChange={(_, c) => addFromVault(c)} kinds={['ssh_key']} placeholder="Add a key from the vault…" className="h-7 text-xs" />
        </div>
        <span className="text-[11px] text-muted-foreground">
          ed25519, RSA, ECDSA and security-key types; no <span className="font-mono">from=</span> / <span className="font-mono">command=</span> options.
        </span>
      </div>
      {rows.length > 0 && (
        <ul className="divide-y divide-border rounded-md border border-border">
          {rows.map((row) => (
            <li key={row.lineNo} className="flex items-center gap-2.5 px-3 py-2">
              {row.ok ? <KeyRound className="size-3.5 shrink-0 text-success" aria-hidden /> : <CircleAlert className="size-3.5 shrink-0 text-error" aria-hidden />}
              <span className={cn('shrink-0 text-xs font-medium', !row.ok && 'text-error-dark dark:text-error-light')}>{row.label}</span>
              <span className={cn('min-w-0 truncate font-mono text-[11px]', row.ok ? 'text-muted-foreground' : 'text-error-dark dark:text-error-light')} title={row.detail}>
                {row.detail}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
