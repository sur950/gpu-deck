'use client';

import { useMemo, useState } from 'react';
import { useRouter } from 'next/navigation';
import { Layers, Rocket, SlidersHorizontal } from 'lucide-react';
import { Input } from '@/components/ui/input';
import { Slider } from '@/components/ui/slider';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { FormDialog, Field } from '@/components/shared/form-dialog';
import { Segmented } from '@/components/automation/segmented';
import { useCreateDeviceSlice, useUpdateDeviceSlice } from '@/hooks/api/fleet';
import { useDeployments } from '@/hooks/api/models-deployments';
import { formatBytes } from '@/utils/format';
import type { Device, DeviceSlice } from '@/types/fleet';

const GiB = 1024 ** 3;

/** Usable VRAM on a device for slices: 90 % of discrete VRAM, 75 % of unified memory. */
export function usableVram(device: Pick<Device, 'vram_total_bytes' | 'is_unified_memory'>): number {
  return device.vram_total_bytes * (device.is_unified_memory ? 0.75 : 0.9);
}

/**
 * VRAM still free on the device, optionally ignoring one slice (resize). The control plane's
 * figure counts headroom, GPU Deck reservations and memory used outside GPU Deck (external
 * servers, other processes); without it (older control plane) only reservations count.
 */
export function freeVram(device: Device, exceptSliceId?: string): number {
  const live = (device.slices ?? []).filter((s) => s.status !== 'released');
  const except = exceptSliceId ? (live.find((s) => s.id === exceptSliceId)?.vram_bytes ?? 0) : 0;
  if (typeof device.vram_free_bytes === 'number') return Math.max(0, device.vram_free_bytes + except);
  const reserved = live.filter((s) => s.id !== exceptSliceId).reduce((sum, s) => sum + s.vram_bytes, 0);
  return Math.max(0, usableVram(device) - reserved);
}

/** Who holds a slice: a deployment replica, an SSH GPU instance, or nobody (standalone reservation). */
export function sliceOwner(slice: DeviceSlice): 'deployment' | 'gpu_instance' | 'reservation' {
  if (slice.deployment_id) return 'deployment';
  if (slice.gpu_instance_id) return 'gpu_instance';
  return 'reservation';
}

function sliderValue(v: number | readonly number[]): number {
  return Array.isArray(v) ? Number(v[0]) : Number(v);
}

const halfGiB = (bytes: number) => Math.floor((bytes / GiB) * 2) / 2;

function SizeControls({
  vramGiB,
  setVramGiB,
  maxGiB,
  compute,
  setCompute,
}: {
  vramGiB: number;
  setVramGiB: (v: number) => void;
  maxGiB: number;
  compute: number;
  setCompute: (v: number) => void;
}) {
  return (
    <>
      <Field label="VRAM" aside={<span className="font-mono text-xs tabular-nums">{vramGiB.toFixed(1)} GiB</span>} hint={`Up to ${maxGiB.toFixed(1)} GiB is free on this GPU (scheduler headroom, GPU Deck reservations and memory used outside GPU Deck kept aside).`}>
        <div className="flex items-center gap-3">
          <Slider min={0.5} max={Math.max(0.5, maxGiB)} step={0.5} value={[Math.min(vramGiB, Math.max(0.5, maxGiB))]} onValueChange={(v) => setVramGiB(sliderValue(v))} aria-label="VRAM in GiB" />
          <Input type="number" min={0.5} max={maxGiB} step={0.5} value={vramGiB} onChange={(e) => setVramGiB(Number(e.target.value))} className="h-8 w-20 font-mono text-xs" aria-label="VRAM GiB" />
        </div>
      </Field>
      <Field label="Compute share" aside={<span className="font-mono text-xs tabular-nums">{compute}%</span>} hint="Enforced through MPS / MIG on NVIDIA, CU masks on AMD, process priority elsewhere. 100 % = no cap.">
        <Slider min={5} max={100} step={5} value={[compute]} onValueChange={(v) => setCompute(sliderValue(v))} aria-label="Compute percent" />
      </Field>
    </>
  );
}

