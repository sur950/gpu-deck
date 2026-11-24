/**
 * Target selector semantics (libs/contracts/rules/target-selector.schema.json):
 * every listed condition is ANDed, values inside a condition are ORed, an
 * empty selector matches every deployment. Shared by the builder's live
 * "matches N deployments" preview and the mock evaluator.
 */

import type { TargetSelector } from '@/hooks/api/automation-api';

/** The facts about a deployment a selector can test. */
export interface MatchableDeployment {
  id: string;
  name: string;
  modelId: string;
  clusterId: string | null;
  modelType: string | null;
  priority: string;
  engine: string | null;
  /** Nodes that host at least one instance. */
  nodeIds: string[];
  /** Labels of those nodes, one map per node. */
  nodeLabels: Record<string, string>[];
}

const LIST_KEYS = [
  'deployment_ids',
  'model_ids',
  'cluster_ids',
  'node_ids',
  'model_types',
  'priorities',
  'engines',
  'exclude_deployment_ids',
] as const;

export function globToRegExp(glob: string): RegExp {
  const escaped = glob.replace(/[.+^${}()|[\]\\]/g, '\\$&').replace(/\*/g, '.*').replace(/\?/g, '.');
  return new RegExp(`^${escaped}$`, 'i');
}

/** Remove empty lists / blank strings so the stored selector stays minimal. */
export function compactTarget(target: TargetSelector): TargetSelector {
  const out: TargetSelector = {};
  for (const key of LIST_KEYS) {
    const list = target[key] as string[] | undefined;
    if (list && list.length) (out as Record<string, unknown>)[key] = [...new Set(list)];
  }
  if (target.labels && Object.keys(target.labels).length) {
    const labels = Object.fromEntries(Object.entries(target.labels).filter(([k]) => k.trim() !== ''));
    if (Object.keys(labels).length) out.labels = labels;
  }
  if (target.name_glob && target.name_glob.trim()) out.name_glob = target.name_glob.trim();
  return out;
}

export function isEmptyTarget(target: TargetSelector | null | undefined): boolean {
  return !target || Object.keys(compactTarget(target)).length === 0;
}

export function matchesTarget(d: MatchableDeployment, target: TargetSelector | null | undefined): boolean {
  const t = target ?? {};
  if (t.exclude_deployment_ids?.includes(d.id)) return false;
  if (t.deployment_ids?.length && !t.deployment_ids.includes(d.id)) return false;
  if (t.model_ids?.length && !t.model_ids.includes(d.modelId)) return false;
  if (t.cluster_ids?.length && !(d.clusterId && t.cluster_ids.includes(d.clusterId))) return false;
  if (t.node_ids?.length && !d.nodeIds.some((n) => t.node_ids?.includes(n))) return false;
  if (t.model_types?.length && !(d.modelType && (t.model_types as string[]).includes(d.modelType))) return false;
  if (t.priorities?.length && !(t.priorities as string[]).includes(d.priority)) return false;
  if (t.engines?.length && !(d.engine && (t.engines as string[]).includes(d.engine))) return false;
  const labels = Object.entries(t.labels ?? {}).filter(([k]) => k.trim() !== '');
  if (labels.length && !d.nodeLabels.some((nl) => labels.every(([k, v]) => nl[k] === v))) return false;
  if (t.name_glob && t.name_glob.trim() && !globToRegExp(t.name_glob.trim()).test(d.name)) return false;
  return true;
}
