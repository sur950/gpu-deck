'use client';

import { useMemo } from 'react';
import { CircleAlert, Loader2, Minus, Plus, Server, TriangleAlert } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Slider } from '@/components/ui/slider';
import { Switch } from '@/components/ui/switch';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Field } from '@/components/shared/form-dialog';
import { KeyValueEditor } from '@/components/shared/key-value-editor';
import { VendorBadge } from '@/components/fleet/vendor-badge';
import { effectiveGpuCount } from '@/components/models/wizard/draft';
import type { StepProps } from '@/components/models/wizard/context';
import { cn } from '@/lib/utils';
import { formatBytes } from '@/utils/format';
import { DEVICE_VENDOR_LABELS, type Device, type DeviceVendor } from '@/types/fleet';
import type { PlacementCandidate } from '@/types/models';

const GiB = 1024 ** 3;

function Stepper({ value, onChange, min = 0, max = 64, label }: { value: number; onChange: (v: number) => void; min?: number; max?: number; label: string }) {
  return (
    <div className="inline-flex items-center rounded-md border border-input" role="group" aria-label={label}>
      <Button type="button" variant="ghost" size="icon-sm" className="rounded-r-none" onClick={() => onChange(Math.max(min, value - 1))} disabled={value <= min} aria-label={`Decrease ${label}`}>
        <Minus className="size-3.5" />
      </Button>
      <span className="w-10 text-center font-mono text-sm tabular-nums">{value}</span>
      <Button type="button" variant="ghost" size="icon-sm" className="rounded-l-none" onClick={() => onChange(Math.min(max, value + 1))} disabled={value >= max} aria-label={`Increase ${label}`}>
        <Plus className="size-3.5" />
      </Button>
    </div>
  );
}

export function placementStepError({ draft, ctx }: Pick<StepProps, 'draft' | 'ctx'>): string | null {
  if (draft.replicas < 0) return 'Replicas must be 0 or more';
  if (draft.minReplicas > draft.replicas) return 'Min replicas exceeds replicas';
  if (draft.maxReplicas < draft.replicas) return 'Max replicas is below replicas';
  if (effectiveGpuCount(draft) > 1 && !ctx.caps.tensorParallel && !ctx.caps.pipelineParallel) return `${ctx.engine?.display_name ?? 'This engine'} runs on one GPU per replica`;
  if (draft.sliceEnabled && !ctx.caps.slices) return `${ctx.engine?.display_name ?? 'This engine'} does not support slices`;
  if (ctx.previewLoading && !ctx.preview) return 'Computing placement…';
  if (ctx.preview && !ctx.preview.feasible && draft.replicas > 0 && draft.start) return 'Not every replica fits — adjust, or save without starting';
  return null;
}

