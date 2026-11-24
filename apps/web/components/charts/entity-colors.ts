/**
 * Stable categorical colours for entities (deployments, GPUs, nodes).
 *
 * Colour follows the entity, never its rank: ids are sorted by a stable key
 * (name) and assigned slots in a fixed order, so filtering a chart never
 * repaints the survivors. Slots start at chart-2 — chart-1 is the brand accent,
 * kept for single-series and headline charts — and stop at four
 * (validated for CVD separation in light and dark); a fifth entity and beyond
 * fold into the muted "Other" colour and must be told apart by label.
 */

export const ENTITY_COLORS = [
  'rgb(var(--chart-2))',
  'rgb(var(--chart-3))',
  'rgb(var(--chart-4))',
  'rgb(var(--chart-5))',
] as const;

export const ENTITY_OTHER = 'rgb(var(--muted-foreground))';

/** Map each key to its colour; keys are ordered by `sortKey` (defaults to the key). */
export function assignEntityColors<T>(items: T[], key: (item: T) => string, sortKey: (item: T) => string = key): Map<string, string> {
  const sorted = [...items].sort((a, b) => sortKey(a).localeCompare(sortKey(b)));
  const out = new Map<string, string>();
  for (const item of sorted) {
    const k = key(item);
    if (!out.has(k)) out.set(k, ENTITY_COLORS[out.size] ?? ENTITY_OTHER);
  }
  return out;
}
