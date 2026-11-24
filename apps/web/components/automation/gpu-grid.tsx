'use client';

import { useState } from 'react';
import { Cpu, GripVertical, MousePointerClick, PowerOff, Server, Shuffle, Trash2, X } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { cn } from '@/lib/utils';
import type { RefDeployment, RefDevice, RefInstance, RefNode } from '@/hooks/api/automation-api';
import { deviceLoads, deviceShortName, estimateVram, gib, GiB, newKey, type EditorItem } from '@/components/automation/layout-model';
import { PRIORITY_DOT } from '@/components/automation/target-selector';
import { PRIORITY_LABELS } from '@/components/automation/rule-meta';

const DRAG_DEPLOYMENT = 'application/x-gpudeck-deployment';
const DRAG_ITEM = 'application/x-gpudeck-item';

interface GpuGridProps {
  items: EditorItem[];
  onChange: (items: EditorItem[]) => void;
  deployments: RefDeployment[];
  nodes: RefNode[];
  devices: RefDevice[];
  instances: RefInstance[];
  colors: Map<string, string>;
}

type Target = { kind: 'device'; nodeId: string; deviceId: string } | { kind: 'node'; nodeId: string } | { kind: 'scheduler' } | { kind: 'unloaded' };

function targetPlacement(t: Target): Pick<EditorItem, 'nodeId' | 'deviceIds'> & { replicas?: number } {
  switch (t.kind) {
    case 'device':
      return { nodeId: t.nodeId, deviceIds: [t.deviceId] };
    case 'node':
      return { nodeId: t.nodeId, deviceIds: [] };
    case 'scheduler':
      return { nodeId: null, deviceIds: [] };
    case 'unloaded':
      return { nodeId: null, deviceIds: [], replicas: 0 };
  }
}

function sameSpot(a: Pick<EditorItem, 'nodeId' | 'deviceIds'>, b: Pick<EditorItem, 'nodeId' | 'deviceIds'>): boolean {
  return a.nodeId === b.nodeId && [...a.deviceIds].sort().join() === [...b.deviceIds].sort().join();
}

interface PlacementChipProps {
  item: EditorItem;
  deployment: RefDeployment | undefined;
  color: string;
  perReplica: number;
  onUpdate: (key: string, patch: Partial<EditorItem>) => void;
  onRemove: (key: string) => void;
}

