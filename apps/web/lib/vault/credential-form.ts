/**
 * Typed credential form logic (pure, unit-tested in tests/unit/vault-form.test.ts).
 *
 * The dialog is driven by a kind from GET /vault/metadata: its fields carry
 * the same rules as the backend JSON Schema (required, pattern, lengths,
 * integer ranges) plus the semantic checks the backend runs (GCP key JSON,
 * SSH public key, kubeconfig). Secret fields are write-only: on edit an empty
 * secret means "keep the stored value", so the payload leaves it out.
 */

import { parseSshKey } from '@/lib/ssh-keys';
import type { Credential, CredentialKind, CredentialKindField } from '@/types/vault';

export type FieldValue = string | boolean;
export type FormValues = Record<string, FieldValue>;
export type FormErrors = Record<string, string>;

export interface FormContext {
  /** Editing an existing credential (empty secrets keep the stored value). */
  editing: boolean;
  /** Secret fields that already hold a value (Credential.secret_fields). */
  storedSecrets?: string[];
}

function fieldDefault(field: CredentialKindField): FieldValue {
  if (field.input === 'boolean') return field.default === undefined || field.default === null ? false : Boolean(field.default);
  if (field.default === undefined || field.default === null) return '';
  return String(field.default);
}

/** Form state for a kind: defaults, or the credential's public fields (secrets always start empty). */
export function initialValues(kind: CredentialKind, credential?: Credential | null): FormValues {
  const out: FormValues = {};
  const stored = (credential?.fields ?? {}) as Record<string, unknown>;
  for (const field of kind.fields) {
    if (field.secret) {
      out[field.name] = '';
      continue;
    }
    const value = stored[field.name];
    if (field.input === 'boolean') out[field.name] = value === undefined || value === null ? fieldDefault(field) : Boolean(value);
    else out[field.name] = value === undefined || value === null ? (credential ? '' : fieldDefault(field)) : String(value);
  }
  return out;
}

function text(value: FieldValue | undefined, field: CredentialKindField): string {
  if (typeof value !== 'string') return '';
  return field.input === 'textarea' || field.input === 'json' ? value.replace(/^\n+|\n+$/g, '') : value.trim();
}

/** One field against its schema rules; `null` when valid. */
export function validateField(field: CredentialKindField, value: FieldValue | undefined, ctx: FormContext): string | null {
  if (field.input === 'boolean') return null;
  const v = text(value, field);
  if (!v.trim()) {
    const keepsStored = field.secret && ctx.editing && (ctx.storedSecrets ?? []).includes(field.name);
    return field.required && !keepsStored ? `${field.label} is required` : null;
  }
  if (field.input === 'number') {
    if (!/^-?\d+$/.test(v)) return `${field.label} must be a whole number`;
    const n = Number(v);
    if (field.minimum !== null && field.minimum !== undefined && n < field.minimum) return `${field.label} must be at least ${field.minimum}`;
    if (field.maximum !== null && field.maximum !== undefined && n > field.maximum) return `${field.label} must be at most ${field.maximum}`;
    return null;
  }
  if (field.min_length && v.length < field.min_length) return `${field.label} needs at least ${field.min_length} characters`;
  if (field.max_length && v.length > field.max_length) return `${field.label} is longer than ${field.max_length} characters`;
  if (field.input === 'json') {
    try {
      const parsed: unknown = JSON.parse(v);
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return `${field.label} must be a JSON object`;
    } catch (error) {
      return `${field.label} is not valid JSON${error instanceof Error ? ` (${error.message})` : ''}`;
    }
  }
  if (field.pattern) {
    let re: RegExp | null = null;
    try {
      re = new RegExp(field.pattern);
    } catch {
      re = null;
    }
    if (re && !re.test(v)) return `${field.label}: ${field.pattern_hint ?? 'invalid format'}`;
  }
  return null;
}

/** Facts derived from a GCP key (shown live in the form; the backend stores the same). */
export function gcpKeyInfo(raw: string): { projectId: string | null; clientEmail: string | null } | null {
  try {
    const key = JSON.parse(raw) as Record<string, unknown>;
    if (!key || typeof key !== 'object') return null;
    return {
      projectId: typeof key.project_id === 'string' ? key.project_id : null,
      clientEmail: typeof key.client_email === 'string' ? key.client_email : null,
    };
  } catch {
    return null;
  }
}

