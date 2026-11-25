/**
 * Cloud API — providers, regions, GPU instance types, quotas, credential
 * checks and the provisioned VMs (`/cloud/nodes`). Provider failures come
 * back with stable codes (CLOUD_QUOTA_EXCEEDED, CLOUD_CAPACITY_UNAVAILABLE,
 * CLOUD_AUTH_FAILED…) on ApiError.code.
 */

import { apiDelete, apiGet, apiPatch, apiPost, toQuery } from '@/lib/api/client';
import { apiList, type ListResult } from '@/lib/api/fleet';
import type {
  CloudCredentialCheck,
  CloudInstanceType,
  CloudNode,
  CloudNodeAction,
  CloudNodeCreate,
  CloudNodeListParams,
  CloudNodeUpdate,
  CloudProvider,
  CloudProviderId,
  CloudProvisionPreview,
  CloudQuota,
  CloudRegion,
  InstanceTypeFilters,
} from '@/types/cloud';

/** Which account to browse with: a cloud cluster's credential, or a vault credential directly. */
export interface CloudAccountRef {
  cluster_id?: string;
  credential_id?: string;
}

export const listCloudProviders = () => apiGet<CloudProvider[]>('/cloud/providers');

export const checkCloudCredential = (provider: CloudProviderId, credentialId: string) =>
  apiPost<CloudCredentialCheck>(`/cloud/providers/${provider}/check-credential`, { credential_id: credentialId });

export const listCloudRegions = (provider: CloudProviderId, account: CloudAccountRef, refresh = false) =>
  apiGet<CloudRegion[]>(`/cloud/providers/${provider}/regions${toQuery({ ...account, refresh: refresh || undefined })}`);

export const listCloudInstanceTypes = (provider: CloudProviderId, region: string, account: CloudAccountRef, filters: InstanceTypeFilters = {}) =>
  apiGet<CloudInstanceType[]>(
    `/cloud/providers/${provider}/instance-types${toQuery({
      region,
      ...account,
      gpu_vendor: filters.gpu_vendor || undefined,
      min_vram_gb: filters.min_vram_gb || undefined,
      gpu_count: filters.gpu_count || undefined,
      max_price: filters.max_price || undefined,
      search: filters.search || undefined,
    })}`,
  );

export const listCloudQuotas = (provider: CloudProviderId, region: string, account: CloudAccountRef) =>
  apiGet<CloudQuota[]>(`/cloud/providers/${provider}/quotas${toQuery({ region, ...account })}`);

export const listCloudNodes = (params: CloudNodeListParams = {}): Promise<ListResult<CloudNode>> =>
  apiList<CloudNode>('/cloud/nodes', {
    page: params.page,
    page_size: params.page_size,
    search: params.search || undefined,
    cluster_id: params.cluster_id,
    provider: params.provider || undefined,
    status: params.status || undefined,
  });

export const getCloudNode = (id: string) => apiGet<CloudNode>(`/cloud/nodes/${id}`);
/** `dry_run: false` → the created rows (one per VM). */
export const createCloudNodes = (body: CloudNodeCreate) => apiPost<CloudNode[]>('/cloud/nodes', { ...body, dry_run: false });
/** `dry_run: true` → price, monthly estimate, resolved names / zone, cluster cap. */
export const previewCloudNodes = (body: CloudNodeCreate) => apiPost<CloudProvisionPreview>('/cloud/nodes', { ...body, dry_run: true });
export const updateCloudNode = (id: string, body: CloudNodeUpdate) => apiPatch<CloudNode>(`/cloud/nodes/${id}`, body);
export const deleteCloudNode = (id: string) => apiDelete<null>(`/cloud/nodes/${id}`);
export const stopCloudNode = (id: string, body: CloudNodeAction) => apiPost<CloudNode>(`/cloud/nodes/${id}/stop`, body);
export const startCloudNode = (id: string) => apiPost<CloudNode>(`/cloud/nodes/${id}/start`);
export const terminateCloudNode = (id: string, body: CloudNodeAction) => apiPost<CloudNode>(`/cloud/nodes/${id}/terminate`, body);
export const refreshCloudNode = (id: string) => apiPost<CloudNode>(`/cloud/nodes/${id}/refresh`);
