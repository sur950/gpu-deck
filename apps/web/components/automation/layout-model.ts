/**
 * Layout editor model: editable items, VRAM estimates per GPU, a client-side
 * diff against the running placement (instant feedback while dragging), and
 * stable colours per deployment. The server preview (`POST
 * /layouts/{id}/preview`) stays authoritative and is shown before applying.
 */

import type { Layout, LayoutItemInput, RefDeployment, RefDevice, RefInstance } from '@/hooks/api/automation-api';
import { SERIES_COLORS } from '@/components/charts/chart-theme';

export const GiB = 1024 ** 3;

export interface EditorItem {
  /** Local key (stable across edits). */
  key: string;
  deploymentId: string;
  nodeId: string | null;
  deviceIds: string[];
  replicas: number;
  sliceVramBytes: number | null;
  sliceComputePct: number | null;
  trafficWeight: number;
}

let keySeq = 0;
export function newKey(): string {
  keySeq += 1;
  return `item-${Date.now().toString(36)}-${keySeq}`;
}

export function itemsFromLayout(layout: Layout): EditorItem[] {
  return layout.items.map((i) => ({
    key: i.id || newKey(),
    deploymentId: i.deploymentId,
    nodeId: i.nodeId,
    deviceIds: i.deviceIds,
    replicas: i.replicas,
    sliceVramBytes: i.sliceVramBytes,
    sliceComputePct: i.sliceComputePct,
    trafficWeight: i.trafficWeight,
  }));
}

/** The running placement as editor items (for "start from current"). */
export function itemsFromInstances(instances: RefInstance[]): EditorItem[] {
  const groups = new Map<string, EditorItem>();
  for (const inst of instances) {
    if (inst.state === 'stopped' || inst.state === 'error') continue;
    const k = `${inst.deploymentId}|${inst.nodeId}|${[...inst.deviceIds].sort().join('+')}`;
    const existing = groups.get(k);
    if (existing) existing.replicas += 1;
    else groups.set(k, { key: newKey(), deploymentId: inst.deploymentId, nodeId: inst.nodeId, deviceIds: inst.deviceIds, replicas: 1, sliceVramBytes: inst.vramBytes, sliceComputePct: inst.vramBytes ? 100 : null, trafficWeight: inst.trafficWeight });
  }
  return [...groups.values()];
}

export function toInput(items: EditorItem[]): LayoutItemInput[] {
  return items.map((i, idx) => ({
    deployment_id: i.deploymentId,
    node_id: i.nodeId,
    device_ids: i.nodeId ? i.deviceIds : [],
    replicas: i.replicas,
    traffic_weight: i.trafficWeight,
    position: idx,
    slice: i.sliceVramBytes ? { vram_bytes: Math.round(i.sliceVramBytes), compute_pct: i.sliceComputePct ?? 100 } : null,
  }));
}

/** Best guess of the VRAM one replica needs (slice → instance on that node → largest instance → 4 GiB). */
export function estimateVram(deploymentId: string, nodeId: string | null, instances: RefInstance[], slice?: number | null): number {
  if (slice) return slice;
  const own = instances.filter((i) => i.deploymentId === deploymentId && i.vramBytes);
  const onNode = nodeId ? own.find((i) => i.nodeId === nodeId) : undefined;
  return onNode?.vramBytes || own.reduce((m, i) => Math.max(m, i.vramBytes ?? 0), 0) || 4 * GiB;
}

export interface DeviceLoad {
  device: RefDevice;
  usedBytes: number;
  segments: { item: EditorItem; bytes: number }[];
  over: boolean;
}

export function deviceLoads(items: EditorItem[], devices: RefDevice[], instances: RefInstance[]): Map<string, DeviceLoad> {
  const loads = new Map<string, DeviceLoad>(devices.map((d) => [d.id, { device: d, usedBytes: 0, segments: [], over: false }]));
  for (const item of items) {
    if (item.replicas <= 0 || !item.deviceIds.length) continue;
    const perReplica = estimateVram(item.deploymentId, item.nodeId, instances, item.sliceVramBytes) || 4 * GiB;
    const perDevice = (perReplica * item.replicas) / item.deviceIds.length;
    for (const id of item.deviceIds) {
      const load = loads.get(id);
      if (!load) continue;
      load.usedBytes += perDevice;
      load.segments.push({ item, bytes: perDevice });
    }
  }
  for (const load of loads.values()) load.over = load.usedBytes > load.device.vramTotalBytes;
  return loads;
}

// ─── Client-side diff ─────────────────────────────────────────────────────────

export type DiffAction = 'load' | 'unload' | 'move' | 'scale' | 'reweight' | 'noop';

export interface ClientDiffEntry {
  deploymentId: string;
  action: DiffAction;
  fromReplicas: number;
  toReplicas: number;
  fromWhere: string[];
  toWhere: string[];
  protected: boolean;
}

function where(nodeId: string | null, deviceIds: string[], names: { node: (id: string) => string; device: (id: string) => string }): string {
  if (!nodeId) return 'scheduler picks';
  return deviceIds.length ? deviceIds.map(names.device).join(' + ') : `${names.node(nodeId)} (any GPU)`;
}

