/**
 * SSH GPU instance mocks: `/gpu-instances` CRUD, connection details,
 * stop / start, TTL changes. The lifecycle (requested → placing → starting →
 * running, stopping → stopped) advances with wall-clock time on every read,
 * so the screens animate the way they do against the backend. Slices taken
 * by an instance are written into the shared fleet store, so the GPU page
 * shows them next to the deployments' slices.
 */

import { GiB, IDS } from '@/lib/mocks/fixtures/fleet';
import { hash32, mulberry32, uuid } from '@/lib/mocks/fixtures/random';
import { fleetStore, newId } from '@/lib/mocks/handlers/fleet';
import { recordAudit } from '@/lib/mocks/handlers/security';
import { bodyOf, created, fail, noContent, notFound, ok, paginate, registerMockHandlers, sortBy } from '@/lib/mocks/registry';
import type { MockRequest, Schemas } from '@/lib/mocks/types';
import { parseSshKey, sshFingerprint } from '@/lib/ssh-keys';
import { cidrError } from '@/components/security/cidr';
import type { GpuInstance, GpuInstanceConnection, GpuInstanceCreate, GpuInstanceKey, GpuInstanceUpdate } from '@/types/gpu-instances';

const iso = (ms: number) => new Date(ms).toISOString();
const NOW = Date.now();
const MIN = 60_000;
const HOUR = 60 * MIN;
const DEFAULT_IMAGE = 'ghcr.io/sur950/gpu-deck-ssh:1';
const DEFAULT_TTL = 8 * 3600;
const MAX_TTL = 7 * 86_400;

/** Phase durations of the simulated lifecycle (ms). */
const PHASE = { requested: 800, placing: 1_600, starting: 4_500, stopping: 2_500 };

interface Record_ {
  instance: GpuInstance;
  /** When the current state was entered (drives the simulation). */
  enteredAt: number;
  /** Slice this instance holds on the shared fleet store, if any. */
  sliceId: string | null;
  /** An existing standalone reservation the request asked for (`slice_id`). */
  requestedSliceId?: string | null;
}

// Fingerprint of a throwaway public key (its private half was deleted after generating it).
const DEMO_KEY_INFO: GpuInstanceKey = { type: 'ssh-ed25519', fingerprint: 'SHA256:5JvVw674q17ehFXdsucAhP8xaFbe1No7b/R2cqdv7z4', comment: 'alice@laptop' };

function hostKey(seed: string): { type: string; fingerprint: string; public_key: string } {
  const r = mulberry32(hash32(`hostkey:${seed}`));
  const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/';
  const chars = (n: number) => Array.from({ length: n }, () => alphabet[Math.floor(r() * 64)]).join('');
  return { type: 'ssh-ed25519', fingerprint: `SHA256:${chars(43)}`, public_key: `ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI${chars(43)}` };
}

function nodeOf(id: string | null | undefined): Schemas['Node'] | undefined {
  return id ? fleetStore.nodes.find((n) => n.id === id) : undefined;
}

function connectionOf(i: GpuInstance, hostPort: number | null): GpuInstanceConnection {
  const node = nodeOf(i.node_id);
  const host = node?.proxy_address ?? node?.ip_address ?? null;
  const ready = i.state === 'running' && !!host && !!hostPort;
  const keys = i.state === 'running' || i.state === 'stopping' ? [hostKey(i.id)] : [];
  return {
    host: hostPort ? host : null,
    port: hostPort,
    user: i.ssh_user,
    host_key_fingerprint: keys[0]?.fingerprint ?? null,
    host_keys: keys,
    ssh_command: host && hostPort ? `ssh -p ${hostPort} ${i.ssh_user}@${host}` : null,
    known_hosts: host && hostPort ? keys.map((k) => `[${host}]:${hostPort} ${k.public_key}`) : [],
    ready,
  };
}

