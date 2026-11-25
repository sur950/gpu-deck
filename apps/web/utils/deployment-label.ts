/**
 * Human labels for deployments in charts and tables.
 *
 * A chart series only carries ids (`deployment_id`, `instance_id`); the name comes from the
 * deployment list or from the labels the metrics API adds (`deployment`, `model`). Observed
 * (external) deployments have no catalog model, so the label falls back to the served model
 * name, and only then to a short id — never a bare UUID.
 */

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function looksLikeUuid(value: string | null | undefined): boolean {
  return Boolean(value && UUID_RE.test(value.trim()));
}

/** First 8 characters of an id, for labels of things with no name. */
export function shortId(id: string): string {
  return id.replace(/-/g, '').slice(0, 8);
}

function usable(value: unknown): string | null {
  if (typeof value !== 'string') return null;
  const v = value.trim();
  return v && !looksLikeUuid(v) ? v : null;
}

export interface DeploymentLike {
  id: string;
  name?: string | null;
  model_name?: string | null;
}

/**
 * Deployment name, else the served model name (from the deployment or the series labels), else
 * `deployment <short id>`.
 */
export function deploymentLabel(
  id: string,
  deployment?: DeploymentLike | null,
  seriesLabels?: Record<string, string | undefined> | null,
): string {
  return (
    usable(deployment?.name) ??
    usable(seriesLabels?.deployment) ??
    usable(deployment?.model_name) ??
    usable(seriesLabels?.served_model) ??
    usable(seriesLabels?.model) ??
    `deployment ${shortId(id)}`
  );
}

export interface LabelledEntity {
  id: string;
  label: string;
}

/**
 * One entity per deployment shown in a per-deployment chart: every listed deployment, plus any
 * deployment that only appears in the series (not in the loaded list page, or just removed),
 * labelled from the series labels. Sorted by label.
 */
export function deploymentEntities(
  deployments: readonly DeploymentLike[],
  series: readonly { labels: Record<string, string | undefined> }[],
  include: (id: string) => boolean = () => true,
): LabelledEntity[] {
  const byId = new Map(deployments.map((d) => [d.id, d]));
  const labelsById = new Map<string, Record<string, string | undefined>>();
  for (const s of series) {
    const id = s.labels.deployment_id;
    if (id && !labelsById.has(id)) labelsById.set(id, s.labels);
  }
  const ids = new Set<string>([...byId.keys(), ...labelsById.keys()]);
  return [...ids]
    .filter(include)
    .map((id) => ({ id, label: deploymentLabel(id, byId.get(id), labelsById.get(id)) }))
    .sort((a, b) => a.label.localeCompare(b.label));
}
