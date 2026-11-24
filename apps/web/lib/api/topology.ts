/**
 * Topology API (`GET /topology`) plus device reads for the infra screens.
 */

import { apiGet, toQuery } from '@/lib/api/client';
import type { components } from '@/lib/api/generated/admin';
import type { ClusterSummary, DeviceSummary, NodeSummary, Topology, TopologyInstance } from '@/types/topology';

type Schemas = components['schemas'];

export function mapDevice(row: Schemas['Device']): DeviceSummary {
  return {
    id: row.id,
    nodeId: row.node_id,
    nodeName: row.node_name ?? '',
    clusterId: row.cluster_id ?? null,
    vendor: row.vendor,
    index: row.index,
    name: row.name,
    uuid: row.uuid ?? null,
    vramTotalBytes: row.vram_total_bytes,
    vramReservedBytes: row.vram_reserved_bytes ?? 0,
    isUnifiedMemory: row.is_unified_memory ?? false,
    driverVersion: row.driver_version ?? null,
    runtimeVersion: row.runtime_version ?? null,
    computeCapability: row.compute_capability ?? null,
    pciBusId: row.pci_bus_id ?? null,
    maxPowerWatts: row.max_power_watts ?? null,
    coreCount: row.core_count ?? null,
    health: row.health,
    healthReason: row.health_reason ?? null,
    lastSeenAt: row.last_seen_at ?? null,
    live: {
      ts: row.live?.ts ?? null,
      utilizationPct: row.live?.utilization_pct ?? null,
      memoryUsedBytes: row.live?.memory_used_bytes ?? null,
      powerWatts: row.live?.power_watts ?? null,
      temperatureC: row.live?.temperature_c ?? null,
    },
    slices: (row.slices ?? []).map((s) => ({
      id: s.id,
      deviceId: s.device_id,
      instanceId: s.instance_id ?? null,
      deploymentId: s.deployment_id ?? null,
      name: s.name ?? null,
      vramBytes: s.vram_bytes,
      computePct: s.compute_pct,
      status: s.status,
    })),
    instanceIds: row.instance_ids ?? [],
    activity: row.activity ?? null,
    outsideProcesses: row.outside_processes ?? 0,
    extra: row.extra ?? {},
  };
}

export function mapNode(row: Schemas['Node']): NodeSummary {
  return {
    id: row.id,
    clusterId: row.cluster_id,
    name: row.name,
    hostname: row.hostname ?? null,
    ipAddress: row.ip_address ?? null,
    os: row.os ?? null,
    osVersion: row.os_version ?? null,
    arch: row.arch ?? null,
    agentVersion: row.agent_version ?? null,
    status: row.status,
    statusMessage: row.status_message ?? null,
    labels: row.labels ?? {},
    cpuCores: row.cpu_cores ?? null,
    memoryTotalBytes: row.memory_total_bytes ?? null,
    cacheUsedBytes: row.cache_used_bytes ?? null,
    cacheLimitBytes: row.cache_limit_bytes ?? null,
    lastHeartbeatAt: row.last_heartbeat_at ?? null,
    connectedAt: row.connected_at ?? null,
    deviceCount: row.device_count ?? 0,
    instanceCount: row.instance_count ?? 0,
    activity: row.activity ?? null,
    outsideProcesses: row.outside_processes ?? 0,
    detached: row.detached ?? false,
    drainRecorded: Boolean(row.drained_at ?? row.drain_requested_at),
  };
}

export function mapCluster(row: Schemas['Cluster']): ClusterSummary {
  return {
    id: row.id,
    name: row.name,
    type: row.type,
    region: row.region ?? null,
    status: row.status,
    description: row.description ?? null,
  };
}

function mapTopologyInstance(row: Schemas['TopologyInstance']): TopologyInstance {
  return {
    id: row.id,
    deploymentId: row.deployment_id,
    deploymentName: row.deployment_name ?? '',
    modelName: row.model_name ?? row.deployment_name ?? '',
    engine: row.engine ?? null,
    state: row.state,
    health: row.health,
    deviceIds: row.device_ids,
    trafficWeight: row.traffic_weight ?? 0,
    activeSessions: row.active_sessions ?? null,
    waitingSessions: row.waiting_sessions ?? null,
    external: row.origin === 'external',
  };
}

export function mapTopology(row: Schemas['Topology']): Topology {
  return {
    generatedAt: row.generated_at,
    clusters: row.clusters.map((c) => ({
      cluster: mapCluster(c.cluster),
      nodes: c.nodes.map((n) => ({
        node: mapNode(n.node),
        devices: n.devices.map((d) => ({ device: mapDevice(d.device), instances: d.instances.map(mapTopologyInstance) })),
      })),
    })),
    links: (row.links ?? [])
      .filter((l) => l.from_instance_id && l.to_instance_id && l.kind)
      .map((l) => ({ fromInstanceId: l.from_instance_id as string, toInstanceId: l.to_instance_id as string, kind: l.kind as Topology['links'][number]['kind'] })),
  };
}

export async function getTopology(params: { clusterId?: string; includeStopped?: boolean } = {}): Promise<Topology> {
  const qs = toQuery({ cluster_id: params.clusterId, include_stopped: params.includeStopped ? 'true' : undefined });
  return mapTopology(await apiGet<Schemas['Topology']>(`/topology${qs}`));
}

export async function getDevices(params: { nodeId?: string; clusterId?: string } = {}): Promise<DeviceSummary[]> {
  const qs = toQuery({ node_id: params.nodeId, cluster_id: params.clusterId, page_size: 200 });
  const rows = await apiGet<Schemas['Device'][]>(`/devices${qs}`);
  return rows.map(mapDevice);
}

export async function getDevice(deviceId: string): Promise<DeviceSummary> {
  return mapDevice(await apiGet<Schemas['Device']>(`/devices/${deviceId}`));
}

export async function getNodes(params: { clusterId?: string } = {}): Promise<NodeSummary[]> {
  const qs = toQuery({ cluster_id: params.clusterId, page_size: 200 });
  const rows = await apiGet<Schemas['Node'][]>(`/nodes${qs}`);
  return rows.map(mapNode);
}