function base(partial: Partial<GpuInstance> & Pick<GpuInstance, 'id' | 'name' | 'state' | 'created_at'>): GpuInstance {
  return {
    description: null,
    state_message: null,
    health: 'unknown',
    image: DEFAULT_IMAGE,
    cluster_id: IDS.cluster,
    node_id: null,
    node_name: null,
    gpu_count: 1,
    slice_vram_bytes: null,
    slice_compute_pct: null,
    device_vendor: null,
    node_selector: {},
    device_ids: [],
    limits: {},
    authorized_keys: [DEMO_KEY_INFO],
    ssh_user: 'gpudeck',
    container_port: 2222,
    allowed_source_cidrs: [],
    env: {},
    volumes: [],
    labels: {},
    ttl_seconds: DEFAULT_TTL,
    idle_timeout_seconds: null,
    expires_at: null,
    connection: { host: null, port: null, user: 'gpudeck', host_key_fingerprint: null, host_keys: [], ssh_command: null, known_hosts: [], ready: false },
    gpu_attached: false,
    warnings: [],
    container_id: null,
    active_connections: 0,
    connections_total: 0,
    last_connection_at: null,
    stop_reason: null,
    start_command_id: null,
    stop_command_id: null,
    gpu_seconds_total: 0,
    state_changed_at: partial.created_at,
    started_at: null,
    running_at: null,
    stopped_at: null,
    updated_at: partial.created_at,
    ...partial,
  };
}

const hostPorts = new Map<string, number>();

function takeSlice(i: GpuInstance, deviceId: string, vram: number, compute: number): string {
  const id = newId('slice:ssh');
  const slice: Schemas['DeviceSlice'] = {
    id,
    device_id: deviceId,
    instance_id: null,
    deployment_id: null,
    gpu_instance_id: i.id,
    name: `ssh:${i.name}`,
    vram_bytes: vram,
    compute_pct: compute,
    status: 'active',
    created_at: iso(Date.now()),
    released_at: null,
  };
  fleetStore.slices.push(slice);
  return id;
}

function releaseSlice(rec: Record_): void {
  const slice = fleetStore.slices.find((s) => s.id === rec.sliceId);
  if (slice && rec.requestedSliceId === slice.id) {
    // A reservation the instance borrowed goes back to being a reservation.
    slice.gpu_instance_id = null;
    slice.status = 'reserved';
  } else if (slice && slice.status !== 'released') {
    slice.status = 'released';
    slice.released_at = iso(Date.now());
  }
  rec.sliceId = null;
}

