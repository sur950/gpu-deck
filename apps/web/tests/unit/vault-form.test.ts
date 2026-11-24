import { describe, expect, it } from 'vitest';
import { VAULT_METADATA } from '@/lib/mocks/fixtures/vault-metadata';
import { buildFields, gcpKeyInfo, initialValues, isCompatible, suggestName, validateCredential, validateField } from '@/lib/vault/credential-form';
import type { Credential, CredentialKind } from '@/types/vault';
import { PICKER_KINDS, usageSummary } from '@/types/vault';

// The kind catalog as GET /vault/metadata returns it (generated from the backend's types.py).
const KINDS = VAULT_METADATA.kinds ?? [];
const kind = (id: string): CredentialKind => {
  const k = KINDS.find((x) => x.id === id);
  if (!k) throw new Error(`no kind ${id}`);
  return k;
};
const create = { editing: false };
const ED25519 = 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIO0iVzQbxv9so2DZfE1htSnq1ygGfsoJktBMl93qIcAs alice@laptop';
const GCP_KEY = JSON.stringify({ type: 'service_account', project_id: 'gpu-proj', private_key: '-----BEGIN PRIVATE KEY-----\nMII\n-----END PRIVATE KEY-----\n', client_email: 'sa@gpu-proj.iam.gserviceaccount.com' });

function credential(over: Partial<Credential>): Credential {
  return { id: 'c1', name: 'c1', type: 'other', status: 'active', created_at: '2026-11-18T00:00:00Z', ...over } as Credential;
}

describe('kind catalog', () => {
  it('covers every kind the pickers ask for, with secret fields marked', () => {
    const ids = KINDS.map((k) => k.id);
    for (const accepted of Object.values(PICKER_KINDS)) for (const id of accepted) expect(ids).toContain(id);
    expect(kind('aws').secret_fields).toEqual(expect.arrayContaining(['access_key_id', 'secret_access_key']));
    expect(kind('ssh_key').secret_fields).toEqual([]);
    expect(VAULT_METADATA.pickers?.['cluster.cloud_aws']).toEqual(['aws']);
  });
});

describe('initialValues', () => {
  it('starts from defaults on create and from public fields on edit; secrets always empty', () => {
    expect(initialValues(kind('smtp'))).toMatchObject({ host: '', port: '587', password: '', starttls: true });
    const stored = credential({ kind: 'smtp', provider: 'smtp', fields: { host: 'smtp.x.com', port: 465, starttls: false }, secret_fields: ['password'] });
    expect(initialValues(kind('smtp'), stored)).toMatchObject({ host: 'smtp.x.com', port: '465', password: '', starttls: false, username: '' });
  });
});

