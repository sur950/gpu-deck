/**
 * Backend status strings → StatusBadge variants for fleet and model
 * entities. Everything the shared badge does not know by name is mapped to
 * the closest tone here, so screens never pass raw strings.
 */

import type { BadgeVariant } from '@/components/shared/StatusBadge';
import type { ClusterStatus, DeviceHealth, DeviceSliceStatus, NodeStatus, AgentCommandState } from '@/types/fleet';
import type { CacheState, DeploymentStatus, InstanceHealth, InstanceState, BenchmarkRunStatus, ModelVersionStatus, CompatResult } from '@/types/models';

export const NODE_BADGE: Record<NodeStatus, BadgeVariant> = {
  pending: 'pending',
  online: 'online',
  offline: 'offline',
  unhealthy: 'error',
  draining: 'draining',
  drained: 'inactive',
  removed: 'expired',
};

export const CLUSTER_BADGE: Record<ClusterStatus, BadgeVariant> = {
  active: 'active',
  disabled: 'inactive',
};

export const DEVICE_HEALTH_BADGE: Record<DeviceHealth, BadgeVariant> = {
  healthy: 'healthy',
  degraded: 'degraded',
  unhealthy: 'down',
  unknown: 'unknown',
};

export const SLICE_BADGE: Record<DeviceSliceStatus, BadgeVariant> = {
  reserved: 'scheduled',
  active: 'active',
  released: 'inactive',
};

export const COMMAND_BADGE: Record<AgentCommandState, BadgeVariant> = {
  pending: 'pending',
  sent: 'scheduled',
  accepted: 'scheduled',
  running: 'loading',
  succeeded: 'completed',
  failed: 'failed',
  rejected: 'error',
  timed_out: 'warning',
  cancelled: 'inactive',
};

export const DEPLOYMENT_BADGE: Record<DeploymentStatus, BadgeVariant> = {
  pending: 'pending',
  scheduling: 'scheduled',
  deploying: 'loading',
  running: 'running',
  degraded: 'degraded',
  scaling: 'loading',
  stopped: 'stopped',
  failed: 'failed',
  deleting: 'warning',
};

export const INSTANCE_BADGE: Record<InstanceState, BadgeVariant> = {
  pending: 'pending',
  loading: 'loading',
  ready: 'running',
  draining: 'draining',
  unloading: 'unloading',
  stopped: 'stopped',
  error: 'failed',
};

export const INSTANCE_HEALTH_BADGE: Record<InstanceHealth, BadgeVariant> = {
  healthy: 'healthy',
  unhealthy: 'down',
  unknown: 'unknown',
};

export const CACHE_BADGE: Record<CacheState, BadgeVariant> = {
  queued: 'pending',
  downloading: 'loading',
  ready: 'success',
  evicting: 'unloading',
  evicted: 'evicted',
  error: 'failed',
};

export const RUN_BADGE: Record<BenchmarkRunStatus, BadgeVariant> = {
  pending: 'pending',
  running: 'loading',
  completed: 'completed',
  failed: 'failed',
  cancelled: 'inactive',
};

export const VERSION_BADGE: Record<ModelVersionStatus, BadgeVariant> = {
  registered: 'pending',
  verifying: 'loading',
  ready: 'success',
  missing: 'warning',
  blocked: 'error',
};

export const COMPAT_BADGE: Record<CompatResult, BadgeVariant> = {
  compatible: 'success',
  incompatible: 'error',
  unknown: 'unknown',
};