function seed(): Record_[] {
  const gpuBox = nodeOf(IDS.nodes.gpu);
  const out: Record_[] = [];

  const dev = base({
    id: uuid('gpu-instance:alice-dev'),
    name: 'alice-dev',
    description: 'Interactive CUDA box for kernel experiments.',
    state: 'running',
    health: 'healthy',
    node_id: IDS.nodes.gpu,
    node_name: gpuBox?.name ?? 'gpu-box-01',
    device_ids: [IDS.devices.l40s1],
    slice_vram_bytes: 16 * GiB,
    slice_compute_pct: 50,
    device_vendor: 'nvidia',
    limits: { cpu_cores: 8, memory_bytes: 32 * GiB, shm_bytes: 8 * GiB },
    env: { HF_HOME: '/data/.cache/huggingface' },
    volumes: [{ type: 'volume', source: 'alice-workspace', target: '/workspace', keep: true, read_only: false }],
    labels: { owner: 'alice', team: 'platform' },
    ttl_seconds: 8 * 3600,
    expires_at: iso(NOW + 5 * HOUR + 42 * MIN),
    gpu_attached: true,
    container_id: 'c0ffee2a91d4',
    active_connections: 1,
    connections_total: 7,
    last_connection_at: iso(NOW - 3 * MIN),
    gpu_seconds_total: 2 * 3600 + 18 * 60,
    created_at: iso(NOW - 2 * HOUR - 18 * MIN - 40_000),
    state_changed_at: iso(NOW - 2 * HOUR - 18 * MIN),
    started_at: iso(NOW - 2 * HOUR - 18 * MIN - 30_000),
    running_at: iso(NOW - 2 * HOUR - 18 * MIN),
  });
  hostPorts.set(dev.id, 40_122);
  out.push({ instance: dev, enteredAt: NOW - 2 * HOUR, sliceId: takeSlice(dev, IDS.devices.l40s1, 16 * GiB, 50) });

  const lora = base({
    id: uuid('gpu-instance:finetune-lora'),
    name: 'finetune-lora',
    description: 'LoRA fine-tune of qwen3-8b on the support transcripts.',
    state: 'running',
    health: 'healthy',
    image: 'nvcr.io/nvidia/pytorch:25.10-py3-sshd',
    container_port: 22,
    node_id: IDS.nodes.gpu,
    node_name: gpuBox?.name ?? 'gpu-box-01',
    device_ids: [IDS.devices.l40s0],
    slice_vram_bytes: 12 * GiB,
    slice_compute_pct: 40,
    device_vendor: 'nvidia',
    authorized_keys: [DEMO_KEY_INFO, { type: 'ssh-rsa', fingerprint: 'SHA256:Q3dkDxfzH2b5q1UXjZ2ZUJo/0iK5uzmy1tSmFSy/ZJ8', comment: 'ci' }],
    allowed_source_cidrs: ['10.20.0.0/16', '192.168.1.0/24'],
    limits: { cpu_cores: 16, memory_bytes: 64 * GiB },
    labels: { owner: 'ml', job: 'lora-support' },
    ttl_seconds: 24 * 3600,
    expires_at: iso(NOW + 38 * MIN),
    idle_timeout_seconds: 3600,
    gpu_attached: true,
    container_id: '7d1be03fa2c9',
    connections_total: 3,
    last_connection_at: iso(NOW - 4 * HOUR),
    gpu_seconds_total: 23 * 3600 + 22 * 60,
    created_at: iso(NOW - 23 * HOUR - 22 * MIN - 60_000),
    state_changed_at: iso(NOW - 23 * HOUR - 22 * MIN),
    started_at: iso(NOW - 23 * HOUR - 22 * MIN - 50_000),
    running_at: iso(NOW - 23 * HOUR - 22 * MIN),
  });
  hostPorts.set(lora.id, 40_317);
  out.push({ instance: lora, enteredAt: NOW - 23 * HOUR, sliceId: takeSlice(lora, IDS.devices.l40s0, 12 * GiB, 40) });

  const big = base({
    id: uuid('gpu-instance:a100-eval'),
    name: 'a100-eval',
    description: 'Needs 80 GiB for the 70B eval run.',
    state: 'error',
    health: 'unknown',
    gpu_count: 1,
    slice_vram_bytes: 80 * GiB,
    slice_compute_pct: 100,
    device_vendor: 'nvidia',
    ttl_seconds: 4 * 3600,
    state_message: 'No node has a free NVIDIA GPU with 80 GiB (largest free: 16.5 GiB on gpu-box-01 / L40S #1). The request waited 30 min for capacity.',
    created_at: iso(NOW - 3 * HOUR),
    state_changed_at: iso(NOW - 2 * HOUR - 30 * MIN),
  });
  out.push({ instance: big, enteredAt: NOW - 2.5 * HOUR, sliceId: null });

  const old = base({
    id: uuid('gpu-instance:eval-sandbox'),
    name: 'eval-sandbox',
    state: 'stopped',
    health: 'unknown',
    node_id: IDS.nodes.gpu,
    node_name: gpuBox?.name ?? 'gpu-box-01',
    device_ids: [IDS.devices.l40s1],
    gpu_count: 1,
    device_vendor: 'nvidia',
    ttl_seconds: 4 * 3600,
    expires_at: iso(NOW - 20 * HOUR),
    stop_reason: 'ttl_expired',
    gpu_attached: true,
    connections_total: 12,
    last_connection_at: iso(NOW - 21 * HOUR),
    gpu_seconds_total: 4 * 3600,
    created_at: iso(NOW - 24 * HOUR - 2 * MIN),
    started_at: iso(NOW - 24 * HOUR - MIN),
    running_at: iso(NOW - 24 * HOUR),
    stopped_at: iso(NOW - 20 * HOUR),
    state_changed_at: iso(NOW - 20 * HOUR),
  });
  out.push({ instance: old, enteredAt: NOW - 20 * HOUR, sliceId: null });
  return out;
}

const records: Record_[] = seed();

// ─── Simulated scheduler / lifecycle ────────────────────────────────────────