/** One placement on a GPU / lane: draggable, opens an editor for replicas, slice and weight. */
function PlacementChip({ item, deployment: d, color, perReplica, onUpdate: update, onRemove: remove }: PlacementChipProps) {
  return (
    <Popover>
      <PopoverTrigger
        draggable
        onDragStart={(e: React.DragEvent) => {
          e.dataTransfer.setData(DRAG_ITEM, item.key);
          e.dataTransfer.effectAllowed = 'move';
        }}
        className={cn(
          'group/chip flex w-full cursor-grab items-center gap-2 rounded-md border border-border bg-background/70 py-1.5 pl-1.5 pr-2 text-left text-xs outline-none transition-colors hover:border-foreground/20 focus-visible:ring-2 focus-visible:ring-ring/30 active:cursor-grabbing',
          item.replicas === 0 && 'opacity-70',
        )}
        aria-label={`${d?.name ?? 'Deployment'} — edit placement`}
      >
        <GripVertical className="size-3.5 shrink-0 text-muted-foreground/50 group-hover/chip:text-muted-foreground" />
        <span className="h-4 w-1 shrink-0 rounded-full" style={{ backgroundColor: color }} />
        <span className="min-w-0 flex-1 truncate font-medium">{d?.name ?? item.deploymentId.slice(0, 8)}</span>
        {item.replicas > 1 && <span className="rounded-[4px] bg-foreground/10 px-1 font-mono text-[10px] tabular-nums">×{item.replicas}</span>}
        {item.replicas > 0 && item.trafficWeight !== 100 && <span className="font-mono text-[10px] text-muted-foreground">w{item.trafficWeight}</span>}
        {item.replicas > 0 && <span className="font-mono text-[10px] tabular-nums text-muted-foreground">{gib(perReplica)}</span>}
      </PopoverTrigger>
      <PopoverContent align="start" className="w-72">
        <div className="flex items-center gap-2">
          <span className="h-4 w-1 rounded-full" style={{ backgroundColor: color }} />
          <p className="text-sm font-semibold">{d?.name}</p>
          <span className="ml-auto flex items-center gap-1 text-[11px] text-muted-foreground">
            <span className={cn('size-1.5 rounded-full', PRIORITY_DOT[d?.priority ?? 'normal'])} />
            {PRIORITY_LABELS[d?.priority ?? 'normal']}
          </span>
        </div>
        <div className="grid grid-cols-2 gap-2.5">
          <label className="space-y-1 text-[11px] text-muted-foreground">
            Replicas
            <Input type="number" min={0} max={64} value={item.replicas} onChange={(e) => update(item.key, { replicas: Math.max(0, Math.min(64, Number(e.target.value) || 0)) })} className="h-8 font-mono text-xs" />
          </label>
          <label className="space-y-1 text-[11px] text-muted-foreground">
            Traffic weight
            <Input type="number" min={0} max={1000} value={item.trafficWeight} onChange={(e) => update(item.key, { trafficWeight: Math.max(0, Math.min(1000, Number(e.target.value) || 0)) })} className="h-8 font-mono text-xs" />
          </label>
          <label className="space-y-1 text-[11px] text-muted-foreground">
            VRAM slice (GiB)
            <Input
              type="number"
              min={0}
              step={0.5}
              value={item.sliceVramBytes ? +(item.sliceVramBytes / GiB).toFixed(2) : ''}
              placeholder={gib(perReplica).replace(' GiB', '')}
              onChange={(e) => update(item.key, { sliceVramBytes: e.target.value === '' ? null : Math.round(Number(e.target.value) * GiB), sliceComputePct: item.sliceComputePct ?? 100 })}
              className="h-8 font-mono text-xs"
            />
          </label>
          <label className="space-y-1 text-[11px] text-muted-foreground">
            Compute %
            <Input type="number" min={1} max={100} value={item.sliceComputePct ?? ''} placeholder="100" disabled={!item.sliceVramBytes} onChange={(e) => update(item.key, { sliceComputePct: e.target.value === '' ? null : Math.max(1, Math.min(100, Number(e.target.value))) })} className="h-8 font-mono text-xs" />
          </label>
        </div>
        <p className="text-[11px] leading-relaxed text-muted-foreground">0 replicas keeps the deployment in the layout but unloaded. Empty slice = the scheduler sizes it from the model.</p>
        <Button type="button" variant="destructive" size="xs" className="w-full" onClick={() => remove(item.key)}>
          <Trash2 className="size-3" /> Remove from layout
        </Button>
      </PopoverContent>
    </Popover>
  );
}

function PlaceHere({ name, label, onPlace }: { name: string | null; label?: string; onPlace: () => void }) {
  if (!name) return null;
  return (
    <Button type="button" size="xs" variant="outline" className="w-full border-dashed" onClick={onPlace}>
      <MousePointerClick className="size-3" />
      {label ?? `Place ${name} here`}
    </Button>
  );
}

/**
 * The layout as a GPU grid: nodes → GPUs with VRAM bars stacked by the
 * deployments placed on them. Drag a deployment from the tray (or a
 * placement between GPUs), or click a deployment to arm it and then
 * "Place here". Click a placement to edit replicas, slice and weight.
 */