describe('validateCredential', () => {
  it('requires the required fields and explains patterns with the hint', () => {
    const errors = validateCredential(kind('aws'), { access_key_id: 'bad key', secret_access_key: '' }, create);
    expect(errors.secret_access_key).toBe('Secret access key is required');
    expect(errors.access_key_id).toMatch(/^Access key ID: Upper-case letters/);
    expect(validateCredential(kind('aws'), { access_key_id: 'AKIAIOSFODNN7EXAMPLE', secret_access_key: 'wJalrXUtnFEMI/K7MDENG', role_arn: 'nope' }, create).role_arn).toContain('arn:aws:iam');
    expect(validateCredential(kind('aws'), { access_key_id: 'AKIAIOSFODNN7EXAMPLE', secret_access_key: 'wJalrXUtnFEMI/K7MDENG' }, create)).toEqual({});
  });

  it('keeps stored secrets on edit (empty = unchanged)', () => {
    const values = { token: '' };
    expect(validateCredential(kind('huggingface'), values, create).token).toBe('Access token is required');
    expect(validateCredential(kind('huggingface'), values, { editing: true, storedSecrets: ['token'] })).toEqual({});
  });

  it('checks integers, JSON and the GCP key shape', () => {
    expect(validateField(kind('smtp').fields.find((f) => f.name === 'port')!, '70000', create)).toBe('Port must be at most 65535');
    expect(validateField(kind('smtp').fields.find((f) => f.name === 'port')!, '5.5', create)).toBe('Port must be a whole number');
    expect(validateCredential(kind('gcp'), { service_account_json: '{nope' }, create).service_account_json).toMatch(/not valid JSON/);
    expect(validateCredential(kind('gcp'), { service_account_json: '{"type":"user"}' }, create).service_account_json).toMatch(/service account key/);
    expect(validateCredential(kind('gcp'), { service_account_json: GCP_KEY }, create)).toEqual({});
    expect(gcpKeyInfo(GCP_KEY)).toEqual({ projectId: 'gpu-proj', clientEmail: 'sa@gpu-proj.iam.gserviceaccount.com' });
  });

  it('accepts one OpenSSH public key and refuses private keys', () => {
    expect(validateCredential(kind('ssh_key'), { public_key: ED25519 }, create)).toEqual({});
    expect(validateCredential(kind('ssh_key'), { public_key: '-----BEGIN OPENSSH PRIVATE KEY-----\nb3Bl\n-----END OPENSSH PRIVATE KEY-----' }, create).public_key).toMatch(/private key/);
    expect(validateCredential(kind('ssh_key'), { public_key: `${ED25519}\n${ED25519}` }, create).public_key).toMatch(/One key/);
  });

  it('rejects non-kubeconfig YAML and non-http webhook URLs', () => {
    expect(validateCredential(kind('kubernetes'), { kubeconfig: 'hello: world' }, create).kubeconfig).toMatch(/kubeconfig/);
    expect(validateCredential(kind('webhook'), { url: 'ftp://x', hmac_secret: '0123456789abcdef0' }, create).url).toBeTruthy();
  });
});

describe('buildFields', () => {
  it('create: drops empty optionals, converts numbers and booleans', () => {
    const values = { ...initialValues(kind('smtp')), host: ' smtp.x.com ', password: 'pw', port: '465' };
    expect(buildFields(kind('smtp'), values, create)).toEqual({ host: 'smtp.x.com', port: 465, password: 'pw', starttls: true });
  });

  it('edit: empty secrets are left out (kept), cleared public fields are sent empty (removed)', () => {
    const values = { access_key_id: '', secret_access_key: '', session_token: '', role_arn: '', external_id: '', region: 'eu-west-1' };
    expect(buildFields(kind('aws'), values, { editing: true, storedSecrets: ['access_key_id', 'secret_access_key'] })).toEqual({ role_arn: '', external_id: '', region: 'eu-west-1' });
  });
});

describe('pickers', () => {
  it('only offers compatible credentials; untyped legacy secrets pass by category', () => {
    expect(isCompatible(credential({ provider: 'aws', kind: 'aws', type: 'cloud_account' }), ['aws'], KINDS)).toBe(true);
    expect(isCompatible(credential({ provider: 'azure', kind: 'azure', type: 'service_account' }), ['aws'], KINDS)).toBe(false);
    expect(isCompatible(credential({ provider: 'generic', kind: 'generic', type: 'other' }), ['slack'], KINDS)).toBe(false);
    expect(isCompatible(credential({ provider: 'alerts', kind: null, type: 'webhook' }), ['slack'], KINDS)).toBe(true);
    expect(isCompatible(credential({ provider: 'internal_ca', kind: null, type: 'ca_key', system: true }), ['aws'], KINDS)).toBe(false);
  });

  it('suggests a free name and summarises usage', () => {
    expect(suggestName({ id: 'ssh_key' }, [])).toBe('ssh-key');
    expect(suggestName({ id: 'aws' }, ['aws', 'AWS-2'])).toBe('aws-3');
    expect(usageSummary({ clusters: 2, alert_channels: 1, models: 0, cloud_instances: 0, total: 3 })).toBe('2 clusters · 1 alert channel');
    expect(usageSummary({ total: 0 })).toBe('');
  });
});
