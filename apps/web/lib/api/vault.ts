/**
 * Vault API — typed credentials (admin.yaml: Vault tag).
 *
 * Responses carry masked previews and public fields only; secret fields are
 * write-only and come back solely from `revealCredential` (audited).
 */

import { apiDelete, apiGet, apiPatch, apiPost } from '@/lib/api/client';
import { apiList, type ListResult } from '@/lib/api/fleet';
import type {
  Credential,
  CredentialCreate,
  CredentialListParams,
  CredentialRevealed,
  CredentialStats,
  CredentialTestResult,
  CredentialUpdate,
  CredentialUsage,
  VaultMetadata,
} from '@/types/vault';

export function listCredentials(params: CredentialListParams = {}): Promise<ListResult<Credential>> {
  return apiList<Credential>('/vault', {
    page: params.page,
    page_size: params.pageSize ?? 50,
    search: params.search || undefined,
    provider: params.provider || undefined,
    status: params.status || undefined,
    sort: params.sort || undefined,
    include_system: params.includeSystem || undefined,
  });
}

export const getCredential = (id: string) => apiGet<Credential>(`/vault/${id}`);
export const getVaultMetadata = () => apiGet<VaultMetadata>('/vault/metadata');
export const getVaultStats = () => apiGet<CredentialStats>('/vault/stats');
export const getCredentialUsage = (id: string) => apiGet<CredentialUsage>(`/vault/${id}/usage`);
export const createCredential = (body: CredentialCreate) => apiPost<Credential>('/vault', body);
export const updateCredential = (id: string, body: CredentialUpdate) => apiPatch<Credential>(`/vault/${id}`, body);
export const deleteCredential = (id: string) => apiDelete<void>(`/vault/${id}`);
export const revealCredential = (id: string) => apiPost<CredentialRevealed>(`/vault/${id}/reveal`);
export const testCredential = (id: string) => apiPost<CredentialTestResult>(`/vault/${id}/test`);