/** The backend's semantic checks for kinds whose single field carries a structured document. */
export function kindErrors(kind: CredentialKind, values: FormValues): FormErrors {
  const errors: FormErrors = {};
  if (kind.id === 'gcp') {
    const raw = text(values.service_account_json, { input: 'json' } as CredentialKindField);
    if (raw.trim()) {
      try {
        const key = JSON.parse(raw) as Record<string, unknown>;
        const missing = ['project_id', 'client_email', 'private_key'].filter((k) => !key?.[k]);
        if (key?.type !== 'service_account') errors.service_account_json = 'Not a service account key ("type": "service_account" missing)';
        else if (missing.length) errors.service_account_json = `The key is missing ${missing.join(', ')}`;
        else if (!String(key.private_key).includes('PRIVATE KEY')) errors.service_account_json = '"private_key" is not a PEM private key';
      } catch {
        /* reported by validateField */
      }
    }
  }
  if (kind.id === 'ssh_key') {
    const raw = typeof values.public_key === 'string' ? values.public_key.trim() : '';
    if (raw) {
      const lines = raw.split(/\r?\n/).filter((l) => l.trim());
      if (/PRIVATE KEY/.test(raw)) errors.public_key = 'This is a private key — paste the public key (.pub) instead';
      else if (lines.length > 1) errors.public_key = 'One key per credential (a single line from the .pub file)';
      else {
        const parsed = parseSshKey(lines[0]);
        if (!parsed.ok) errors.public_key = parsed.error;
      }
    }
  }
  if (kind.id === 'kubernetes') {
    const raw = typeof values.kubeconfig === 'string' ? values.kubeconfig : '';
    if (raw.trim() && !/^\s*clusters\s*:/m.test(raw)) errors.kubeconfig = 'This does not look like a kubeconfig (no clusters:)';
  }
  if (kind.id === 'webhook') {
    const url = typeof values.url === 'string' ? values.url.trim() : '';
    if (url && !/^https?:\/\/[^/\s:?#]+/i.test(url)) errors.url = 'URL must be an http(s) URL with a host';
  }
  return errors;
}

/** Every field + the kind checks; empty object when the form can be submitted. */
export function validateCredential(kind: CredentialKind, values: FormValues, ctx: FormContext): FormErrors {
  const errors: FormErrors = {};
  for (const field of kind.fields) {
    const message = validateField(field, values[field.name], ctx);
    if (message) errors[field.name] = message;
  }
  for (const [name, message] of Object.entries(kindErrors(kind, values))) {
    if (!errors[name]) errors[name] = message;
  }
  return errors;
}

/**
 * `fields` for POST /vault (create) or PATCH /vault/{id} (edit). Create leaves
 * empty optional fields out; edit sends '' for cleared public fields (the
 * backend removes them) and leaves out empty secrets (stored value kept).
 */
export function buildFields(kind: CredentialKind, values: FormValues, ctx: FormContext): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const field of kind.fields) {
    const raw = values[field.name];
    if (field.input === 'boolean') {
      out[field.name] = Boolean(raw);
      continue;
    }
    const v = text(raw, field);
    if (!v.trim()) {
      if (ctx.editing && !field.secret) out[field.name] = '';
      continue;
    }
    out[field.name] = field.input === 'number' ? Number(v) : v;
  }
  return out;
}

/** True when a credential can be used where `accepted` kinds are expected (mirrors the backend:
 * untyped legacy secrets pass when their category matches). */
export function isCompatible(credential: Pick<Credential, 'provider' | 'kind' | 'type' | 'system'>, accepted: string[], kinds: CredentialKind[]): boolean {
  if (credential.provider && accepted.includes(credential.provider)) return true;
  if (credential.kind || credential.system) return false;
  const categories = kinds.filter((k) => accepted.includes(k.id)).map((k) => k.credential_type);
  return categories.includes(credential.type) || credential.type === 'other';
}

/** A free name for a new credential of `kind` ("aws", "aws-2", …). */
export function suggestName(kind: Pick<CredentialKind, 'id'>, taken: string[]): string {
  const base = kind.id.replace(/_/g, '-');
  const lower = new Set(taken.map((n) => n.toLowerCase()));
  if (!lower.has(base)) return base;
  for (let i = 2; i < 1000; i += 1) {
    if (!lower.has(`${base}-${i}`)) return `${base}-${i}`;
  }
  return `${base}-${Date.now()}`;
}

export const NAME_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._:@-]{0,254}$/;
