/**
 * Vault (Credentials) types — contract shapes from admin.yaml plus the UI
 * labels for kinds and usage. A credential's kind is `provider` (aws, azure,
 * gcp, digitalocean, huggingface, modelscope, slack, smtp, webhook, ssh_key,
 * container_registry, kubernetes, generic); its fields come from the type
 * catalog at GET /vault/metadata.
 */

import type { components } from '@/lib/api/generated/admin';

type S = components['schemas'];

export type Credential = S['Credential'];
export type CredentialCreate = S['CredentialCreate'];
export type CredentialUpdate = S['CredentialUpdate'];
export type CredentialStats = S['CredentialStats'];
export type CredentialRevealed = S['CredentialRevealed'];
export type CredentialTestResult = S['CredentialTestResult'];
export type CredentialUsage = S['CredentialUsage'];
export type CredentialReference = S['CredentialReference'];
export type CredentialUsageCounts = S['CredentialUsageCounts'];
export type CredentialKind = S['CredentialKindInfo'];
export type CredentialKindField = S['CredentialKindField'];
export type CredentialCategory = S['CredentialKindInfo']['category'];
export type CredentialStatus = S['credential_status'];
export type VaultMetadata = S['VaultMetadata'];

/** Known kind ids (the backend may add more; unknown ids render as generic). */
export type CredentialKindId =
  | 'aws'
  | 'azure'
  | 'gcp'
  | 'digitalocean'
  | 'huggingface'
  | 'modelscope'
  | 'slack'
  | 'smtp'
  | 'webhook'
  | 'ssh_key'
  | 'container_registry'
  | 'kubernetes'
  | 'generic';

export interface CredentialListParams {
  page?: number;
  pageSize?: number;
  search?: string;
  /** One kind or a comma-separated list. */
  provider?: string;
  status?: CredentialStatus | '';
  sort?: string;
  includeSystem?: boolean;
}

export const CATEGORY_LABELS: Record<CredentialCategory, string> = {
  cloud: 'Cloud accounts',
  model_source: 'Model sources',
  notification: 'Notifications',
  access: 'Access',
  other: 'Other',
};

export const CATEGORY_ORDER: CredentialCategory[] = ['cloud', 'model_source', 'notification', 'access', 'other'];

/** Picker ids → accepted kinds (mirrors VaultMetadata.pickers; used before metadata loads). */
export const PICKER_KINDS: Record<string, CredentialKindId[]> = {
  'cluster.cloud_aws': ['aws'],
  'cluster.cloud_azure': ['azure'],
  'cluster.cloud_gcp': ['gcp'],
  'cluster.cloud_do': ['digitalocean'],
  'cluster.kubernetes': ['kubernetes'],
  'cluster.bare_metal': ['ssh_key'],
  'cluster.vm': ['ssh_key'],
  'cluster.ssh_key': ['ssh_key'],
  'alert_channel.slack': ['slack'],
  'alert_channel.email': ['smtp'],
  'alert_channel.webhook': ['webhook'],
  'model.huggingface': ['huggingface'],
  'model.modelscope': ['modelscope'],
  'gpu_instance.authorized_keys': ['ssh_key'],
};

const USAGE_NOUNS: [keyof CredentialUsageCounts, string, string][] = [
  ['clusters', 'cluster', 'clusters'],
  ['alert_channels', 'alert channel', 'alert channels'],
  ['models', 'model', 'models'],
  ['cloud_instances', 'cloud VM', 'cloud VMs'],
];

/** "2 clusters · 1 alert channel" (empty string when unused). */
export function usageSummary(used: CredentialUsageCounts | undefined | null): string {
  if (!used) return '';
  return USAGE_NOUNS.filter(([key]) => (used[key] ?? 0) > 0)
    .map(([key, one, many]) => `${used[key]} ${used[key] === 1 ? one : many}`)
    .join(' · ');
}

export const REFERENCE_LABELS: Record<CredentialReference['resource_type'], string> = {
  cluster: 'Cluster',
  alert_channel: 'Alert channel',
  model: 'Model',
  cloud_instance: 'Cloud VM',
};

export function referenceHref(ref: CredentialReference): string {
  switch (ref.resource_type) {
    case 'cluster':
      return `/clusters/${ref.resource_id}`;
    case 'model':
      return `/catalog/${ref.resource_id}`;
    case 'cloud_instance':
      return `/cloud/${ref.resource_id}`;
    default:
      return '/alerts?tab=channels';
  }
}
