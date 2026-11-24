/**
 * Vault mocks (Credentials screen + every credential picker): typed CRUD over
 * the shared `fleetStore.credentials` list (the cloud mocks read it too),
 * metadata from the backend's kind catalog (`fixtures/vault-metadata.ts`),
 * schema validation with the same rules the form uses, reference counts from
 * the cluster and alert-channel mocks, audited reveal and an offline test.
 * Secret values live only in this module (`secrets`), never in list rows.
 */

import { uuid } from '@/lib/mocks/fixtures/random';
import { VAULT_METADATA } from '@/lib/mocks/fixtures/vault-metadata';
import { mockAlertChannels } from '@/lib/mocks/handlers/automation';
import { fleetStore } from '@/lib/mocks/handlers/fleet';
import { recordAudit } from '@/lib/mocks/handlers/security';
import { bodyOf, created, fail, noContent, notFound, ok, paginate, registerMockHandlers, sortBy } from '@/lib/mocks/registry';
import type { Schemas } from '@/lib/mocks/types';
import { buildFields, gcpKeyInfo, initialValues, validateCredential, type FormValues } from '@/lib/vault/credential-form';

type Credential = Schemas['Credential'];
type Kind = Schemas['CredentialKindInfo'];

const KINDS: Kind[] = VAULT_METADATA.kinds ?? [];
const kindOf = (id: string | null | undefined): Kind | undefined => KINDS.find((k) => k.id === id);
const T0 = Date.now();
const iso = (ms: number) => new Date(ms).toISOString();

/** id → every field (secret ones included). */
const secrets = new Map<string, Record<string, unknown>>();
/** id → use history (newest first). */
const history = new Map<string, Schemas['CredentialUsageEntry'][]>();

function mask(value: unknown): string {
  const text = String(value ?? '');
  if (text.length <= 4) return '****';
  return `${text.slice(0, text.length > 10 ? 6 : 3)}...${text.slice(-4)}`;
}

function derived(kind: Kind, fields: Record<string, unknown>): Record<string, unknown> {
  if (kind.id === 'gcp') {
    const info = gcpKeyInfo(String(fields.service_account_json ?? ''));
    return info ? { project_id: info.projectId, client_email: info.clientEmail } : {};
  }
  if (kind.id === 'ssh_key') {
    const [type, , ...rest] = String(fields.public_key ?? '').trim().split(/\s+/);
    return { key_type: type, comment: rest.join(' ') || null, fingerprint: `SHA256:${btoa(String(fields.public_key ?? '').slice(-32)).replace(/=+$/, '').slice(0, 43)}` };
  }
  if (kind.id === 'kubernetes') {
    const ctx = /current-context:\s*(\S+)/.exec(String(fields.kubeconfig ?? ''))?.[1] ?? null;
    const server = /server:\s*(\S+)/.exec(String(fields.kubeconfig ?? ''))?.[1] ?? null;
    return { current_context: ctx, server };
  }
  return {};
}