function enter(rec: Record_, state: GpuInstance['state'], at: number, patch: Partial<GpuInstance> = {}): void {
  Object.assign(rec.instance, patch, { state, state_changed_at: iso(at), updated_at: iso(at) });
  rec.enteredAt = at;
}

/** Why nothing fits right now (null when it does) — same rule as `place()`. */
function capacityProblem(i: Pick<GpuInstance, 'gpu_count' | 'slice_vram_bytes' | 'device_vendor' | 'node_selector'>): string | null {
  if (i.gpu_count === 0) return null;
  const vendor = i.device_vendor ?? 'nvidia';
  const pinnedNodes = (i.node_selector.node_ids as string[] | undefined) ?? [];
  const candidates = fleetStore.devices.filter((d) => d.vendor === vendor && (pinnedNodes.length === 0 || pinnedNodes.includes(d.node_id)));
  const need = i.slice_vram_bytes ?? 0;
  const free = (d: Schemas['Device']) =>
    d.vram_total_bytes * (d.is_unified_memory ? 0.75 : 0.9) - fleetStore.slices.filter((x) => x.device_id === d.id && x.status !== 'released').reduce((sum, x) => sum + x.vram_bytes, 0);
  const fits = candidates.filter((d) => (need ? free(d) >= need : fleetStore.slices.every((x) => x.device_id !== d.id || x.status === 'released')));
  if (fits.length >= Math.max(1, i.gpu_count)) return null;
  const largest = candidates.reduce((best, d) => Math.max(best, free(d)), 0);
  return need
    ? `No ${vendor.toUpperCase()} GPU has ${(need / GiB).toFixed(1)} GiB free (largest free: ${(largest / GiB).toFixed(1)} GiB). Queue the request (wait_for_capacity_seconds) or ask for less.`
    : `Not enough idle ${vendor.toUpperCase()} GPUs: ${i.gpu_count} requested, ${fits.length} idle.`;
}

function place(rec: Record_, at: number): void {
  const i = rec.instance;
  if (rec.requestedSliceId) {
    const slice = fleetStore.slices.find((x) => x.id === rec.requestedSliceId && x.status !== 'released' && !x.deployment_id && !x.gpu_instance_id);
    const device = slice && fleetStore.devices.find((d) => d.id === slice.device_id);
    if (!slice || !device) {
      enter(rec, 'error', at, { state_message: 'The requested slice is gone or already taken.' });
      return;
    }
    slice.gpu_instance_id = i.id;
    slice.status = 'active';
    rec.sliceId = slice.id;
    const node = nodeOf(device.node_id);
    enter(rec, 'starting', at, {
      node_id: node?.id ?? null,
      node_name: node?.name ?? null,
      device_ids: [device.id],
      slice_vram_bytes: slice.vram_bytes,
      slice_compute_pct: slice.compute_pct,
      started_at: iso(at),
      gpu_attached: device.vendor !== 'apple',
    });
    hostPorts.set(i.id, 40_000 + (hash32(`${i.id}:${at}`) % 1000));
    return;
  }
  const vendor = i.device_vendor ?? 'nvidia';
  const pinnedNodes = (i.node_selector.node_ids as string[] | undefined) ?? [];
  const candidates = fleetStore.devices.filter(
    (d) => (vendor ? d.vendor === vendor : true) && (pinnedNodes.length === 0 || pinnedNodes.includes(d.node_id)),
  );
  if (i.gpu_count === 0) {
    const node = pinnedNodes.length ? nodeOf(pinnedNodes[0]) : nodeOf(IDS.nodes.gpu);
    enter(rec, 'starting', at, { node_id: node?.id ?? null, node_name: node?.name ?? null, device_ids: [], gpu_attached: false });
    return;
  }
  const need = i.slice_vram_bytes ?? 0;
  const free = (d: Schemas['Device']) =>
    d.vram_total_bytes * (d.is_unified_memory ? 0.75 : 0.9) - fleetStore.slices.filter((s) => s.device_id === d.id && s.status !== 'released').reduce((sum, s) => sum + s.vram_bytes, 0);
  const fits = candidates.filter((d) => (need ? free(d) >= need : fleetStore.slices.every((s) => s.device_id !== d.id || s.status === 'released')));
  if (fits.length < Math.max(1, i.gpu_count)) {
    const largest = candidates.reduce((best, d) => Math.max(best, free(d)), 0);
    enter(rec, 'error', at, {
      state_message: need
        ? `No node has a free ${vendor.toUpperCase()} GPU with ${(need / GiB).toFixed(1)} GiB (largest free: ${(largest / GiB).toFixed(1)} GiB).`
        : `Not enough idle ${vendor.toUpperCase()} GPUs: ${i.gpu_count} requested, ${fits.length} free.`,
    });
    return;
  }
  const chosen = fits.slice(0, Math.max(1, i.gpu_count));
  const node = nodeOf(chosen[0].node_id);
  for (const d of chosen) {
    const id = takeSlice(i, d.id, need || Math.round(d.vram_total_bytes * 0.9), i.slice_compute_pct ?? 100);
    rec.sliceId = rec.sliceId ?? id;
  }
  const apple = chosen[0].vendor === 'apple';
  enter(rec, 'starting', at, {
    node_id: node?.id ?? null,
    node_name: node?.name ?? null,
    device_ids: chosen.map((d) => d.id),
    started_at: iso(at),
    start_command_id: newId('command'),
    gpu_attached: !apple,
    warnings: apple ? ['Containers on macOS cannot see the Apple GPU (no Metal passthrough); the instance runs CPU-only. Use a model deployment for MLX / llama.cpp on Metal.'] : [],
  });
  hostPorts.set(i.id, 40_000 + (hash32(`${i.id}:${at}`) % 1000));
}