/**
 * Resize a slice in place (PATCH /devices/{id}/slices/{slice_id}). A
 * standalone reservation is resized immediately after a capacity check; a
 * deployment's slice changes the deployment's slice size, so every replica is
 * replaced one at a time (rolling update).
 */
export function ResizeSliceDialog({ slice, device, open, onOpenChange }: { slice: DeviceSlice; device: Device; open: boolean; onOpenChange: (open: boolean) => void }) {
  const update = useUpdateDeviceSlice();
  const [name, setName] = useState(slice.name ?? '');
  const [vramGiB, setVramGiB] = useState(Math.round((slice.vram_bytes / GiB) * 2) / 2);
  const [compute, setCompute] = useState(slice.compute_pct);
  const maxGiB = halfGiB(freeVram(device, slice.id));
  const owner = sliceOwner(slice);
  const unchanged = Math.round(vramGiB * GiB) === slice.vram_bytes && compute === slice.compute_pct && name === (slice.name ?? '');

  return (
    <FormDialog
      open={open}
      onOpenChange={onOpenChange}
      icon={<SlidersHorizontal className="size-4 text-muted-foreground" />}
      title={`Resize ${slice.name ?? 'slice'}`}
      description={
        owner === 'deployment'
          ? 'This slice belongs to a deployment, so the new size applies to every replica: they are swapped one at a time (drain → unload → load) and traffic keeps flowing.'
          : 'A standalone reservation: resized right away if the GPU has room.'
      }
      submitLabel={owner === 'deployment' ? 'Apply rolling resize' : 'Resize slice'}
      isPending={update.isPending}
      submitDisabled={unchanged || vramGiB <= 0 || vramGiB > maxGiB}
      footerNote={`Now ${formatBytes(slice.vram_bytes)} · ${slice.compute_pct}%`}
      onSubmit={() =>
        update.mutate(
          {
            deviceId: device.id,
            sliceId: slice.id,
            body: {
              vram_bytes: Math.round(vramGiB * GiB),
              compute_pct: compute,
              ...(owner === 'reservation' && name !== (slice.name ?? '') ? { name: name || null } : {}),
            },
          },
          { onSuccess: () => onOpenChange(false) },
        )
      }
    >
      {owner === 'reservation' && (
        <Field label="Name" htmlFor="slice-rename">
          <Input id="slice-rename" value={name} onChange={(e) => setName(e.target.value)} placeholder="notebook-reservation" className="font-mono text-sm" />
        </Field>
      )}
      <SizeControls vramGiB={vramGiB} setVramGiB={setVramGiB} maxGiB={maxGiB} compute={compute} setCompute={setCompute} />
    </FormDialog>
  );
}

type NewSliceMode = 'reserve' | 'replica';

/**
 * Carve a slice on this GPU (POST /devices/{id}/slices): either a standalone
 * VRAM/compute reservation the scheduler honours (an SSH GPU instance can
 * take it later), or one more replica of a deployment that runs on slices,
 * placed here with that deployment's slice size.
 */