export function GpuGrid({ items, onChange, deployments, nodes, devices, instances, colors }: GpuGridProps) {
  const [armed, setArmed] = useState<string | null>(null);
  const [over, setOver] = useState<string | null>(null);
  const loads = deviceLoads(items, devices, instances);
  const byId = new Map(deployments.map((d) => [d.id, d]));

  function place(deploymentId: string, t: Target, moveKey?: string) {
    const spot = targetPlacement(t);
    const moving = moveKey ? items.find((i) => i.key === moveKey) : undefined;
    const clash = items.find((i) => i.deploymentId === deploymentId && i.key !== moveKey && sameSpot(i, spot) && (spot.replicas === 0) === (i.replicas === 0));
    if (clash) {
      if (spot.replicas !== 0) onChange(items.map((i) => (i.key === clash.key ? { ...i, replicas: i.replicas + (moving?.replicas ?? 1) } : i)).filter((i) => i.key !== moveKey));
      return;
    }
    const d = byId.get(deploymentId);
    if (t.kind === 'device') {
      const dev = devices.find((x) => x.id === t.deviceId);
      const need = estimateVram(deploymentId, t.nodeId, instances, moving?.sliceVramBytes) || 4 * GiB;
      const load = loads.get(t.deviceId);
      if (dev && load && load.usedBytes - (moving && moving.deviceIds.includes(t.deviceId) ? need : 0) + need > dev.vramTotalBytes) {
        toast.warning(`${d?.name ?? 'Deployment'} may not fit on ${deviceShortName(dev)}`, { description: `About ${gib(need)} needed, ${gib(Math.max(0, dev.vramTotalBytes - load.usedBytes))} free. The preview will flag it before applying.` });
      }
    }
    if (moving) {
      onChange(items.map((i) => (i.key === moveKey ? { ...i, ...spot, replicas: spot.replicas ?? (i.replicas === 0 ? 1 : i.replicas), sliceVramBytes: t.kind === 'device' && i.nodeId !== spot.nodeId ? estimateVram(deploymentId, spot.nodeId, instances) || i.sliceVramBytes : i.sliceVramBytes } : i)));
    } else {
      const est = spot.nodeId ? estimateVram(deploymentId, spot.nodeId, instances) : null;
      onChange([...items, { key: newKey(), deploymentId, ...spot, replicas: spot.replicas ?? 1, sliceVramBytes: est && t.kind === 'device' ? est : null, sliceComputePct: est && t.kind === 'device' ? 100 : null, trafficWeight: 100 }]);
    }
    setArmed(null);
  }

  function dropProps(id: string, t: Target) {
    return {
      onDragOver: (e: React.DragEvent) => {
        if (e.dataTransfer.types.includes(DRAG_DEPLOYMENT) || e.dataTransfer.types.includes(DRAG_ITEM)) {
          e.preventDefault();
          e.dataTransfer.dropEffect = 'move';
          if (over !== id) setOver(id);
        }
      },
      onDragLeave: (e: React.DragEvent) => {
        if (!(e.currentTarget as HTMLElement).contains(e.relatedTarget as Node)) setOver((o) => (o === id ? null : o));
      },
      onDrop: (e: React.DragEvent) => {
        e.preventDefault();
        setOver(null);
        const itemKey = e.dataTransfer.getData(DRAG_ITEM);
        const deploymentId = e.dataTransfer.getData(DRAG_DEPLOYMENT);
        if (itemKey) {
          const item = items.find((i) => i.key === itemKey);
          if (item) place(item.deploymentId, t, itemKey);
        } else if (deploymentId) place(deploymentId, t);
      },
    };
  }

  function update(key: string, patch: Partial<EditorItem>) {
    onChange(items.map((i) => (i.key === key ? { ...i, ...patch } : i)));
  }

  function remove(key: string) {
    onChange(items.filter((i) => i.key !== key));
  }

  const armedDeployment = armed ? byId.get(armed) : undefined;
  const armedName = armedDeployment?.name ?? null;

  const chip = (item: EditorItem) => (
    <PlacementChip
      key={item.key}
      item={item}
      deployment={byId.get(item.deploymentId)}
      color={colors.get(item.deploymentId) ?? 'rgb(var(--muted-foreground))'}
      perReplica={estimateVram(item.deploymentId, item.nodeId, instances, item.sliceVramBytes)}
      onUpdate={update}
      onRemove={remove}
    />
  );
  const placeHere = (t: Target, label?: string) => <PlaceHere name={armedName} label={label} onPlace={() => armedDeployment && place(armedDeployment.id, t)} />;

  const schedulerItems = items.filter((i) => i.nodeId === null && i.replicas > 0);
  const unloadedItems = items.filter((i) => i.replicas === 0);

  return (
    <div className="grid gap-4 lg:grid-cols-[250px_minmax(0,1fr)]">
      {/* Tray */}
      <div className="space-y-2 lg:sticky lg:top-4 lg:self-start">
        <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">Deployments</p>
        <p className="text-[11px] leading-relaxed text-muted-foreground">Drag onto a GPU, or click one and then “Place here”.</p>
        <ul className="space-y-1.5">
          {deployments.map((d) => {
            const placed = items.filter((i) => i.deploymentId === d.id);
            const running = placed.filter((i) => i.replicas > 0).reduce((s, i) => s + i.replicas, 0);
            const isArmed = armed === d.id;
            return (
              <li key={d.id}>
                <button
                  type="button"
                  draggable
                  onDragStart={(e) => {
                    e.dataTransfer.setData(DRAG_DEPLOYMENT, d.id);
                    e.dataTransfer.effectAllowed = 'copyMove';
                  }}
                  onClick={() => setArmed(isArmed ? null : d.id)}
                  aria-pressed={isArmed}
                  className={cn(
                    'flex w-full cursor-grab items-start gap-2.5 rounded-lg border px-2.5 py-2 text-left outline-none transition-all focus-visible:ring-2 focus-visible:ring-ring/30 active:cursor-grabbing',
                    isArmed ? 'border-foreground/40 bg-foreground/[0.05] ring-1 ring-foreground/15' : 'border-border bg-card hover:border-foreground/20',
                  )}
                >
                  <span className="mt-0.5 h-8 w-1 shrink-0 rounded-full" style={{ backgroundColor: colors.get(d.id) }} />
                  <span className="min-w-0 flex-1">
                    <span className="flex items-center justify-between gap-2">
                      <span className="truncate text-xs font-semibold">{d.name}</span>
                      <span className="font-mono text-[10px] tabular-nums text-muted-foreground">{gib(estimateVram(d.id, null, instances) || 4 * GiB)}</span>
                    </span>
                    <span className="mt-0.5 flex items-center gap-1.5 text-[11px] text-muted-foreground">
                      <span className={cn('size-1.5 rounded-full', PRIORITY_DOT[d.priority])} />
                      {PRIORITY_LABELS[d.priority]}
                      <span aria-hidden>·</span>
                      {placed.length === 0 ? <span>not in layout</span> : running === 0 ? <span className="text-error-dark dark:text-error-light">unloaded</span> : <span className="text-foreground/80">{running} replica{running === 1 ? '' : 's'}</span>}
                    </span>
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
        {armedDeployment && (
          <p className="flex items-center justify-between gap-2 rounded-md bg-foreground/[0.05] px-2.5 py-1.5 text-[11px]">
            <span>
              Placing <b>{armedDeployment.name}</b>
            </span>
            <button type="button" onClick={() => setArmed(null)} className="text-muted-foreground hover:text-foreground" aria-label="Cancel placing">
              <X className="size-3.5" />
            </button>
          </p>
        )}
      </div>

      {/* Grid */}
      <div className="min-w-0 space-y-4">
        {nodes.map((node) => {
          const nodeDevices = devices.filter((d) => d.nodeId === node.id).sort((a, b) => a.index - b.index);
          const nodeLane = items.filter((i) => i.nodeId === node.id && i.deviceIds.length === 0 && i.replicas > 0);
          const laneId = `node:${node.id}`;
          return (
            <section key={node.id} className="rounded-xl border border-border bg-card/60">
              <header className="flex flex-wrap items-center gap-2 border-b border-border px-4 py-2.5">
                <Server className="size-4 text-muted-foreground" />
                <span className="text-[13px] font-semibold">{node.name}</span>
                <span className={cn('size-1.5 rounded-full', node.status === 'online' ? 'bg-success' : node.status === 'draining' ? 'bg-warning' : 'bg-muted-foreground/50')} title={node.status} />
                <span className="text-[11px] text-muted-foreground">
                  {nodeDevices.length} GPU{nodeDevices.length === 1 ? '' : 's'}
                  {node.status !== 'online' ? ` · ${node.status}` : ''}
                </span>
              </header>
              <div className="grid gap-3 p-3 sm:grid-cols-2 2xl:grid-cols-3">
                {nodeDevices.length === 0 && <p className="px-1 py-3 text-xs text-muted-foreground">No GPUs reported by this node.</p>}
                {nodeDevices.map((dev) => {
                  const load = loads.get(dev.id);
                  const used = load?.usedBytes ?? 0;
                  const pct = Math.min(100, (used / dev.vramTotalBytes) * 100);
                  const id = `dev:${dev.id}`;
                  return (
                    <div
                      key={dev.id}
                      {...dropProps(id, { kind: 'device', nodeId: node.id, deviceId: dev.id })}
                      className={cn(
                        'flex min-h-[156px] flex-col gap-2.5 rounded-lg border bg-background/50 p-3 transition-colors',
                        over === id ? 'border-foreground/40 bg-foreground/[0.04] ring-2 ring-foreground/10' : load?.over ? 'border-error/50' : 'border-border',
                      )}
                    >
                      <div className="flex items-start justify-between gap-2">
                        <div className="min-w-0">
                          <p className="flex items-center gap-1.5 truncate text-xs font-semibold">
                            <Cpu className="size-3.5 text-muted-foreground" />
                            {deviceShortName(dev)}
                          </p>
                          <p className="text-[10px] uppercase tracking-wide text-muted-foreground">
                            {dev.vendor}
                            {dev.isUnifiedMemory ? ' · unified memory' : ''}
                          </p>
                        </div>
                        <p className={cn('text-right font-mono text-[11px] tabular-nums', load?.over ? 'text-error-dark dark:text-error-light' : 'text-muted-foreground')}>
                          <span className="text-foreground">{gib(used)}</span> / {gib(dev.vramTotalBytes, 0)}
                        </p>
                      </div>
                      <div className="flex h-2.5 overflow-hidden rounded-full bg-muted" role="img" aria-label={`${gib(used)} of ${gib(dev.vramTotalBytes)} planned`}>
                        {(load?.segments ?? []).map((s) => (
                          <span key={s.item.key} className="h-full border-r border-background/70 last:border-r-0" style={{ width: `${(s.bytes / dev.vramTotalBytes) * 100}%`, backgroundColor: colors.get(s.item.deploymentId) }} />
                        ))}
                      </div>
                      <p className={cn('-mt-1 text-[10px] tabular-nums', load?.over ? 'font-medium text-error-dark dark:text-error-light' : 'text-muted-foreground')}>
                        {load?.over ? `Over by ${gib(used - dev.vramTotalBytes)} — will not fit` : `${gib(Math.max(0, dev.vramTotalBytes - used))} free · ${pct.toFixed(0)}% planned`}
                      </p>
                      <div className="flex flex-1 flex-col gap-1.5">
                        {(load?.segments ?? []).map((s) => chip(s.item))}
                        {!load?.segments.length && !armedDeployment && <p className="flex flex-1 items-center justify-center rounded-md border border-dashed border-border py-3 text-[11px] text-muted-foreground">Drop a deployment here</p>}
                        {placeHere({ kind: 'device', nodeId: node.id, deviceId: dev.id })}
                      </div>
                    </div>
                  );
                })}
              </div>
              <div {...dropProps(laneId, { kind: 'node', nodeId: node.id })} className={cn('mx-3 mb-3 rounded-lg border border-dashed px-3 py-2 transition-colors', over === laneId ? 'border-foreground/40 bg-foreground/[0.04]' : 'border-border')}>
                <p className="mb-1.5 flex items-center gap-1.5 text-[11px] text-muted-foreground">
                  <Shuffle className="size-3" /> Any GPU on {node.name} — the scheduler picks the card
                </p>
                <div className="grid gap-1.5 sm:grid-cols-2">
                  {nodeLane.map((i) => chip(i))}
                </div>
                {placeHere({ kind: 'node', nodeId: node.id }, armedName ? `Place ${armedName} on any GPU of ${node.name}` : undefined)}
              </div>
            </section>
          );
        })}

        <div className="grid gap-4 md:grid-cols-2">
          <div {...dropProps('scheduler', { kind: 'scheduler' })} className={cn('rounded-xl border border-dashed p-3 transition-colors', over === 'scheduler' ? 'border-foreground/40 bg-foreground/[0.04]' : 'border-border')}>
            <p className="mb-2 flex items-center gap-1.5 text-xs font-medium">
              <Shuffle className="size-3.5 text-muted-foreground" /> Scheduler decides
            </p>
            <p className="mb-2 text-[11px] text-muted-foreground">Placed wherever fits best when the layout is applied.</p>
            <div className="space-y-1.5">
              {schedulerItems.map((i) => chip(i))}
              {placeHere({ kind: 'scheduler' }, armedName ? `Let the scheduler place ${armedName}` : undefined)}
            </div>
          </div>
          <div {...dropProps('unloaded', { kind: 'unloaded' })} className={cn('rounded-xl border border-dashed p-3 transition-colors', over === 'unloaded' ? 'border-error/50 bg-error/[0.04]' : 'border-border')}>
            <p className="mb-2 flex items-center gap-1.5 text-xs font-medium">
              <PowerOff className="size-3.5 text-muted-foreground" /> Unloaded in this layout
            </p>
            <p className="mb-2 text-[11px] text-muted-foreground">Drop here to unload when the layout applies. Always-on models stay loaded regardless.</p>
            <div className="space-y-1.5">
              {unloadedItems.map((i) => chip(i))}
              {placeHere({ kind: 'unloaded' }, armedName ? `Unload ${armedName} in this layout` : undefined)}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