function advance(rec: Record_, now = Date.now()): GpuInstance {
  const i = rec.instance;
  for (let guard = 0; guard < 6; guard += 1) {
    const elapsed = now - rec.enteredAt;
    if (i.state === 'requested' && elapsed >= PHASE.requested) {
      enter(rec, 'placing', rec.enteredAt + PHASE.requested);
    } else if (i.state === 'placing' && elapsed >= PHASE.placing) {
      place(rec, rec.enteredAt + PHASE.placing);
    } else if (i.state === 'starting' && elapsed >= PHASE.starting) {
      const at = rec.enteredAt + PHASE.starting;
      enter(rec, 'running', at, {
        running_at: iso(at),
        health: 'healthy',
        container_id: i.container_id ?? (hash32(i.id).toString(16) + '0000000000').slice(0, 12),
        expires_at: i.ttl_seconds ? iso(at + i.ttl_seconds * 1000) : null,
      });
    } else if (i.state === 'running' && i.expires_at && Date.parse(i.expires_at) <= now) {
      enter(rec, 'stopping', Date.parse(i.expires_at), { stop_reason: 'ttl_expired', stop_command_id: newId('command') });
    } else if (i.state === 'stopping' && elapsed >= PHASE.stopping) {
      const at = rec.enteredAt + PHASE.stopping;
      enter(rec, 'stopped', at, { stopped_at: iso(at), active_connections: 0, health: 'unknown' });
      releaseSlice(rec);
    } else {
      break;
    }
  }
  if (i.state === 'running' && i.running_at) {
    // Metered GPU time keeps growing while it runs.
    const runningFor = (now - Date.parse(i.running_at)) / 1000;
    i.gpu_seconds_total = Math.max(i.gpu_seconds_total, Math.round(runningFor * Math.max(1, i.gpu_count)));
  }
  i.connection = connectionOf(i, i.state === 'running' || i.state === 'stopping' || i.state === 'starting' ? (hostPorts.get(i.id) ?? null) : null);
  return i;
}

function find(req: MockRequest): Record_ | undefined {
  return records.find((r) => r.instance.id === req.params.instance_id);
}

async function keysFrom(lines: string[] | undefined): Promise<{ keys: GpuInstanceKey[]; error: string | null }> {
  const keys: GpuInstanceKey[] = [];
  for (const [n, line] of (lines ?? []).entries()) {
    const parsed = parseSshKey(line);
    if (!parsed.ok) return { keys, error: `authorized_keys[${n}]: ${parsed.error}` };
    const fingerprint = await sshFingerprint(parsed.key.blob);
    if (!keys.some((k) => k.fingerprint === fingerprint)) keys.push({ type: parsed.key.type, fingerprint, comment: parsed.key.comment });
  }
  return { keys, error: keys.length ? null : 'At least one SSH public key is required' };
}

