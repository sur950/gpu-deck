'use client';

import { useMemo, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { useForm, useWatch } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import { ArrowLeft, Cpu, HardDrive, Layers, Loader2, Server, SlidersHorizontal, SquareTerminal } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { Slider } from '@/components/ui/slider';
import { Switch } from '@/components/ui/switch';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { Form, FormControl, FormDescription, FormField, FormItem, FormLabel, FormMessage } from '@/components/ui/form';
import { PageContainer } from '@/components/shared/PageContainer';
import { PageHeader } from '@/components/shared/PageHeader';
import { KeyValueEditor, rowsToRecord, type KeyValueRow } from '@/components/shared/key-value-editor';
import { Segmented } from '@/components/automation/segmented';
import { usableVram } from '@/components/fleet/slice-dialogs';
import { useNow } from '@/components/fleet/expiry-countdown';
import { SshKeysField } from '@/components/gpu-instances/ssh-keys-field';
import { IDLE_OPTIONS, TTL_OPTIONS, formatSpan } from '@/components/gpu-instances/meta';
import { cidrError } from '@/components/security/cidr';
import { useClusters, useDevice, useDevices, useNodes } from '@/hooks/api/fleet';
import { useCreateGpuInstance } from '@/hooks/use-gpu-instances';
import { parseSshKeyLines } from '@/lib/ssh-keys';
import { cn } from '@/lib/utils';
import { formatBytes } from '@/utils/format';
import { DEVICE_VENDOR_LABELS, type DeviceVendor } from '@/types/fleet';
import type { GpuInstanceCreate } from '@/types/gpu-instances';

const GiB = 1024 ** 3;
const ANY = 'any';

const schema = z
  .object({
    name: z.string().regex(/^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$/, 'Lowercase letters, digits and dashes (a DNS label, max 63)'),
    description: z.string().max(2048),
    image: z.string().max(512),
    containerPort: z.string().regex(/^$|^\d{1,5}$/, 'A port number'),
    gpuMode: z.enum(['whole', 'slice', 'none']),
    gpuCount: z.number().int().min(1).max(8),
    sliceGiB: z.number().min(0.5),
    slicePct: z.number().int().min(5).max(100),
    vendor: z.string(),
    clusterId: z.string(),
    nodeId: z.string(),
    keys: z.string().superRefine((text, ctx) => {
      const lines = parseSshKeyLines(text);
      const bad = lines.find((l) => !l.result.ok);
      if (bad && !bad.result.ok) ctx.addIssue({ code: 'custom', message: `Line ${bad.lineNo}: ${bad.result.error}` });
      else if (lines.length === 0) ctx.addIssue({ code: 'custom', message: 'Add at least one public key — it is the only way in' });
      else if (lines.length > 64) ctx.addIssue({ code: 'custom', message: 'At most 64 keys' });
    }),
    sshUser: z.string().regex(/^[a-z_][a-z0-9_-]{0,31}$/, 'Lowercase user name').refine((u) => u !== 'root', 'root is not allowed'),
    cidrs: z.string().superRefine((text, ctx) => {
      const bad = splitList(text).find((c) => cidrError(c));
      if (bad) ctx.addIssue({ code: 'custom', message: `"${bad}" is not an address or CIDR range` });
    }),
    ttl: z.number().int().min(0),
    idle: z.number().int().min(0),
    waitMinutes: z.number().int().min(0).max(1440),
    cpuCores: z.string().regex(/^$|^\d+(\.\d+)?$/, 'A number'),
    memoryGiB: z.string().regex(/^$|^\d+(\.\d+)?$/, 'A number'),
    shmGiB: z.string().regex(/^$|^\d+(\.\d+)?$/, 'A number'),
    keepWorkspace: z.boolean(),
  })
  .refine((v) => v.gpuMode !== 'slice' || v.slicePct <= 100, { path: ['slicePct'] });

type FormValues = z.infer<typeof schema>;

function splitList(text: string): string[] {
  return text
    .split(/[\s,]+/)
    .map((s) => s.trim())
    .filter(Boolean);
}

function SectionTitle({ icon: Icon, title, description }: { icon: typeof Cpu; title: string; description: string }) {
  return (
    <CardHeader className="border-b border-border px-5 py-4">
      <CardTitle className="flex items-center gap-2 text-sm font-semibold">
        <Icon className="size-4 text-muted-foreground" />
        {title}
      </CardTitle>
      <CardDescription>{description}</CardDescription>
    </CardHeader>
  );
}

export function GpuInstanceNew() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const create = useCreateGpuInstance();
  const clusters = useClusters({ page_size: 100 });
  const [env, setEnv] = useState<KeyValueRow[]>([]);
  const [labels, setLabels] = useState<KeyValueRow[]>([]);
  const now = useNow(30_000);
  // Arriving from a GPU's slice table: take that standalone reservation.
  const sliceId = searchParams.get('slice_id');
  const pinnedDevice = useDevice(sliceId ? (searchParams.get('device_id') ?? '') : '');
  const pinnedSlice = sliceId ? pinnedDevice.data?.slices?.find((x) => x.id === sliceId) : undefined;

  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    mode: 'onTouched',
    defaultValues: {
      name: '',
      description: '',
      image: '',
      containerPort: '',
      gpuMode: searchParams.get('device_id') ? 'slice' : 'whole',
      gpuCount: 1,
      sliceGiB: 16,
      slicePct: 50,
      vendor: ANY,
      clusterId: ANY,
      nodeId: searchParams.get('node_id') ?? ANY,
      keys: '',
      sshUser: 'gpudeck',
      cidrs: '',
      ttl: 8 * 3600,
      idle: 0,
      waitMinutes: 0,
      cpuCores: '',
      memoryGiB: '',
      shmGiB: '',
      keepWorkspace: false,
    },
  });
  const v = useWatch({ control: form.control }) as FormValues;

  const nodes = useNodes({ cluster_id: v.clusterId === ANY ? undefined : v.clusterId, page_size: 200 }, { live: false });
  const devices = useDevices({ page_size: 500, cluster_id: v.clusterId === ANY ? undefined : v.clusterId });

  // Capacity hint: the largest slice / idle GPUs that fit the current filters.
  const capacity = useMemo(() => {
    const list = (devices.data?.items ?? []).filter(
      (d) => (v.vendor === ANY || d.vendor === v.vendor) && (v.nodeId === ANY || d.node_id === v.nodeId) && d.health !== 'unhealthy',
    );
    const free = list.map((d) => {
      const reserved = (d.slices ?? []).filter((s) => s.status !== 'released').reduce((sum, s) => sum + s.vram_bytes, 0);
      return { device: d, free: Math.max(0, usableVram(d) - reserved), idle: reserved === 0 };
    });
    const best = free.reduce<(typeof free)[number] | null>((a, b) => (!a || b.free > a.free ? b : a), null);
    return { total: list.length, idle: free.filter((f) => f.idle).length, best };
  }, [devices.data, v.vendor, v.nodeId]);

  const keyCount = parseSshKeyLines(v.keys ?? '').filter((l) => l.result.ok).length;
  const node = (nodes.data?.items ?? []).find((n) => n.id === v.nodeId);
  const sliceTooBig = !sliceId && v.gpuMode === 'slice' && capacity.best !== null && v.sliceGiB * GiB > capacity.best.free;
  const wholeTooMany = !sliceId && v.gpuMode === 'whole' && capacity.total > 0 && v.gpuCount > capacity.idle;

  function onSubmit(values: FormValues) {
    const nodeSelector: Record<string, unknown> = {};
    if (values.nodeId !== ANY) nodeSelector.node_ids = [values.nodeId];
    const deviceId = searchParams.get('device_id');
    if (deviceId && values.nodeId !== ANY && !sliceId) nodeSelector.device_ids = [deviceId];
    const limits = {
      ...(values.cpuCores ? { cpu_cores: Number(values.cpuCores) } : {}),
      ...(values.memoryGiB ? { memory_bytes: Math.round(Number(values.memoryGiB) * GiB) } : {}),
      ...(values.shmGiB ? { shm_bytes: Math.round(Number(values.shmGiB) * GiB) } : {}),
    };
    const body: GpuInstanceCreate = {
      name: values.name,
      description: values.description.trim() || null,
      image: values.image.trim() || null,
      container_port: values.containerPort ? Number(values.containerPort) : null,
      cluster_id: values.clusterId === ANY ? null : values.clusterId,
      gpu_count: sliceId ? 1 : values.gpuMode === 'none' ? 0 : values.gpuMode === 'slice' ? 1 : values.gpuCount,
      slice_id: sliceId,
      slice_vram_bytes: !sliceId && values.gpuMode === 'slice' ? Math.round(values.sliceGiB * GiB) : null,
      slice_compute_pct: !sliceId && values.gpuMode === 'slice' ? values.slicePct : null,
      device_vendor: values.vendor === ANY ? null : (values.vendor as DeviceVendor),
      node_selector: nodeSelector,
      authorized_keys: parseSshKeyLines(values.keys).flatMap((l) => (l.result.ok ? [l.result.key.line] : [])),
      ssh_user: values.sshUser,
      allowed_source_cidrs: splitList(values.cidrs),
      limits,
      env: rowsToRecord(env),
      labels: rowsToRecord(labels),
      volumes: values.keepWorkspace ? [{ type: 'volume', source: `${values.name}-workspace`, target: '/workspace', keep: true, read_only: false }] : [],
      ttl_seconds: values.ttl || null,
      idle_timeout_seconds: values.idle || null,
      wait_for_capacity_seconds: values.waitMinutes * 60,
    };
    create.mutate(body, {
      onSuccess: (instance) => router.push(`/gpu-instances/${instance.id}`),
      onError: (err) => form.setError('root', { message: err instanceof Error ? err.message : 'Request failed' }),
    });
  }

  const stopsAt = v.ttl ? new Date(now + v.ttl * 1000) : null;

  return (
    <PageContainer className="max-w-6xl">
      <PageHeader
        title="Request an SSH GPU instance"
        description="A container with sshd and the GPU (or slice) you ask for, placed by the scheduler. Billed as GPU minutes while it runs."
        actions={
          <Button variant="outline" size="sm" className="rounded-sm" onClick={() => router.push('/gpu-instances')}>
            <ArrowLeft className="size-4" />
            Back to instances
          </Button>
        }
      />

      <Form {...form}>
        <form onSubmit={form.handleSubmit(onSubmit)} className="grid items-start gap-6 lg:grid-cols-[minmax(0,1fr)_340px]">
          <div className="space-y-6">
            <Card className="gap-0 rounded-md py-0 shadow-premium">
              <SectionTitle icon={SquareTerminal} title="Instance" description="Name it, and optionally bring your own image (it must run sshd)." />
              <CardContent className="grid gap-4 px-5 py-5 sm:grid-cols-2">
                <FormField
                  control={form.control}
                  name="name"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Name</FormLabel>
                      <FormControl>
                        <Input {...field} onChange={(e) => field.onChange(e.target.value.toLowerCase())} placeholder="alice-dev" className="font-mono text-sm" autoFocus />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
                <FormField
                  control={form.control}
                  name="sshUser"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>SSH user</FormLabel>
                      <FormControl>
                        <Input {...field} className="font-mono text-sm" />
                      </FormControl>
                      <FormDescription>Created in the container; never root.</FormDescription>
                      <FormMessage />
                    </FormItem>
                  )}
                />
                <FormField
                  control={form.control}
                  name="image"
                  render={({ field }) => (
                    <FormItem className="sm:col-span-2">
                      <FormLabel>Image</FormLabel>
                      <FormControl>
                        <Input {...field} placeholder="ghcr.io/sur950/gpu-deck-ssh:1 (server default)" className="font-mono text-sm" />
                      </FormControl>
                      <FormDescription>Leave empty for the server default. Custom images must start sshd and accept keys in the user&apos;s authorized_keys.</FormDescription>
                      <FormMessage />
                    </FormItem>
                  )}
                />
                <FormField
                  control={form.control}
                  name="description"
                  render={({ field }) => (
                    <FormItem className="sm:col-span-2">
                      <FormLabel>Description</FormLabel>
                      <FormControl>
                        <Textarea {...field} rows={2} placeholder="What is it for? Shown in the list and the audit log." />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
              </CardContent>
            </Card>

            <Card className="gap-0 rounded-md py-0 shadow-premium">
              <SectionTitle icon={Cpu} title="GPU" description="Whole GPUs are exclusive; a slice shares one GPU with other work (VRAM + compute caps)." />
              <CardContent className="space-y-5 px-5 py-5">
                {sliceId ? (
                  <div className="flex items-start gap-3 rounded-md border border-border bg-muted/40 px-4 py-3">
                    <Layers className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
                    <div className="text-[13px]">
                      <p className="font-medium">Uses the reserved slice {pinnedSlice?.name ? <span className="font-mono">{pinnedSlice.name}</span> : ''}</p>
                      <p className="text-xs text-muted-foreground">
                        {pinnedSlice ? `${formatBytes(pinnedSlice.vram_bytes)} · ${pinnedSlice.compute_pct}% compute on ${pinnedDevice.data?.node_name ?? 'node'} / ${pinnedDevice.data?.name ?? 'GPU'} #${pinnedDevice.data?.index ?? ''}` : 'Loading the slice…'}
                      </p>
                    </div>
                    <Button type="button" variant="ghost" size="xs" className="ml-auto" onClick={() => router.replace('/gpu-instances/new')}>
                      Choose size instead
                    </Button>
                  </div>
                ) : (
                <>
                <FormField
                  control={form.control}
                  name="gpuMode"
                  render={({ field }) => (
                    <FormItem>
                      <Segmented
                        variant="block"
                        aria-label="GPU shape"
                        value={field.value}
                        onChange={field.onChange}
                        options={[
                          { value: 'whole', label: 'Whole GPUs', icon: Cpu, hint: 'Exclusive devices' },
                          { value: 'slice', label: 'GPU slice', icon: Layers, hint: 'Part of one GPU' },
                          { value: 'none', label: 'No GPU', icon: Server, hint: 'CPU-only container' },
                        ]}
                      />
                    </FormItem>
                  )}
                />
                {v.gpuMode === 'whole' && (
                  <FormField
                    control={form.control}
                    name="gpuCount"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel className="flex items-center justify-between">
                          GPUs <span className="font-mono text-xs tabular-nums">{field.value}</span>
                        </FormLabel>
                        <Slider min={1} max={8} step={1} value={[field.value]} onValueChange={(x) => field.onChange(Array.isArray(x) ? Number(x[0]) : Number(x))} aria-label="GPU count" />
                        <FormDescription className={cn(wholeTooMany && 'text-warning-dark dark:text-warning-light')}>
                          {capacity.total === 0 ? 'No matching GPUs reported yet.' : `${capacity.idle} of ${capacity.total} matching GPUs are idle right now.`}
                        </FormDescription>
                      </FormItem>
                    )}
                  />
                )}
                {v.gpuMode === 'slice' && (
                  <div className="grid gap-5 sm:grid-cols-2">
                    <FormField
                      control={form.control}
                      name="sliceGiB"
                      render={({ field }) => (
                        <FormItem>
                          <FormLabel className="flex items-center justify-between">
                            VRAM <span className="font-mono text-xs tabular-nums">{field.value} GiB</span>
                          </FormLabel>
                          <Slider min={0.5} max={80} step={0.5} value={[field.value]} onValueChange={(x) => field.onChange(Array.isArray(x) ? Number(x[0]) : Number(x))} aria-label="Slice VRAM in GiB" />
                          <FormDescription className={cn(sliceTooBig && 'text-warning-dark dark:text-warning-light')}>
                            {capacity.best
                              ? `Largest free: ${formatBytes(capacity.best.free)} on ${capacity.best.device.node_name ?? 'node'} / ${capacity.best.device.name.replace(/^NVIDIA |^AMD /, '')} #${capacity.best.device.index}.`
                              : 'No matching GPUs reported yet.'}
                          </FormDescription>
                        </FormItem>
                      )}
                    />
                    <FormField
                      control={form.control}
                      name="slicePct"
                      render={({ field }) => (
                        <FormItem>
                          <FormLabel className="flex items-center justify-between">
                            Compute share <span className="font-mono text-xs tabular-nums">{field.value}%</span>
                          </FormLabel>
                          <Slider min={5} max={100} step={5} value={[field.value]} onValueChange={(x) => field.onChange(Array.isArray(x) ? Number(x[0]) : Number(x))} aria-label="Compute percent" />
                          <FormDescription>MPS / MIG on NVIDIA, CU masks on AMD. 100% = no cap.</FormDescription>
                        </FormItem>
                      )}
                    />
                  </div>
                )}
                </>
                )}
                <div className="grid gap-4 sm:grid-cols-3">
                  <FormField
                    control={form.control}
                    name="vendor"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Vendor</FormLabel>
                        <Select value={field.value} onValueChange={(x) => field.onChange(String(x ?? ANY))}>
                          <SelectTrigger className="w-full">
                            <SelectValue>{(x: unknown) => (x === ANY ? 'Any vendor' : DEVICE_VENDOR_LABELS[x as DeviceVendor])}</SelectValue>
                          </SelectTrigger>
                          <SelectContent>
                            <SelectItem value={ANY}>Any vendor</SelectItem>
                            {(Object.keys(DEVICE_VENDOR_LABELS) as DeviceVendor[]).map((k) => (
                              <SelectItem key={k} value={k}>
                                {DEVICE_VENDOR_LABELS[k]}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                        {v.vendor === 'apple' && <FormDescription className="text-warning-dark dark:text-warning-light">Containers on macOS cannot see the Apple GPU — the instance runs CPU-only.</FormDescription>}
                      </FormItem>
                    )}
                  />
                  <FormField
                    control={form.control}
                    name="clusterId"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Cluster</FormLabel>
                        <Select value={field.value} onValueChange={(x) => { field.onChange(String(x ?? ANY)); form.setValue('nodeId', ANY); }}>
                          <SelectTrigger className="w-full">
                            <SelectValue>{(x: unknown) => (x === ANY ? 'Any cluster' : (clusters.data?.items.find((c) => c.id === x)?.name ?? 'Cluster'))}</SelectValue>
                          </SelectTrigger>
                          <SelectContent>
                            <SelectItem value={ANY}>Any cluster</SelectItem>
                            {(clusters.data?.items ?? []).filter((c) => c.status === 'active').map((c) => (
                              <SelectItem key={c.id} value={c.id}>
                                {c.name}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      </FormItem>
                    )}
                  />
                  <FormField
                    control={form.control}
                    name="nodeId"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Node</FormLabel>
                        <Select value={field.value} onValueChange={(x) => field.onChange(String(x ?? ANY))}>
                          <SelectTrigger className="w-full">
                            <SelectValue>{(x: unknown) => (x === ANY ? 'Scheduler picks' : (nodes.data?.items.find((n) => n.id === x)?.name ?? 'Node'))}</SelectValue>
                          </SelectTrigger>
                          <SelectContent>
                            <SelectItem value={ANY}>Scheduler picks</SelectItem>
                            {(nodes.data?.items ?? []).filter((n) => n.status === 'online').map((n) => (
                              <SelectItem key={n.id} value={n.id}>
                                {n.name}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      </FormItem>
                    )}
                  />
                </div>
              </CardContent>
            </Card>

            <Card className="gap-0 rounded-md py-0 shadow-premium">
              <SectionTitle icon={SquareTerminal} title="Access" description="Public keys only — there is no password login. The port is exposed through the node agent." />
              <CardContent className="space-y-4 px-5 py-5">
                <FormField
                  control={form.control}
                  name="keys"
                  render={({ field, fieldState }) => (
                    <FormItem>
                      <FormLabel>Authorized keys</FormLabel>
                      <SshKeysField id="ssh-keys" value={field.value} onChange={(x) => { field.onChange(x); void form.trigger('keys'); }} invalid={!!fieldState.error} />
                      <FormMessage />
                    </FormItem>
                  )}
                />
                <FormField
                  control={form.control}
                  name="cidrs"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Allowed source addresses</FormLabel>
                      <FormControl>
                        <Input {...field} placeholder="10.20.0.0/16, 192.168.1.0/24 (empty = any address that can reach the node)" className="font-mono" />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
              </CardContent>
            </Card>

            <Card className="gap-0 rounded-md py-0 shadow-premium">
              <SectionTitle icon={SlidersHorizontal} title="Lifetime" description="The instance stops by itself when its TTL runs out; you can extend it while it runs." />
              <CardContent className="grid gap-4 px-5 py-5 sm:grid-cols-3">
                <FormField
                  control={form.control}
                  name="ttl"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Stop after</FormLabel>
                      <Select value={String(field.value)} onValueChange={(x) => field.onChange(Number(x))}>
                        <SelectTrigger className="w-full">
                          <SelectValue>{(x: unknown) => TTL_OPTIONS.find((o) => String(o.value) === x)?.label}</SelectValue>
                        </SelectTrigger>
                        <SelectContent>
                          {TTL_OPTIONS.map((o) => (
                            <SelectItem key={o.value} value={String(o.value)}>
                              {o.label}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </FormItem>
                  )}
                />
                <FormField
                  control={form.control}
                  name="idle"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Idle stop</FormLabel>
                      <Select value={String(field.value)} onValueChange={(x) => field.onChange(Number(x))}>
                        <SelectTrigger className="w-full">
                          <SelectValue>{(x: unknown) => IDLE_OPTIONS.find((o) => String(o.value) === x)?.label}</SelectValue>
                        </SelectTrigger>
                        <SelectContent>
                          {IDLE_OPTIONS.map((o) => (
                            <SelectItem key={o.value} value={String(o.value)}>
                              {o.label}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </FormItem>
                  )}
                />
                <FormField
                  control={form.control}
                  name="waitMinutes"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>If nothing fits</FormLabel>
                      <Select value={String(field.value)} onValueChange={(x) => field.onChange(Number(x))}>
                        <SelectTrigger className="w-full">
                          <SelectValue>{(x: unknown) => (x === '0' ? 'Fail right away' : `Queue for ${formatSpan(Number(x) * 60)}`)}</SelectValue>
                        </SelectTrigger>
                        <SelectContent>
                          {[0, 15, 30, 60, 240].map((m) => (
                            <SelectItem key={m} value={String(m)}>
                              {m === 0 ? 'Fail right away' : `Queue for ${formatSpan(m * 60)}`}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </FormItem>
                  )}
                />
              </CardContent>
            </Card>

            <Collapsible className="rounded-md border border-border bg-card shadow-premium">
              <CollapsibleTrigger className="flex w-full cursor-pointer items-center justify-between px-5 py-4 text-left text-sm font-semibold">
                <span className="flex items-center gap-2">
                  <HardDrive className="size-4 text-muted-foreground" />
                  Resources, storage and environment
                </span>
                <span className="text-xs font-normal text-muted-foreground">optional</span>
              </CollapsibleTrigger>
              <CollapsibleContent className="space-y-5 border-t border-border px-5 py-5">
                <div className="grid gap-4 sm:grid-cols-4">
                  {(['cpuCores', 'memoryGiB', 'shmGiB', 'containerPort'] as const).map((name) => (
                    <FormField
                      key={name}
                      control={form.control}
                      name={name}
                      render={({ field }) => (
                        <FormItem>
                          <FormLabel>{{ cpuCores: 'CPU cores', memoryGiB: 'Memory (GiB)', shmGiB: '/dev/shm (GiB)', containerPort: 'sshd port inside' }[name]}</FormLabel>
                          <FormControl>
                            <Input {...field} inputMode="decimal" placeholder={name === 'containerPort' ? 'image default' : 'no limit'} className="font-mono" />
                          </FormControl>
                          <FormMessage />
                        </FormItem>
                      )}
                    />
                  ))}
                </div>
                <FormField
                  control={form.control}
                  name="keepWorkspace"
                  render={({ field }) => (
                    <FormItem className="flex items-center justify-between gap-4 rounded-md border border-border px-3 py-2.5">
                      <div>
                        <FormLabel>Keep /workspace between starts</FormLabel>
                        <FormDescription>A named volume on the node that survives stop and start (lost if the node is removed).</FormDescription>
                      </div>
                      <Switch checked={field.value} onCheckedChange={field.onChange} aria-label="Keep workspace volume" />
                    </FormItem>
                  )}
                />
                <div className="grid gap-5 sm:grid-cols-2">
                  <div className="space-y-2">
                    <p className="text-[13px] font-medium">Environment</p>
                    <KeyValueEditor rows={env} onChange={setEnv} keyPlaceholder="HF_HOME" valuePlaceholder="/data/.cache" addLabel="Add variable" />
                  </div>
                  <div className="space-y-2">
                    <p className="text-[13px] font-medium">Labels</p>
                    <KeyValueEditor rows={labels} onChange={setLabels} keyPlaceholder="owner" valuePlaceholder="alice" addLabel="Add label" mono={false} />
                  </div>
                </div>
              </CollapsibleContent>
            </Collapsible>
          </div>

          <div className="space-y-4 lg:sticky lg:top-6">
            <Card className="relative gap-0 overflow-hidden rounded-md py-0 shadow-premium">
              <div aria-hidden className="pointer-events-none absolute -right-16 -top-20 size-48 rounded-full bg-brand/10 blur-3xl" />
              <CardHeader className="relative border-b border-border px-5 py-4">
                <CardTitle className="text-sm font-semibold">Summary</CardTitle>
                <CardDescription>What the scheduler will look for.</CardDescription>
              </CardHeader>
              <CardContent className="relative space-y-3 px-5 py-4 text-[13px]">
                {[
                  ['GPU', pinnedSlice ? `reserved slice · ${formatBytes(pinnedSlice.vram_bytes)}` : v.gpuMode === 'none' ? 'none (CPU only)' : v.gpuMode === 'slice' ? `${v.sliceGiB} GiB slice · ${v.slicePct}% compute` : `${v.gpuCount} whole GPU${v.gpuCount === 1 ? '' : 's'}`],
                  ['Vendor', v.vendor === ANY ? 'any' : DEVICE_VENDOR_LABELS[v.vendor as DeviceVendor]],
                  ['Placement', node ? `pinned to ${node.name}` : 'scheduler picks a node'],
                  ['Keys', keyCount ? `${keyCount} public key${keyCount === 1 ? '' : 's'}` : 'none yet'],
                  ['Stops', stopsAt ? `after ${formatSpan(v.ttl)} (≈ ${stopsAt.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}${stopsAt.toDateString() !== new Date(now).toDateString() ? ` ${stopsAt.toLocaleDateString([], { weekday: 'short' })}` : ''})` : 'only when you stop it'],
                ].map(([k, val]) => (
                  <div key={k} className="flex items-baseline justify-between gap-3">
                    <span className="text-muted-foreground">{k}</span>
                    <span className="text-right font-medium">{val}</span>
                  </div>
                ))}
                <div className="rounded-md border border-inverse-border bg-inverse px-3 py-2 font-mono text-[11.5px] text-inverse-foreground">
                  <span className="text-inverse-muted-foreground">$ </span>
                  ssh -p <span className="text-inverse-muted-foreground">&lt;port&gt;</span> {v.sshUser || 'gpudeck'}@{node?.proxy_address ?? node?.ip_address ?? <span className="text-inverse-muted-foreground">&lt;node&gt;</span>}
                </div>
                {(sliceTooBig || wholeTooMany) && (
                  <p className="rounded-md bg-warning/10 p-2.5 text-xs text-warning-dark dark:text-warning-light">
                    Nothing fits right now. {v.waitMinutes ? `The request queues for ${formatSpan(v.waitMinutes * 60)}.` : 'Queue it (If nothing fits) or it fails immediately.'}
                  </p>
                )}
                {form.formState.errors.root?.message && <p className="rounded-md bg-destructive/10 p-3 text-sm text-destructive">{form.formState.errors.root.message}</p>}
                <Button type="submit" className="w-full" disabled={create.isPending}>
                  {create.isPending ? <Loader2 className="size-4 animate-spin" /> : <SquareTerminal className="size-4" />}
                  Request instance
                </Button>
              </CardContent>
            </Card>
          </div>
        </form>
      </Form>
    </PageContainer>
  );
}
