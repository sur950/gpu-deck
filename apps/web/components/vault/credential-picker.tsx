'use client';

import { useMemo, useState } from 'react';
import Link from 'next/link';
import { Plus } from 'lucide-react';
import { Combobox, type ComboboxFooterItem, type ComboboxOption } from '@/components/shared/Combobox';
import { CredentialDialog } from '@/components/vault/credential-dialog';
import { KindGlyph } from '@/components/vault/kind-icon';
import { useCredentialKinds, useCredentialOptionsAll } from '@/hooks/use-vault';
import { isCompatible } from '@/lib/vault/credential-form';
import type { Credential } from '@/types/vault';

const ADD = '__add_credential__';
const NONE = '__none__';

export interface CredentialPickerProps {
  /** Selected credential id ('' = none). */
  value: string;
  onChange: (id: string, credential?: Credential) => void;
  /** Kinds this consumer accepts (e.g. ['aws'] for an AWS cluster). */
  kinds: string[];
  placeholder?: string;
  /** Offer a "None" choice (optional credentials). */
  allowNone?: boolean;
  noneLabel?: string;
  disabled?: boolean;
  className?: string;
}

function option(c: Credential): ComboboxOption<Credential> {
  return { value: c.id, searchTerms: [c.name, c.provider ?? '', c.summary ?? '', c.description ?? ''], data: c };
}

/**
 * Vault credential picker: only credentials of compatible kinds, with
 * "+ Add credential" opening the typed dialog pre-filtered to those kinds —
 * the new credential is selected on save. Empty pickers explain what is needed
 * and link to the Credentials page.
 */
export function CredentialPicker({ value, onChange, kinds: accepted, placeholder, allowNone, noneLabel = 'None', disabled, className }: CredentialPickerProps) {
  const all = useCredentialOptionsAll();
  const { kinds, byId } = useCredentialKinds();
  const [adding, setAdding] = useState(false);
  const options = useMemo(() => (all.data ?? []).filter((c) => isCompatible(c, accepted, kinds)).map(option), [all.data, accepted, kinds]);
  const label = accepted.map((k) => byId[k]?.label ?? k.replace(/_/g, ' ')).join(' or ');

  const footer: ComboboxFooterItem[] = [
    {
      value: ADD,
      label: (
        <span className="flex items-center gap-1.5 not-italic font-medium text-foreground">
          <Plus className="size-3.5" /> Add {accepted.length === 1 ? label : 'credential'}…
        </span>
      ),
      returnFocus: false,
      onSelect: () => setAdding(true),
    },
  ];
  if (allowNone) footer.push({ value: NONE, label: noneLabel, selected: value === '', selectedLabel: <span className="text-muted-foreground">{noneLabel}</span>, onSelect: () => onChange('') });

  return (
    <div className="space-y-1.5">
      <Combobox<Credential>
        options={options}
        value={value}
        onSelect={(id) => onChange(id, options.find((o) => o.value === id)?.data)}
        renderOption={(c) => (
          <>
            <KindGlyph kind={c.kind ?? c.provider} category={byId[c.kind ?? '']?.category ?? 'legacy'} size="xs" />
            <span className="truncate">{c.name}</span>
            <span className="ml-auto truncate pl-2 font-mono text-[10.5px] text-muted-foreground">{c.summary ?? c.masked_value}</span>
          </>
        )}
        renderSelected={(c) => (
          <>
            <KindGlyph kind={c.kind ?? c.provider} category={byId[c.kind ?? '']?.category ?? 'legacy'} size="xs" />
            <span className="truncate">{c.name}</span>
            {c.masked_value && <span className="truncate font-mono text-[11px] text-muted-foreground">{c.masked_value}</span>}
          </>
        )}
        placeholder={placeholder ?? (options.length ? 'Pick from the vault…' : 'Nothing in the vault yet — add one')}
        searchPlaceholder="Search credentials…"
        emptyText={`No ${label} in the vault.`}
        loading={all.isLoading}
        disabled={disabled}
        footerItems={footer}
        className={className}
      />
      {!all.isLoading && options.length === 0 && (
        <p className="text-[11px] text-muted-foreground">
          Nothing to pick yet: add a {label} here, or manage all secrets on the{' '}
          <Link href="/credentials" className="font-medium text-foreground underline-offset-2 hover:underline">
            Credentials
          </Link>{' '}
          page.
        </p>
      )}
      <CredentialDialog open={adding} onOpenChange={setAdding} allowedKinds={accepted} onSaved={(c) => onChange(c.id, c)} />
    </div>
  );
}