registerMockHandlers('gpu-instances', [
  {
    method: 'GET',
    path: '/gpu-instances',
    handle: (req) => {
      const now = Date.now();
      const state = req.query.getAll('state');
      const search = (req.query.get('search') ?? '').toLowerCase();
      const includeStopped = req.query.get('include_stopped') !== 'false';
      const nodeId = req.query.get('node_id');
      const list = records
        .map((r) => advance(r, now))
        .filter(
          (i) =>
            (!state.length || state.includes(i.state)) &&
            (includeStopped || i.state !== 'stopped') &&
            (!nodeId || i.node_id === nodeId) &&
            (!search || i.name.includes(search) || (i.description ?? '').toLowerCase().includes(search) || i.image.toLowerCase().includes(search)),
        );
      return paginate(sortBy(list as unknown as Record<string, unknown>[], req.query.get('sort'), '-created_at'), req.query);
    },
  },
  {
    method: 'POST',
    path: '/gpu-instances',
    handle: async (req) => {
      const body = bodyOf<GpuInstanceCreate>(req);
      const name = (body.name ?? '').trim();
      if (!/^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$/.test(name)) return fail(422, 'VALIDATION_ERROR', 'name must be lowercase letters, digits and "-" (DNS label)');
      if (records.some((r) => r.instance.name === name && r.instance.state !== 'stopped')) return fail(409, 'CONFLICT', `An instance called "${name}" already exists`);
      const { keys, error } = await keysFrom(body.authorized_keys);
      if (error) return fail(422, 'VALIDATION_ERROR', error);
      const badCidr = (body.allowed_source_cidrs ?? []).find((c) => cidrError(c));
      if (badCidr) return fail(422, 'VALIDATION_ERROR', `invalid CIDR "${badCidr}"`);
      if ((body.ttl_seconds ?? 0) > MAX_TTL) return fail(422, 'VALIDATION_ERROR', 'ttl_seconds is above GPU_INSTANCE_MAX_TTL_SECONDS (7 days)');
      if (body.slice_vram_bytes && (body.gpu_count ?? 1) !== 1) return fail(422, 'VALIDATION_ERROR', 'a GPU slice is single-device: gpu_count must be 1');
      const now = Date.now();
      const instance = base({
        id: newId('gpu-instance'),
        name,
        description: body.description ?? null,
        state: 'requested',
        image: body.image || DEFAULT_IMAGE,
        cluster_id: body.cluster_id ?? IDS.cluster,
        gpu_count: body.gpu_count ?? 1,
        slice_vram_bytes: body.slice_vram_bytes ?? null,
        slice_compute_pct: body.slice_vram_bytes ? (body.slice_compute_pct ?? 100) : null,
        device_vendor: body.device_vendor ?? null,
        node_selector: body.node_selector ?? {},
        authorized_keys: keys,
        ssh_user: body.ssh_user || 'gpudeck',
        container_port: body.container_port ?? (body.image ? 22 : 2222),
        allowed_source_cidrs: body.allowed_source_cidrs ?? [],
        limits: body.limits ?? {},
        env: body.env ?? {},
        volumes: body.volumes ?? [],
        labels: body.labels ?? {},
        ttl_seconds: body.ttl_seconds === undefined ? DEFAULT_TTL : body.ttl_seconds || null,
        idle_timeout_seconds: body.idle_timeout_seconds ?? null,
        created_at: iso(now),
      });
      const problem = body.slice_id ? null : capacityProblem(instance);
      if (problem && !body.wait_for_capacity_seconds) return fail(409, 'NO_CAPACITY', problem);
      const rec: Record_ = { instance, enteredAt: now, sliceId: null, requestedSliceId: body.slice_id ?? null };
      records.unshift(rec);
      recordAudit({ eventType: 'gpu_instance.created', resourceType: 'gpu_instance', resourceId: instance.id, resourceName: instance.name, at: now, after: { image: instance.image, gpu_count: instance.gpu_count, slice_vram_bytes: instance.slice_vram_bytes } });
      return created(advance(rec, now));
    },
  },
  {
    method: 'GET',
    path: '/gpu-instances/:instance_id',
    handle: (req) => {
      const rec = find(req);
      return rec ? ok(advance(rec)) : notFound('GPU instance');
    },
  },
  {
    method: 'GET',
    path: '/gpu-instances/:instance_id/connection',
    handle: (req) => {
      const rec = find(req);
      return rec ? ok(advance(rec).connection) : notFound('GPU instance');
    },
  },
  {
    method: 'PATCH',
    path: '/gpu-instances/:instance_id',
    handle: async (req) => {
      const rec = find(req);
      if (!rec) return notFound('GPU instance');
      const i = advance(rec);
      const body = bodyOf<GpuInstanceUpdate>(req);
      const now = Date.now();
      if (body.ttl_seconds !== undefined && body.ttl_seconds !== null) {
        if (body.ttl_seconds > MAX_TTL) return fail(422, 'VALIDATION_ERROR', 'ttl_seconds is above the 7 day maximum');
        i.ttl_seconds = body.ttl_seconds || null;
        i.expires_at = body.ttl_seconds && i.state !== 'stopped' ? iso(now + body.ttl_seconds * 1000) : null;
      }
      if (body.idle_timeout_seconds !== undefined) i.idle_timeout_seconds = body.idle_timeout_seconds || null;
      if (body.description !== undefined) i.description = body.description;
      if (body.labels) i.labels = body.labels;
      if (body.authorized_keys) {
        if (i.state !== 'stopped') return fail(409, 'CONFLICT', 'Keys can only change while the instance is stopped (they are used on the next start)');
        const { keys, error } = await keysFrom(body.authorized_keys);
        if (error) return fail(422, 'VALIDATION_ERROR', error);
        i.authorized_keys = keys;
      }
      i.updated_at = iso(now);
      return ok(advance(rec));
    },
  },
  {
    method: 'POST',
    path: '/gpu-instances/:instance_id/stop',
    handle: (req) => {
      const rec = find(req);
      if (!rec) return notFound('GPU instance');
      const i = advance(rec);
      const now = Date.now();
      if (i.state === 'stopped') return fail(409, 'CONFLICT', 'The instance is already stopped');
      if (i.state === 'error' || i.state === 'requested' || i.state === 'placing') {
        enter(rec, 'stopped', now, { stop_reason: 'user', stopped_at: iso(now) });
        releaseSlice(rec);
      } else if (i.state !== 'stopping') {
        enter(rec, 'stopping', now, { stop_reason: 'user', stop_command_id: newId('command') });
      }
      recordAudit({ eventType: 'gpu_instance.stopped', resourceType: 'gpu_instance', resourceId: i.id, resourceName: i.name, at: now });
      return { status: 202, data: advance(rec, now) };
    },
  },
  {
    method: 'POST',
    path: '/gpu-instances/:instance_id/start',
    handle: (req) => {
      const rec = find(req);
      if (!rec) return notFound('GPU instance');
      const i = advance(rec);
      if (i.state !== 'stopped' && i.state !== 'error') return fail(409, 'CONFLICT', `Only a stopped instance can be started (it is ${i.state})`);
      const now = Date.now();
      enter(rec, 'requested', now, { stop_reason: null, state_message: null, stopped_at: null, running_at: null, expires_at: null, device_ids: [], node_id: null, node_name: null, active_connections: 0 });
      return { status: 202, data: advance(rec, now) };
    },
  },
  {
    method: 'DELETE',
    path: '/gpu-instances/:instance_id',
    handle: (req) => {
      const idx = records.findIndex((r) => r.instance.id === req.params.instance_id);
      if (idx < 0) return notFound('GPU instance');
      const rec = records[idx];
      const i = advance(rec);
      if (i.state !== 'stopped' && i.state !== 'error' && req.query.get('force') !== 'true') {
        return fail(409, 'CONFLICT', 'Stop the instance first, or delete with force=true');
      }
      releaseSlice(rec);
      records.splice(idx, 1);
      return noContent();
    },
  },
]);
