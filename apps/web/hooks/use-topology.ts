/**
 * Topology and device inventory hooks.
 */

'use client';

import { useQuery } from '@tanstack/react-query';
import { getDevice, getDevices, getNodes, getTopology } from '@/lib/api/topology';

export const topologyKeys = {
  all: ['topology'] as const,
  map: (clusterId: string | undefined, includeStopped: boolean) => [...topologyKeys.all, 'map', clusterId ?? 'all', includeStopped] as const,
  devices: (scope: Record<string, string | undefined>) => [...topologyKeys.all, 'devices', scope] as const,
  device: (id: string) => [...topologyKeys.all, 'device', id] as const,
  nodes: (clusterId: string | undefined) => [...topologyKeys.all, 'nodes', clusterId ?? 'all'] as const,
};

export function useTopology(params: { clusterId?: string; includeStopped?: boolean } = {}) {
  return useQuery({
    queryKey: topologyKeys.map(params.clusterId, params.includeStopped ?? false),
    queryFn: () => getTopology(params),
    staleTime: 10_000,
    refetchInterval: 15_000,
    placeholderData: (prev) => prev,
  });
}

export function useDevices(params: { nodeId?: string; clusterId?: string } = {}) {
  return useQuery({
    queryKey: topologyKeys.devices(params),
    queryFn: () => getDevices(params),
    staleTime: 15_000,
    refetchInterval: 30_000,
    placeholderData: (prev) => prev,
  });
}

export function useDevice(deviceId: string | null) {
  return useQuery({
    queryKey: topologyKeys.device(deviceId ?? ''),
    queryFn: () => getDevice(deviceId as string),
    enabled: Boolean(deviceId),
    staleTime: 15_000,
    refetchInterval: 30_000,
    placeholderData: (prev) => prev,
  });
}

export function useNodes(params: { clusterId?: string } = {}) {
  return useQuery({
    queryKey: topologyKeys.nodes(params.clusterId),
    queryFn: () => getNodes(params),
    staleTime: 30_000,
    placeholderData: (prev) => prev,
  });
}