/** Device tiles for one node with the replicas the scheduler would place on it. */
function NodePreview({ name, devices, placements, rejected }: { name: string; devices: Device[]; placements: { index: number; candidate: PlacementCandidate }[]; rejected?: string }) {
  return (
    <div className={cn('rounded-lg border p-3', placements.length ? 'border-foreground/30 bg-card' : 'border-border bg-muted/20')}>
      <div className="mb-2 flex items-center justify-between gap-2">
        <span className="flex items-center gap-1.5 text-xs font-medium">
          <Server className="size-3.5 text-muted-foreground" />
          {name}
        </span>
        {placements.length > 0 ? (
          <span className="text-[10.5px] font-medium text-success-dark dark:text-success-light">{placements.length} replica{placements.length === 1 ? '' : 's'}</span>
        ) : (
          rejected && <span className="max-w-[60%] truncate text-[10.5px] text-muted-foreground" title={rejected}>{rejected}</span>
        )}
      </div>
      <div className="space-y-2">
        {devices.map((d) => {
          const hits = placements.filter((p) => p.candidate.device_ids.includes(d.id));
          const reserved = (d.slices ?? []).filter((s) => s.status !== 'released').reduce((s, x) => s + x.vram_bytes, 0);
          const usable = d.vram_total_bytes * (d.is_unified_memory ? 0.75 : 0.9);
          const adding = hits.reduce((s, h) => s + (h.candidate.slice?.vram_bytes ?? (h.candidate.estimated_vram_bytes ?? 0) / Math.max(1, h.candidate.device_ids.length)), 0);
          const pct = (b: number) => `${Math.min(100, (b / d.vram_total_bytes) * 100)}%`;
          return (
            <div key={d.id} className="space-y-1">
              <div className="flex items-center justify-between gap-2 text-[11px]">
                <span className="flex min-w-0 items-center gap-1.5">
                  <VendorBadge vendor={d.vendor} withLabel={false} />
                  <span className="truncate">
                    {d.name} <span className="text-muted-foreground">#{d.index}</span>
                  </span>
                </span>
                <span className="shrink-0 font-mono text-[10.5px] text-muted-foreground tabular-nums">
                  {hits.length ? (
                    <span className="text-foreground">
                      {hits.map((h) => `R${h.index + 1}`).join(' ')} · +{formatBytes(adding)}
                    </span>
                  ) : (
                    `${formatBytes(Math.max(0, usable - reserved))} free`
                  )}
                </span>
              </div>
              <div className="relative flex h-2.5 overflow-hidden rounded-[4px] bg-muted">
                <div className="h-full bg-muted-foreground/35" style={{ width: pct(reserved) }} title={`${formatBytes(reserved)} reserved by other slices`} />
                {adding > 0 && <div className="h-full animate-pulse bg-brand/80" style={{ width: pct(adding) }} title={`${formatBytes(adding)} for this deployment`} />}
                <div aria-hidden className="absolute inset-y-0 border-r border-dashed border-foreground/40" style={{ left: pct(usable) }} title="Scheduler headroom starts here" />
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export function StepPlacement({ draft, update, ctx }: StepProps) {
  const vendorsInFleet = useMemo(() => [...new Set(ctx.devices.map((d) => d.vendor))], [ctx.devices]);
  const engineVendors = ctx.engine?.supported_vendors ?? [];
  const gpus = effectiveGpuCount(draft);
  const preview = ctx.preview;
  const byNode = useMemo(() => {
    const placements = (preview?.placements ?? []).map((candidate, index) => ({ index, candidate }));
    return ctx.nodes
      .filter((n) => n.status !== 'removed' && (!draft.clusterId || n.cluster_id === draft.clusterId))
      .map((n) => ({
        node: n,
        devices: ctx.devices.filter((d) => d.node_id === n.id),
        placements: placements.filter((p) => p.candidate.node_id === n.id),
        rejected: preview?.rejected?.find((r) => r.node_id === n.id)?.reason,
      }));
  }, [ctx.nodes, ctx.devices, preview, draft.clusterId]);
  const maxSlice = useMemo(() => {
    const usable = ctx.devices.map((d) => d.vram_total_bytes * (d.is_unified_memory ? 0.75 : 0.9));
    return Math.max(1, Math.floor(Math.max(0, ...usable) / GiB));
  }, [ctx.devices]);
  const estimate = preview?.compat?.estimated_vram_bytes ?? ctx.autoPick?.check?.estimated_vram_bytes ?? null;

  return (
    <div className="grid items-start gap-6 xl:grid-cols-[minmax(0,1fr)_420px]">
      <div className="space-y-5">
        <div className="grid gap-4 sm:grid-cols-3">
          <Field label="Replicas" hint="Instances serving this model.">
            <Stepper value={draft.replicas} onChange={(v) => update({ replicas: v, maxReplicas: Math.max(draft.maxReplicas, v) })} max={32} label="replicas" />
          </Field>
          <Field label="Min · max" hint="Bounds for rules that grow or shrink it.">
            <div className="flex items-center gap-2">
              <Input type="number" min={0} value={draft.minReplicas} onChange={(e) => update({ minReplicas: Math.max(0, Number(e.target.value)) })} className="h-9 w-16 font-mono text-sm" aria-label="Min replicas" />
              <span className="text-muted-foreground">–</span>
              <Input type="number" min={1} value={draft.maxReplicas} onChange={(e) => update({ maxReplicas: Math.max(1, Number(e.target.value)) })} className="h-9 w-16 font-mono text-sm" aria-label="Max replicas" />
            </div>
          </Field>
          <Field label="Cluster">
            <Select value={draft.clusterId || 'any'} onValueChange={(v) => update({ clusterId: v === 'any' ? '' : String(v) })}>
              <SelectTrigger className="w-full">
                <SelectValue>{(v: unknown) => (v === 'any' ? 'Any cluster' : ctx.clusters.find((c) => c.id === v)?.name ?? 'Cluster')}</SelectValue>
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="any">Any cluster</SelectItem>
                {ctx.clusters.map((c) => (
                  <SelectItem key={c.id} value={c.id} disabled={c.status !== 'active'}>
                    {c.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>
        </div>

        <div className="grid gap-4 sm:grid-cols-3">
          <Field label="Tensor parallel" hint={ctx.caps.tensorParallel ? 'Split layers across GPUs of one node.' : 'Not supported by this engine.'}>
            <Stepper value={draft.tpSize} onChange={(v) => update({ tpSize: v, gpuCount: Math.max(draft.gpuCount, v * draft.ppSize) })} min={1} max={ctx.caps.tensorParallel ? 8 : 1} label="tensor parallel size" />
          </Field>
          <Field label="Pipeline parallel" hint={ctx.caps.pipelineParallel ? 'Stages across GPUs or nodes.' : 'Not supported by this engine.'}>
            <Stepper value={draft.ppSize} onChange={(v) => update({ ppSize: v, gpuCount: Math.max(draft.gpuCount, draft.tpSize * v) })} min={1} max={ctx.caps.pipelineParallel ? 8 : 1} label="pipeline parallel size" />
          </Field>
          <Field label="GPUs per replica" hint={`TP × PP = ${draft.tpSize * draft.ppSize}`}>
            <Stepper value={gpus} onChange={(v) => update({ gpuCount: Math.max(v, draft.tpSize * draft.ppSize) })} min={1} max={8} label="GPUs per replica" />
          </Field>
        </div>

        <Field label="Strategy">
          <div className="grid gap-2 sm:grid-cols-2" role="radiogroup" aria-label="Placement strategy">
            {([
              ['spread', 'Spread', 'One replica per node first, emptiest GPUs — best for failover.'],
              ['pack', 'Pack', 'Fill the busiest GPU that still fits — keeps whole GPUs free for big models.'],
            ] as const).map(([value, title, body]) => (
              <button
                key={value}
                type="button"
                role="radio"
                aria-checked={draft.strategy === value}
                onClick={() => update({ strategy: value })}
                className={cn('rounded-lg border p-3 text-left transition-colors', draft.strategy === value ? 'border-foreground/60 bg-muted/40 ring-1 ring-foreground/50' : 'border-border hover:border-foreground/25')}
              >
                <span className="block text-xs font-medium">{title}</span>
                <span className="block text-[11px] leading-snug text-muted-foreground">{body}</span>
              </button>
            ))}
          </div>
        </Field>

        <Field label="Accelerators" hint={`${ctx.engine?.display_name ?? 'The engine'} supports ${engineVendors.map((v) => DEVICE_VENDOR_LABELS[v]).join(', ') || '—'}. No selection = any of those.`}>
          <div className="flex flex-wrap gap-1.5">
            {(Object.keys(DEVICE_VENDOR_LABELS) as DeviceVendor[]).map((v) => {
              const on = draft.vendors.includes(v);
              const usable = engineVendors.includes(v);
              const present = vendorsInFleet.includes(v);
              return (
                <button
                  key={v}
                  type="button"
                  disabled={!usable}
                  aria-pressed={on}
                  onClick={() => update({ vendors: on ? draft.vendors.filter((x) => x !== v) : [...draft.vendors, v] })}
                  className={cn('inline-flex items-center gap-1.5 rounded-md border px-2 py-1 text-[11px] transition-colors disabled:cursor-not-allowed disabled:opacity-35', on ? 'border-foreground/60 bg-muted' : 'border-border hover:border-foreground/25', !present && usable && 'border-dashed')}
                  title={!present ? 'No devices of this vendor in the fleet' : undefined}
                >
                  <VendorBadge vendor={v} withLabel={false} />
                  {DEVICE_VENDOR_LABELS[v]}
                </button>
              );
            })}
          </div>
        </Field>

        <Field label="Pin to nodes" hint="Optional. Combined with the label selector below.">
          <div className="flex flex-wrap gap-1.5">
            {ctx.nodes
              .filter((n) => n.status !== 'removed')
              .map((n) => {
                const on = draft.pinNodeIds.includes(n.id);
                return (
                  <button
                    key={n.id}
                    type="button"
                    aria-pressed={on}
                    onClick={() => update({ pinNodeIds: on ? draft.pinNodeIds.filter((x) => x !== n.id) : [...draft.pinNodeIds, n.id], pinDeviceIds: on ? draft.pinDeviceIds.filter((id) => ctx.devices.find((d) => d.id === id)?.node_id !== n.id) : draft.pinDeviceIds })}
                    className={cn('rounded-md border px-2 py-1 text-[11px] transition-colors', on ? 'border-foreground/60 bg-muted font-medium' : 'border-border text-muted-foreground hover:border-foreground/25 hover:text-foreground')}
                  >
                    {n.name}
                    <span className="ml-1 text-muted-foreground">· {n.status}</span>
                  </button>
                );
              })}
          </div>
          {draft.pinDeviceIds.length > 0 && (
            <p className="flex flex-wrap items-center gap-1.5 pt-1 text-[11px] text-muted-foreground">
              Pinned GPU{draft.pinDeviceIds.length === 1 ? '' : 's'}:
              {draft.pinDeviceIds.map((id) => {
                const d = ctx.devices.find((x) => x.id === id);
                return (
                  <span key={id} className="inline-flex items-center gap-1 rounded-sm border border-border px-1.5 py-0.5 font-mono text-[10.5px] text-foreground">
                    {d ? `${d.node_name} · ${d.name} #${d.index}` : id.slice(0, 8)}
                    <button type="button" className="text-muted-foreground hover:text-foreground" onClick={() => update({ pinDeviceIds: draft.pinDeviceIds.filter((x) => x !== id) })} aria-label="Unpin GPU">
                      ×
                    </button>
                  </span>
                );
              })}
            </p>
          )}
        </Field>

        <Field label="Node label selector" hint="Every label must match, e.g. zone = a.">
          <KeyValueEditor rows={draft.labelSelector} onChange={(rows) => update({ labelSelector: rows })} keyPlaceholder="zone" valuePlaceholder="a" addLabel="Add label" />
        </Field>

        <div className={cn('space-y-3 rounded-lg border border-border p-3', !ctx.caps.slices && 'opacity-60')}>
          <label className="flex items-start justify-between gap-4">
            <span>
              <span className="block text-xs font-medium">GPU slice</span>
              <span className="block text-[11px] text-muted-foreground">
                {ctx.caps.slices ? 'Reserve a fixed VRAM / compute share per GPU instead of the scheduler estimate, so several models share a card.' : `${ctx.engine?.display_name ?? 'This engine'} takes whole GPUs.`}
              </span>
            </span>
            <Switch checked={draft.sliceEnabled} disabled={!ctx.caps.slices} onCheckedChange={(v) => update({ sliceEnabled: v })} aria-label="Use a GPU slice" />
          </label>
          {draft.sliceEnabled && (
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="VRAM per GPU" aside={<span className="font-mono text-xs tabular-nums">{+draft.sliceGiB.toFixed(2)} GiB</span>} hint={estimate ? `Estimate for weights + KV: ~${formatBytes(estimate)}.` : undefined}>
                <Slider min={1} max={maxSlice} step={0.5} value={[draft.sliceGiB]} onValueChange={(v) => update({ sliceGiB: Array.isArray(v) ? Number(v[0]) : Number(v) })} aria-label="Slice VRAM" />
              </Field>
              <Field label="Compute share" aside={<span className="font-mono text-xs tabular-nums">{draft.slicePct}%</span>}>
                <Slider min={5} max={100} step={5} value={[draft.slicePct]} onValueChange={(v) => update({ slicePct: Array.isArray(v) ? Number(v[0]) : Number(v) })} aria-label="Slice compute" />
              </Field>
            </div>
          )}
        </div>
      </div>

      <div className="space-y-3 xl:sticky xl:top-4">
        <div
          className={cn(
            'flex items-start gap-2.5 rounded-lg border px-3 py-2.5 text-xs',
            !preview ? 'border-border bg-muted/30' : preview.feasible ? 'border-success/30 bg-success/10' : 'border-warning/40 bg-warning/10',
          )}
        >
          {ctx.previewLoading ? (
            <Loader2 className="mt-0.5 size-4 shrink-0 animate-spin text-muted-foreground" />
          ) : preview?.feasible ? (
            <span className="mt-1 size-2 shrink-0 rounded-full bg-success" />
          ) : (
            <TriangleAlert className="mt-0.5 size-4 shrink-0 text-warning" />
          )}
          <span>
            <span className="block font-medium">
              {!preview
                ? 'Placement preview'
                : preview.feasible
                  ? `${preview.placements.length} of ${draft.replicas} replica${draft.replicas === 1 ? '' : 's'} placed`
                  : `Only ${preview.placements.length} of ${draft.replicas} replicas fit`}
            </span>
            <span className="text-muted-foreground">
              Scheduler dry run with the current settings{estimate ? ` · ~${formatBytes(estimate)} per GPU` : ''}. Highlighted bars are what this deployment would take.
            </span>
          </span>
        </div>
        <div className="space-y-2">
          {byNode.map((n) => (
            <NodePreview key={n.node.id} name={n.node.name} devices={n.devices} placements={n.placements} rejected={n.rejected ?? (n.node.status !== 'online' ? `node is ${n.node.status}` : undefined)} />
          ))}
        </div>
        {(preview?.would_evict?.length ?? 0) > 0 && (
          <div className="rounded-lg border border-warning/40 bg-warning/10 p-3 text-xs">
            <p className="flex items-center gap-1.5 font-medium">
              <CircleAlert className="size-3.5 text-warning" />
              Make-room would unload {preview?.would_evict?.length} evictable instance(s)
            </p>
            <ul className="mt-1 space-y-0.5 text-[11px] text-muted-foreground">
              {preview?.would_evict?.map((i) => (
                <li key={i.id}>
                  {i.deployment_name} on {i.node_name}
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </div>
  );
}
