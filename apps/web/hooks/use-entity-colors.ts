/**
 * Fleet-wide entity colours. Assigned from the unscoped deployment / device
 * lists so a deployment (or GPU) keeps the same colour on every page and
 * under every filter.
 */

'use client';

import { useMemo } from 'react';
import { assignEntityColors } from '@/components/charts/entity-colors';
import { useDeployments } from '@/hooks/use-metrics';
import { useDevices } from '@/hooks/use-topology';

export function useDeploymentColors(): Map<string, string> {
  const deployments = useDeployments();
  return useMemo(() => assignEntityColors(deployments.data ?? [], (d) => d.id, (d) => d.name), [deployments.data]);
}

export function useDeviceColors(): Map<string, string> {
  const devices = useDevices();
  return useMemo(
    () => assignEntityColors(devices.data ?? [], (d) => d.id, (d) => `${d.nodeName}#${String(d.index).padStart(3, '0')}`),
    [devices.data],
  );
}