function preview(kind: Kind, fields: Record<string, unknown>): { masked: string; summary: string | null } {
  const d = derived(kind, fields);
  switch (kind.id) {
    case 'aws':
      return { masked: mask(fields.access_key_id), summary: [fields.role_arn ? `role ${String(fields.role_arn).split('/').pop()}` : null, fields.region].filter(Boolean).join(' · ') || null };
    case 'gcp':
      return { masked: String(d.client_email ?? 'service account').slice(0, 50), summary: d.project_id ? `project ${d.project_id}` : null };
    case 'slack':
      return { masked: `hooks.slack.com/…${String(fields.webhook_url ?? '').slice(-4)}`, summary: null };
    case 'smtp':
      return { masked: mask(fields.password), summary: `${fields.username ? `${fields.username} @ ` : ''}${fields.host}:${fields.port ?? 587}` };
    case 'webhook':
      return { masked: mask(fields.hmac_secret), summary: /^https?:\/\/([^/:]+)/.exec(String(fields.url ?? ''))?.[1] ?? null };
    case 'ssh_key':
      return { masked: String(d.fingerprint ?? 'ssh key').slice(0, 50), summary: [d.key_type, d.comment].filter(Boolean).join(' · ') || null };
    case 'container_registry':
      return { masked: mask(fields.password), summary: `${fields.username} @ ${fields.registry}` };
    case 'kubernetes':
      return { masked: `kubeconfig · ${d.current_context ?? '—'}`.slice(0, 50), summary: (d.server as string | null)?.replace(/^https?:\/\//, '') ?? null };
    case 'generic':
      return { masked: mask(fields.value), summary: (fields.key as string) || null };
    default: {
      const secret = kind.secret_fields?.[0];
      return { masked: mask(secret ? fields[secret] : ''), summary: null };
    }
  }
}

/** Apply typed fields to a row: public mirror, secret names, preview. */
function applyFields(c: Credential, kind: Kind, fields: Record<string, unknown>): void {
  const clean = Object.fromEntries(Object.entries(fields).filter(([, v]) => v !== undefined && v !== null && v !== ''));
  secrets.set(c.id, clean);
  const pub: Record<string, unknown> = {};
  for (const f of kind.fields) if (!f.secret && clean[f.name] !== undefined) pub[f.name] = clean[f.name];
  Object.assign(pub, Object.fromEntries(Object.entries(derived(kind, clean)).filter(([, v]) => v !== null && v !== undefined)));
  const { masked, summary } = preview(kind, clean);
  c.provider = kind.id;
  c.kind = kind.id;
  c.type = kind.credential_type;
  c.fields = pub;
  c.secret_fields = (kind.secret_fields ?? []).filter((n) => clean[n] !== undefined);
  c.masked_value = masked;
  c.summary = summary;
  c.system = false;
}

// ─── Seeds: give the fleet fixture's credentials types + the alert channel secrets ───

const SEED_FIELDS: Record<string, Record<string, unknown>> = {
  aws: { access_key_id: 'AKIAQX7MOCKEXAMPLE7Q2D', secret_access_key: 'mock/K7MDENG/bPxRfiCYEXAMPLEKEY', region: 'ap-south-1' },
  azure: { tenant_id: '7f3c1a2b-4d5e-4f60-8a9b-0c1d2e3f0b91', client_id: '0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d', client_secret: 'mock~azure~client~secret', subscription_id: '5e6f7a8b-9c0d-4e1f-a2b3-c4d5e6f70b91' },
  gcp: { service_account_json: JSON.stringify({ type: 'service_account', project_id: 'gpu-deck-prod', private_key_id: 'abc123def456', private_key: '-----BEGIN PRIVATE KEY-----\nMOCK\n-----END PRIVATE KEY-----\n', client_email: 'gpu-deck@gpu-deck-prod.iam.gserviceaccount.com' }, null, 2) },
  digitalocean: { api_token: 'dop_v1_mock0000000000000000000000000000000000000000000000000000a91e' },
  kubernetes: { kubeconfig: 'apiVersion: v1\nkind: Config\nclusters:\n- name: eks-gpu\n  cluster: {server: "https://4F2A.gr7.ap-south-1.eks.amazonaws.com"}\ncurrent-context: eks-gpu-admin\n' },
  ssh_key: { public_key: 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIMockMockMockMockMockMockMockMockMockMock lab-deploy@example' },
  huggingface: { token: 'hf_mockMockMockMockMockWq3' },
};

function seed(): void {
  for (const c of fleetStore.credentials) {
    if (c.kind !== undefined) continue;
    const kindId = c.provider ?? (c.type === 'ssh_key' ? 'ssh_key' : null);
    const kind = kindOf(kindId);
    if (kind && SEED_FIELDS[kind.id]) applyFields(c, kind, SEED_FIELDS[kind.id]);
    else c.kind = null;
  }
  const extra: { key: string; name: string; kind: string; fields: Record<string, unknown>; days: number; used: number }[] = [
    { key: 'credential:slack-gpu-ops', name: 'slack-gpu-ops', kind: 'slack', fields: { webhook_url: 'https://hooks.slack.com/services/T0MOCK/B0MOCK/mockSlackHook9Qz' }, days: 20, used: 214 },
    { key: 'credential:smtp-password', name: 'smtp-office365', kind: 'smtp', fields: { host: 'smtp.office365.com', port: 587, username: 'gpudeck@example.com', password: 'mock-mail-pass', from: 'gpudeck@example.com', starttls: true }, days: 20, used: 37 },
    { key: 'credential:finops-signing', name: 'finops-webhook', kind: 'webhook', fields: { url: 'https://finops.example.com/hooks/gpu-deck', hmac_secret: 'mock-signing-secret-0123456789' }, days: 12, used: 9 },
  ];
  for (const e of extra) {
    const id = uuid(e.key);
    if (fleetStore.credentials.some((c) => c.id === id)) continue;
    const kind = kindOf(e.kind);
    if (!kind) continue;
    const c: Credential = { id, name: e.name, type: kind.credential_type, status: 'active', usage_count: e.used, last_used_at: iso(T0 - 3_600_000), created_at: iso(T0 - e.days * 86_400_000), updated_at: iso(T0 - e.days * 86_400_000), description: null };
    applyFields(c, kind, e.fields);
    fleetStore.credentials.push(c);
  }
}
seed();

// ─── Views ────────────────────────────────────────────────────────────────────

function references(id: string): Schemas['CredentialReference'][] {
  const refs: Schemas['CredentialReference'][] = [];
  for (const cl of fleetStore.clusters) {
    const cfg = (cl.config ?? {}) as Record<string, unknown>;
    if (cl.credential_id === id) refs.push({ resource_type: 'cluster', resource_id: cl.id, name: cl.name, field: 'credential_id', detail: cl.type });
    else if (cfg.ssh_key_credential_id === id) refs.push({ resource_type: 'cluster', resource_id: cl.id, name: cl.name, field: 'config.ssh_key_credential_id', detail: cl.type });
    else if (cfg.kubeconfig_credential_id === id) refs.push({ resource_type: 'cluster', resource_id: cl.id, name: cl.name, field: 'config.kubeconfig_credential_id', detail: cl.type });
  }
  for (const ch of mockAlertChannels) {
    if (ch.credential_id === id) refs.push({ resource_type: 'alert_channel', resource_id: ch.id, name: ch.name, field: 'credential_id', detail: ch.type });
  }
  return refs;
}

function counts(id: string): Schemas['CredentialUsageCounts'] {
  const refs = references(id);
  const n = (t: Schemas['CredentialReference']['resource_type']) => refs.filter((r) => r.resource_type === t).length;
  return { clusters: n('cluster'), alert_channels: n('alert_channel'), models: n('model'), cloud_instances: n('cloud_instance'), total: refs.length };
}

function defaults(c: Credential): string[] {
  const out: string[] = [];
  for (const d of VAULT_METADATA.defaults ?? []) {
    if (d.name && d.name.toLowerCase() === c.name.toLowerCase()) out.push(d.used_for);
  }
  if (!out.length && c.kind && ['huggingface', 'modelscope'].includes(c.kind)) {
    const newest = sortBy(fleetStore.credentials.filter((x) => x.kind === c.kind && x.status === 'active'), '-created_at')[0];
    const named = (VAULT_METADATA.defaults ?? []).find((d) => d.kind === c.kind);
    const namedExists = named?.name && fleetStore.credentials.some((x) => x.name.toLowerCase() === named.name?.toLowerCase());
    if (newest?.id === c.id && !namedExists && named) out.push(named.used_for);
  }
  return out;
}

function view(c: Credential): Credential {
  return { ...c, used_by: counts(c.id), default_for: defaults(c) };
}

function byId(id: string): Credential | undefined {
  return fleetStore.credentials.find((c) => c.id === id);
}

function recordUse(id: string, purpose: string, admin = true): void {
  const c = byId(id);
  const at = new Date().toISOString();
  if (c) {
    c.usage_count = (c.usage_count ?? 0) + 1;
    c.last_used_at = at;
  }
  history.set(id, [{ used_at: at, purpose, used_by: admin ? uuid('admin') : null, used_by_username: admin ? 'admin' : null }, ...(history.get(id) ?? [])].slice(0, 20));
}

function invalid(kind: Kind, fields: Record<string, unknown>, editing: boolean, stored: string[] = []): string | null {
  const values: FormValues = { ...initialValues(kind, null) };
  for (const [k, v] of Object.entries(fields)) values[k] = typeof v === 'boolean' ? v : String(v ?? '');
  const errors = validateCredential(kind, values, { editing, storedSecrets: stored });
  const list = Object.values(errors);
  return list.length ? `Invalid ${kind.label}: ${list.join('; ')}` : null;
}

// ─── Handlers ─────────────────────────────────────────────────────────────────

registerMockHandlers('vault', [
  {
    method: 'GET',
    path: '/vault',
    handle: (req) => {
      const providers = (req.query.get('provider') ?? '').split(',').map((p) => p.trim()).filter(Boolean);
      const status = req.query.get('status');
      const type = req.query.get('type');
      const search = (req.query.get('search') ?? '').toLowerCase();
      const list = fleetStore.credentials.filter(
        (c) =>
          (!providers.length || providers.includes(c.provider ?? '')) &&
          (!status || c.status === status) &&
          (!type || c.type === type) &&
          (!search || [c.name, c.description ?? '', c.provider ?? ''].some((s) => s.toLowerCase().includes(search))),
      );
      return paginate(sortBy(list as unknown as Record<string, unknown>[], req.query.get('sort')).map((c) => view(c as unknown as Credential)), req.query, 50);
    },
  },
  {
    method: 'POST',
    path: '/vault',
    handle: (req) => {
      const body = bodyOf<Schemas['CredentialCreate']>(req);
      const name = String(body.name ?? '').trim();
      if (!name) return fail(422, 'VALIDATION_ERROR', 'name is required');
      if (fleetStore.credentials.some((c) => c.name.toLowerCase() === name.toLowerCase())) return fail(409, 'DUPLICATE_RESOURCE', 'Credential with this name already exists');
      const kind = kindOf(body.provider ?? 'generic');
      if (!kind) return fail(422, 'VALIDATION_ERROR', `Unknown credential type '${body.provider}'`);
      const fields = (body.fields ?? (body.value ? { [kind.secret_fields?.[0] ?? 'value']: body.value } : {})) as Record<string, unknown>;
      const problem = invalid(kind, fields, false);
      if (problem) return fail(422, 'CREDENTIAL_INVALID', problem);
      const now = new Date().toISOString();
      const c: Credential = { id: uuid(`credential:${name}:${now}`), name, type: kind.credential_type, status: 'active', description: body.description ?? null, expires_at: body.expires_at ?? null, usage_count: 0, last_used_at: null, metadata: body.metadata ?? {}, created_at: now, updated_at: now };
      applyFields(c, kind, fields);
      fleetStore.credentials.unshift(c);
      recordAudit({ eventType: 'credential.created', resourceType: 'credential', resourceId: c.id, resourceName: c.name, after: { kind: kind.id, public_fields: Object.keys(c.fields ?? {}) } });
      return created(view(c));
    },
  },
  { method: 'GET', path: '/vault/metadata', handle: () => ok(VAULT_METADATA) },
  {
    method: 'GET',
    path: '/vault/stats',
    handle: () => {
      const list = fleetStore.credentials.filter((c) => !c.system);
      const soon = Date.now() + 7 * 86_400_000;
      const byKind: Record<string, number> = {};
      for (const c of list) byKind[c.provider ?? 'untyped'] = (byKind[c.provider ?? 'untyped'] ?? 0) + 1;
      return ok({
        total: list.length,
        active: list.filter((c) => c.status === 'active').length,
        expiring_soon: list.filter((c) => c.status === 'active' && c.expires_at && Date.parse(c.expires_at) > Date.now() && Date.parse(c.expires_at) <= soon).length,
        expired: list.filter((c) => c.status === 'expired').length,
        by_kind: byKind,
      });
    },
  },
  {
    method: 'GET',
    path: '/vault/:credential_id',
    handle: (req) => {
      const c = byId(req.params.credential_id);
      return c ? ok(view(c)) : notFound('Credential');
    },
  },
  {
    method: 'PATCH',
    path: '/vault/:credential_id',
    handle: (req) => {
      const c = byId(req.params.credential_id);
      if (!c) return notFound('Credential');
      const body = bodyOf<Schemas['CredentialUpdate']>(req);
      if (body.name && body.name !== c.name && fleetStore.credentials.some((x) => x.id !== c.id && x.name.toLowerCase() === body.name?.toLowerCase())) return fail(409, 'DUPLICATE_RESOURCE', 'Credential with this name already exists');
      const kind = kindOf(c.kind);
      if (body.provider && body.provider !== c.provider) return fail(422, 'VALIDATION_ERROR', "A credential's type cannot change; create a new one");
      if (kind && (body.fields || body.value)) {
        const incoming = (body.fields ?? { [kind.secret_fields?.[0] ?? 'value']: body.value }) as Record<string, unknown>;
        const merged: Record<string, unknown> = { ...(secrets.get(c.id) ?? {}) };
        for (const [k, v] of Object.entries(incoming)) {
          const spec = kind.fields.find((f) => f.name === k);
          if (spec?.secret && (v === '' || v === null || v === undefined)) continue;
          if (v === '' || v === null) delete merged[k];
          else merged[k] = v;
        }
        const problem = invalid(kind, merged, false);
        if (problem) return fail(422, 'CREDENTIAL_INVALID', problem);
        applyFields(c, kind, buildFields(kind, Object.fromEntries(Object.entries(merged).map(([k, v]) => [k, typeof v === 'boolean' ? v : String(v)])), { editing: false }));
        c.status = 'active';
      }
      if (body.name) c.name = body.name;
      if (body.description !== undefined) c.description = body.description;
      if (body.expires_at !== undefined) c.expires_at = body.expires_at;
      c.updated_at = new Date().toISOString();
      recordAudit({ eventType: 'credential.updated', resourceType: 'credential', resourceId: c.id, resourceName: c.name, metadata: { value_changed: !!(body.fields || body.value) } });
      return ok(view(c));
    },
  },
  {
    method: 'DELETE',
    path: '/vault/:credential_id',
    handle: (req) => {
      const idx = fleetStore.credentials.findIndex((c) => c.id === req.params.credential_id);
      if (idx < 0) return notFound('Credential');
      const c = fleetStore.credentials[idx];
      const refs = references(c.id);
      if (refs.length) {
        const n = counts(c.id);
        const used = [n.clusters ? `${n.clusters} clusters` : '', n.alert_channels ? `${n.alert_channels} alert channels` : ''].filter(Boolean).join(', ');
        return fail(409, 'CREDENTIAL_IN_USE', `Credential is in use by ${used} (${refs.map((r) => `${r.resource_type} ${r.name}`).join(', ')})`);
      }
      fleetStore.credentials.splice(idx, 1);
      secrets.delete(c.id);
      recordAudit({ eventType: 'credential.deleted', resourceType: 'credential', resourceId: c.id, resourceName: c.name });
      return noContent();
    },
  },
  {
    method: 'POST',
    path: '/vault/:credential_id/reveal',
    handle: (req) => {
      const c = byId(req.params.credential_id);
      if (!c) return notFound('Credential');
      if (c.system) return fail(403, 'CREDENTIAL_SYSTEM', 'System credentials are managed by GPU Deck');
      const fields = secrets.get(c.id) ?? { value: 'mock-secret-value' };
      const kind = kindOf(c.kind);
      const value = kind && kind.id !== 'aws' && kind.id !== 'azure' ? String(fields[kind.secret_fields?.[0] ?? 'public_key'] ?? fields.public_key ?? '') : JSON.stringify(fields);
      recordUse(c.id, 'reveal');
      recordAudit({ eventType: 'credential.revealed', resourceType: 'credential', resourceId: c.id, resourceName: c.name, metadata: { masked_value: c.masked_value, kind: c.kind } });
      return ok({ id: c.id, value, fields });
    },
  },
  {
    method: 'POST',
    path: '/vault/:credential_id/test',
    handle: (req) => {
      const c = byId(req.params.credential_id);
      if (!c) return notFound('Credential');
      const kind = kindOf(c.kind);
      const fields = secrets.get(c.id) ?? {};
      const problem = kind ? invalid(kind, fields, false) : null;
      const checks: Schemas['CredentialCheck'][] = [
        { name: 'decrypt', ok: true, message: 'Decrypts with VAULT_KEYS' },
        c.status === 'active' ? { name: 'status', ok: true, message: 'Active' } : { name: 'status', ok: false, message: `Status is ${c.status}` },
        kind ? { name: 'schema', ok: !problem, message: problem ?? `Valid ${kind.label}` } : { name: 'schema', ok: true, message: 'Untyped credential: no schema to check. Edit it to pick a type.' },
      ];
      recordUse(c.id, 'test');
      const ok_ = checks.every((x) => x.ok);
      recordAudit({ eventType: 'credential.tested', resourceType: 'credential', resourceId: c.id, resourceName: c.name, outcome: ok_ ? 'success' : 'failure' });
      return ok({ id: c.id, ok: ok_, kind: kind?.id ?? null, checks, cloud_provider: kind?.cloud_provider ?? null, cloud_check_path: kind?.cloud_provider ? `/cloud/providers/${kind.cloud_provider}/check-credential` : null, tested_at: new Date().toISOString() });
    },
  },
  {
    method: 'GET',
    path: '/vault/:credential_id/usage',
    handle: (req) => {
      const c = byId(req.params.credential_id);
      if (!c) return notFound('Credential');
      let past = history.get(c.id);
      if (!past) {
        const purposes = c.kind === 'aws' ? ['cloud:aws:catalog', 'cloud:aws:provision'] : c.kind === 'huggingface' ? ['huggingface:model download', 'huggingface:catalog'] : ['alert delivery'];
        past = Array.from({ length: Math.min(5, c.usage_count ?? 0) }, (_, i) => ({ used_at: iso(T0 - (i + 1) * 3 * 3_600_000), purpose: purposes[i % purposes.length], used_by: null, used_by_username: null }));
        history.set(c.id, past);
      }
      return ok({ credential_id: c.id, used_by: counts(c.id), references: references(c.id), default_for: defaults(c), history: past });
    },
  },
]);
