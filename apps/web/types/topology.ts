/**
 * UI types for the topology map and the device inventory it drills into.
 */

import type { components } from '@/lib/api/generated/admin';
import type { EngineName, InstanceHealth, InstanceState } from '@/types/metrics';

export type DeviceVendor = components['schemas']['device_vendor'];
export type DeviceHealth = components['schemas']['device_health'];
export type NodeStatus = components['schemas']['node_status'];
export type ClusterType = components['schemas']['cluster_type'];

export interface DeviceLive {
  ts: string | null;
  utilizationPct: number | null;
  memoryUsedBytes: number | null;
  powerWatts: number | null;
  temperatureC: number | null;
}

export interface DeviceSlice {
  id: string;
  deviceId: string;
  instanceId: string | null;
  deploymentId: string | null;
  name: string | null;
  vramBytes: number;
  computePct: number;
  status: components['schemas']['device_slice_status'];
}

export interface DeviceSummary {
  id: string;
  nodeId: string;
  nodeName: string;
  clusterId: string | null;
  vendor: DeviceVendor;
  index: number;
  name: string;
  uuid: string | null;
  vramTotalBytes: number;
  vramReservedBytes: number;
  isUnifiedMemory: boolean;
  driverVersion: string | null;
  runtimeVersion: string | null;
  computeCapability: string | null;
  pciBusId: string | null;
  maxPowerWatts: number | null;
  coreCount: number | null;
  health: DeviceHealth;
  healthReason: string | null;
  lastSeenAt: string | null;
  live: DeviceLive;
  slices: DeviceSlice[];
  instanceIds: string[];
  /** detached | stale | gpu_deck | mixed | external | idle, from live data. */
  activity: string | null;
  /** GPU processes not run by GPU Deck. */
  outsideProcesses: number;
  /** Vendor-specific properties from the device plugin (MIG mode, ECC …). */
  extra: Record<string, unknown>;
}

export interface NodeSummary {
  id: string;
  clusterId: string;
  name: string;
  hostname: string | null;
  ipAddress: string | null;
  os: string | null;
  osVersion: string | null;
  arch: string | null;
  agentVersion: string | null;
  status: NodeStatus;
  statusMessage: string | null;
  labels: Record<string, string>;
  cpuCores: number | null;
  memoryTotalBytes: number | null;
  cacheUsedBytes: number | null;
  cacheLimitBytes: number | null;
  lastHeartbeatAt: string | null;
  connectedAt: string | null;
  deviceCount: number;
  instanceCount: number;
  activity: string | null;
  outsideProcesses: number;
  /** Agent gone (offline / removed / heartbeat stale). */
  detached: boolean;
  /** A drain is recorded (kept while a drained node is offline). */
  drainRecorded: boolean;
}

export interface ClusterSummary {
  id: string;
  name: string;
  type: ClusterType;
  region: string | null;
  status: string;
  description: string | null;
}

export interface TopologyInstance {
  id: string;
  deploymentId: string;
  deploymentName: string;
  modelName: string;
  engine: EngineName | null;
  state: InstanceState;
  health: InstanceHealth;
  deviceIds: string[];
  trafficWeight: number;
  activeSessions: number | null;
  waitingSessions: number | null;
  /** Observed server GPU Deck did not start. */
  external: boolean;
}

export interface TopologyDevice {
  device: DeviceSummary;
  instances: TopologyInstance[];
}

export interface TopologyNode {
  node: NodeSummary;
  devices: TopologyDevice[];
}

export interface TopologyCluster {
  cluster: ClusterSummary;
  nodes: TopologyNode[];
}

export type TopologyLinkKind = 'tensor_parallel' | 'pipeline_parallel' | 'pd_transfer' | 'kv_share';

export interface TopologyLink {
  fromInstanceId: string;
  toInstanceId: string;
  kind: TopologyLinkKind;
}

export interface Topology {
  generatedAt: string;
  clusters: TopologyCluster[];
  links: TopologyLink[];
}