export function NewSliceDialog({ device, open, onOpenChange }: { device: Device; open: boolean; onOpenChange: (open: boolean) => void }) {
  const router = useRouter();
  const create = useCreateDeviceSlice();
  const deployments = useDeployments({ page_size: 200, origin: 'managed' }, { live: false });
  const maxGiB = halfGiB(freeVram(device));
  const [mode, setMode] = useState<NewSliceMode>('reserve');
  const [name, setName] = useState('');
  const [vramGiB, setVramGiB] = useState(Math.min(8, Math.max(0.5, maxGiB)));
  const [compute, setCompute] = useState(50);
  const [deploymentId, setDeploymentId] = useState('');

  const sliced = useMemo(
    () => (deployments.data?.items ?? []).filter((d) => !!d.slice_vram_bytes && (!d.device_vendor_filter?.length || d.device_vendor_filter.includes(device.vendor))),
    [deployments.data, device.vendor],
  );
  const deployment = sliced.find((d) => d.id === deploymentId);
  const replicaFits = !!deployment && (deployment.slice_vram_bytes ?? 0) <= freeVram(device);
  const nameValid = name === '' || /^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,254}$/.test(name);
  const disabled = mode === 'reserve' ? !nameValid || vramGiB <= 0 || vramGiB > maxGiB : !deployment || !replicaFits;

  return (
    <FormDialog
      open={open}
      onOpenChange={onOpenChange}
      icon={<Layers className="size-4 text-muted-foreground" />}
      title={`New slice on ${device.name} #${device.index}`}
      description="Reserve part of this GPU, or place one more replica of a sliced deployment here."
      submitLabel={mode === 'reserve' ? 'Reserve slice' : 'Place replica here'}
      widthClass="sm:max-w-xl"
      isPending={create.isPending}
      submitDisabled={disabled}
      footerNote={
        <button
          type="button"
          className="inline-flex items-center gap-1 underline underline-offset-2 hover:text-foreground"
          onClick={() => router.push(`/deployments/new?node_id=${device.node_id}&device_id=${device.id}&slice_gib=${vramGiB}&slice_pct=${compute}`)}
        >
          <Rocket className="size-3" />
          Deploy a new model here instead
        </button>
      }
      onSubmit={() =>
        create.mutate(
          {
            deviceId: device.id,
            body:
              mode === 'reserve'
                ? { name: name || null, vram_bytes: Math.round(vramGiB * GiB), compute_pct: compute }
                : { deployment_id: deploymentId, vram_bytes: deployment?.slice_vram_bytes ?? null, compute_pct: deployment?.slice_compute_pct ?? null },
          },
          { onSuccess: () => onOpenChange(false) },
        )
      }
    >
      <Segmented
        variant="block"
        size="sm"
        aria-label="Slice type"
        value={mode}
        onChange={setMode}
        options={[
          { value: 'reserve', label: 'Reservation', icon: Layers, hint: 'Hold VRAM + compute' },
          { value: 'replica', label: 'Deployment replica', icon: Rocket, hint: 'replicas + 1 here', disabled: sliced.length === 0 },
        ]}
      />
      {mode === 'reserve' ? (
        <>
          <Field label="Name" hint={nameValid ? 'Optional. An SSH GPU instance can take this slice later.' : 'Letters, digits, dots, dashes, colons and underscores.'} htmlFor="slice-name">
            <Input id="slice-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="notebook-reservation" className="font-mono text-sm" aria-invalid={!nameValid} />
          </Field>
          {maxGiB <= 0 ? (
            <p className="rounded-md bg-destructive/10 p-3 text-xs text-destructive">No free VRAM left on this GPU. Resize or release another slice first.</p>
          ) : (
            <SizeControls vramGiB={vramGiB} setVramGiB={setVramGiB} maxGiB={maxGiB} compute={compute} setCompute={setCompute} />
          )}
        </>
      ) : (
        <>
          <Field label="Deployment" hint={deployment ? `Each replica gets ${formatBytes(deployment.slice_vram_bytes ?? 0)} · ${deployment.slice_compute_pct ?? 100}% — the deployment's slice size.` : 'Deployments that run on GPU slices.'}>
            <Select value={deploymentId || null} onValueChange={(v) => setDeploymentId(String(v ?? ''))}>
              <SelectTrigger className="w-full">
                <SelectValue placeholder="Pick a deployment">{(v: unknown) => sliced.find((d) => d.id === v)?.name ?? 'Pick a deployment'}</SelectValue>
              </SelectTrigger>
              <SelectContent>
                {sliced.map((d) => (
                  <SelectItem key={d.id} value={d.id}>
                    {d.name}
                    <span className="ml-1 text-muted-foreground">
                      · {formatBytes(d.slice_vram_bytes ?? 0)} · {d.replicas} replica{d.replicas === 1 ? '' : 's'}
                    </span>
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>
          {deployment && !replicaFits && (
            <p className="rounded-md bg-warning/10 p-3 text-xs text-warning-dark dark:text-warning-light">
              A replica needs {formatBytes(deployment.slice_vram_bytes ?? 0)} but only {formatBytes(freeVram(device))} is free here.
            </p>
          )}
        </>
      )}
    </FormDialog>
  );
}
