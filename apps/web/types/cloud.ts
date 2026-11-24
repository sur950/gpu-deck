/**
 * Cloud GPU VM provisioning (admin.yaml Cloud tag): providers, regions,
 * GPU instance types with list prices, and the VMs ("cloud nodes") GPU Deck
 * creates — each installs the agent from cloud-init and joins a cloud cluster.
 */

import type { components } from '@/lib/api/generated/admin';
import type { ClusterType } from '@/types/fleet';

type S = components['schemas'];

export type CloudProviderId = S['cloud_provider'];
export type CloudProvider = S['CloudProvider'];
export type CloudRegion = S['CloudRegion'];
export type CloudInstanceType = S['CloudInstanceType'];
export type CloudQuota = S['CloudQuota'];
export type CloudCredentialCheck = S['CloudCredentialCheck'];
export type CloudNode = S['CloudNode'];
export type CloudNodeStatus = S['cloud_node_status'];
export type CloudNodeCreate = S['CloudNodeCreate'];
export type CloudNodeUpdate = S['CloudNodeUpdate'];
export type CloudNodeAction = S['CloudNodeAction'];
export type CloudProvisionPreview = S['CloudProvisionPreview'];
export type CloudPricePlanSuggestion = S['CloudPricePlanSuggestion'];

export interface CloudNodeListParams {
  page?: number;
  page_size?: number;
  search?: string;
  cluster_id?: string;
  provider?: CloudProviderId | '';
  status?: CloudNodeStatus | '';
}

export interface InstanceTypeFilters {
  gpu_vendor?: string;
  min_vram_gb?: number;
  gpu_count?: number;
  max_price?: number;
  search?: string;
}

export const CLOUD_PROVIDER_LABELS: Record<CloudProviderId, string> = {
  aws: 'AWS',
  azure: 'Azure',
  gcp: 'Google Cloud',
  digitalocean: 'DigitalOcean',
};

/** Cloud cluster type for each provider (clusters.type). */
export const CLOUD_CLUSTER_TYPE: Record<CloudProviderId, ClusterType> = {
  aws: 'cloud_aws',
  azure: 'cloud_azure',
  gcp: 'cloud_gcp',
  digitalocean: 'cloud_do',
};

export function providerForClusterType(type: ClusterType | null | undefined): CloudProviderId | null {
  const hit = (Object.entries(CLOUD_CLUSTER_TYPE) as [CloudProviderId, ClusterType][]).find(([, t]) => t === type);
  return hit ? hit[0] : null;
}

export const CLOUD_STATUS_LABELS: Record<CloudNodeStatus, string> = {
  pending: 'Pending',
  provisioning: 'Provisioning',
  booting: 'Booting',
  running: 'Running',
  draining: 'Draining',
  stopping: 'Stopping',
  stopped: 'Stopped',
  starting: 'Starting',
  terminating: 'Terminating',
  terminated: 'Terminated',
  error: 'Error',
};

/** States in which the VM is billed by the provider. */
export const BILLED_STATES: CloudNodeStatus[] = ['provisioning', 'booting', 'running', 'draining', 'stopping', 'starting'];
