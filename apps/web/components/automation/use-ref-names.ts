'use client';

import { useMemo } from 'react';
import { useLayouts, useRefClusters, useRefDeployments, useRefModels, useRefNodes } from '@/hooks/api/automation';
import type { RefNames } from '@/components/automation/rule-meta';

/** id → name maps for sentences and tables (deployments, models, nodes, clusters, layouts). */
export function useRefNames(): RefNames {
  const deployments = useRefDeployments();
  const models = useRefModels();
  const nodes = useRefNodes();
  const clusters = useRefClusters();
  const layouts = useLayouts({ pageSize: 100 });
  return useMemo(
    () => ({
      deployments: new Map((deployments.data ?? []).map((d) => [d.id, d.name])),
      models: new Map([...(deployments.data ?? []).map((d) => [d.modelId, d.modelName ?? d.name] as const), ...(models.data ?? []).map((m) => [m.id, m.displayName ?? m.name] as const)]),
      nodes: new Map((nodes.data ?? []).map((n) => [n.id, n.name])),
      clusters: new Map((clusters.data ?? []).map((c) => [c.id, c.name])),
      layouts: new Map((layouts.data?.items ?? []).map((l) => [l.id, l.name])),
    }),
    [deployments.data, models.data, nodes.data, clusters.data, layouts.data],
  );
}