export function clientDiff(
  items: EditorItem[],
  unlistedPolicy: 'keep' | 'unload',
  instances: RefInstance[],
  deployments: RefDeployment[],
  names: { node: (id: string) => string; device: (id: string) => string },
): ClientDiffEntry[] {
  const live = instances.filter((i) => i.state !== 'stopped' && i.state !== 'error');
  const listed = new Set(items.map((i) => i.deploymentId));
  const ids = new Set<string>([...listed, ...(unlistedPolicy === 'unload' ? live.map((i) => i.deploymentId) : [])]);
  const out: ClientDiffEntry[] = [];
  for (const id of ids) {
    const d = deployments.find((x) => x.id === id);
    const alwaysOn = d?.priority === 'always_on';
    const cur = live.filter((i) => i.deploymentId === id);
    const want = items.filter((i) => i.deploymentId === id && i.replicas > 0);
    const fromReplicas = cur.length;
    const toReplicas = want.reduce((s, i) => s + i.replicas, 0);
    const fromWhere = cur.map((i) => where(i.nodeId, i.deviceIds, names));
    const toWhere = want.map((i) => `${where(i.nodeId, i.deviceIds, names)}${i.replicas > 1 ? ` ×${i.replicas}` : ''}`);
    const base = { deploymentId: id, fromReplicas, toReplicas, fromWhere, toWhere, protected: false };
    if (!listed.has(id) || toReplicas === 0) {
      if (fromReplicas === 0) continue;
      out.push(alwaysOn ? { ...base, action: 'noop', protected: true, toReplicas: fromReplicas, toWhere: fromWhere } : { ...base, action: 'unload' });
      continue;
    }
    if (fromReplicas === 0) {
      out.push({ ...base, action: 'load' });
      continue;
    }
    const key = (nodeId: string | null, deviceIds: string[]) => `${nodeId ?? '*'}:${[...deviceIds].sort().join('+')}`;
    const curKeys = cur.map((i) => key(i.nodeId, i.deviceIds));
    const wantKeys = want.flatMap((i) => Array.from({ length: i.replicas }, () => key(i.nodeId, i.deviceIds)));
    const same = curKeys.length === wantKeys.length && [...curKeys].sort().join('|') === [...wantKeys].sort().join('|');
    if (same) {
      const sameWeights = want.every((w) => cur.filter((c) => key(c.nodeId, c.deviceIds) === key(w.nodeId, w.deviceIds)).every((c) => c.trafficWeight === w.trafficWeight));
      out.push({ ...base, action: sameWeights ? 'noop' : 'reweight' });
      continue;
    }
    const overlap = wantKeys.some((k) => curKeys.includes(k));
    out.push({ ...base, action: overlap && fromReplicas !== toReplicas ? 'scale' : 'move' });
  }
  const order: DiffAction[] = ['unload', 'move', 'scale', 'load', 'reweight', 'noop'];
  return out.sort((a, b) => order.indexOf(a.action) - order.indexOf(b.action));
}

export const DIFF_META: Record<DiffAction, { label: string; sign: string; className: string }> = {
  load: { label: 'Load', sign: '+', className: 'bg-success/12 text-success-dark ring-success/30 dark:text-success-light' },
  unload: { label: 'Unload', sign: '−', className: 'bg-error/10 text-error-dark ring-error/30 dark:text-error-light' },
  move: { label: 'Move', sign: '→', className: 'bg-info/10 text-info-dark ring-info/30 dark:text-info-light' },
  scale: { label: 'Scale', sign: '±', className: 'bg-warning/10 text-warning-dark ring-warning/30 dark:text-warning-light' },
  reweight: { label: 'Re-weight', sign: '%', className: 'bg-chart-4/10 text-foreground ring-chart-4/30' },
  noop: { label: 'No change', sign: '=', className: 'bg-muted text-muted-foreground ring-border' },
};

/** Stable colour per deployment (alphabetical by name). */
export function deploymentColors(deployments: { id: string; name: string }[]): Map<string, string> {
  const sorted = [...deployments].sort((a, b) => a.name.localeCompare(b.name));
  return new Map(sorted.map((d, i) => [d.id, SERIES_COLORS[(i + 1) % SERIES_COLORS.length]]));
}

/** Layout colours for the 24 h timeline (chart-2 onward; chart-1 is the brand). */
export function layoutColors(layouts: { id: string; name: string }[]): Map<string, string> {
  const sorted = [...layouts].sort((a, b) => a.name.localeCompare(b.name));
  return new Map(sorted.map((l, i) => [l.id, SERIES_COLORS[(i + 1) % SERIES_COLORS.length]]));
}

export function deviceShortName(d: RefDevice): string {
  if (d.vendor === 'apple') return d.name.replace(/\s*\(.*\)$/, '');
  return `${d.name.replace(/^NVIDIA\s+|^AMD\s+/i, '')} #${d.index}`;
}

export function gib(bytes: number, digits = 1): string {
  const v = bytes / GiB;
  return `${v >= 100 ? Math.round(v) : v.toFixed(digits).replace(/\.0$/, '')} GiB`;
}
