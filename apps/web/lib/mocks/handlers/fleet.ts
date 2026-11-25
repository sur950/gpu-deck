/**
 * Fleet mocks: clusters, nodes, join tokens, certificates, agent
 * commands, devices and slices — full CRUD over an in-memory store seeded
 * from the shared fixture fleet (`lib/mocks/fixtures/fleet.ts`), so ids line
 * up with the dashboards, observability and topology mocks.
 *
 * The store also holds the instances and deployments, because node drain,
 * slices and deployments all move the same records; the models feature
 * (`handlers/models.ts`) imports `fleetStore` and the view helpers from here.
 * These handlers supersede the read-only stand-ins registered by the
 * topology / observability files for the same routes.
 *
 * Asynchronous agent work (drain, joins, loads) is simulated with timers so
 * the screens show the in-between states the real control plane reports.
 */

import { CLUSTER, DEPLOYMENTS, DEVICE_SLICES, DEVICES, GiB, IDS, INSTANCES, MiB, NODES } from '@/lib/mocks/fixtures/fleet';
import { hash32, hexId, uuid } from '@/lib/mocks/fixtures/random';
import { engineMetricValue, engineSample, gpuMemoryUsed, gpuPower, gpuTemperature, gpuUtilization } from '@/lib/mocks/fixtures/timeseries';
import { accepted, asFallback, bodyOf, created, fail, noContent, notFound, ok, paginate, registerMockHandlers, sortBy } from '@/lib/mocks/registry';
import type { MockHandler, MockRequest, Schemas } from '@/lib/mocks/types';

type Cluster = Schemas['Cluster'];
type Node = Schemas['Node'];
type Device = Schemas['Device'];
type DeviceSlice = Schemas['DeviceSlice'];
type Instance = Schemas['Instance'];
type Deployment = Schemas['Deployment'];
type JoinToken = Schemas['JoinToken'];
type NodeCertificate = Schemas['NodeCertificate'];
type AgentCommand = Schemas['AgentCommand'];
type Credential = Schemas['Credential'];
type CacheItem = Schemas['CacheItem'];

const iso = (ms: number) => new Date(ms).toISOString();
const T0 = Date.now();
const hoursAgo = (h: number) => iso(T0 - h * 3_600_000);
const daysAgo = (d: number) => hoursAgo(d * 24);
let seq = 0;
/** Fresh ids for records created in this browser session. */
export function newId(kind: string): string {
  seq += 1;
  return uuid(`${kind}:${Date.now()}:${seq}:${Math.random()}`);
}

// ─── Store ──────────────────────────────────────────────────────────────────

const CLOUD_CLUSTER_ID = uuid('cluster:aws-mumbai');

const CREDENTIALS: Credential[] = [
  { id: uuid('cred:aws-gpu-prod'), name: 'aws-gpu-prod', type: 'cloud_account', provider: 'aws', status: 'active', masked_value: 'AKIA••••••••7Q2D', description: 'IAM user for GPU instances (ap-south-1)', usage_count: 41, created_at: daysAgo(12), updated_at: daysAgo(3) },
  { id: uuid('cred:azure-sub'), name: 'azure-ml-subscription', type: 'cloud_account', provider: 'azure', status: 'active', masked_value: '7f3c••••-••••-••••-••••0b91', usage_count: 0, created_at: daysAgo(9), updated_at: daysAgo(9) },
  { id: uuid('cred:gcp-sa'), name: 'gcp-gpu-sa', type: 'service_account', provider: 'gcp', status: 'active', masked_value: 'gpu-deck@…iam.gserviceaccount.com', usage_count: 3, created_at: daysAgo(8), updated_at: daysAgo(8) },
  { id: uuid('cred:do-token'), name: 'digitalocean-token', type: 'api_key', provider: 'digitalocean', status: 'active', masked_value: 'dop_v1_••••••••a91e', usage_count: 0, created_at: daysAgo(6), updated_at: daysAgo(6) },
  { id: uuid('cred:kubeconfig-eks'), name: 'eks-gpu-kubeconfig', type: 'service_account', provider: 'kubernetes', status: 'active', masked_value: 'apiVersion: v1 •••', usage_count: 7, created_at: daysAgo(5), updated_at: daysAgo(5) },
  { id: uuid('cred:ssh-lab'), name: 'lab-ssh-deploy', type: 'ssh_key', provider: null, status: 'active', masked_value: 'ssh-ed25519 AAAA••••qk9', usage_count: 12, created_at: daysAgo(20), updated_at: daysAgo(20) },
  { id: uuid('cred:hf-token'), name: 'huggingface-token', type: 'token', provider: 'huggingface', status: 'active', masked_value: 'hf_••••••••Wq3', usage_count: 88, created_at: daysAgo(21), updated_at: hoursAgo(5) },
];

const extraCluster: Cluster = {
  id: CLOUD_CLUSTER_ID,
  name: 'aws-mumbai',
  type: 'cloud_aws',
  description: 'Burst capacity for client evals — scaled to zero between runs',
  region: 'ap-south-1',
  status: 'disabled',
  credential_id: uuid('cred:aws-gpu-prod'),
  price_plan_id: null,
  config: { vpc: 'vpc-0a1b2c3d4e5f', subnet: 'subnet-0f9e8d7c6b5a', instance_types: ['g6e.xlarge', 'g6e.2xlarge'], ssh_key_credential_id: uuid('cred:ssh-lab') },
  egress_allowlist: ['huggingface.co', 'cdn-lfs.huggingface.co'],
  labels: { env: 'burst', cost_center: 'clients' },
  counts: {},
  created_at: daysAgo(10),
  updated_at: daysAgo(1),
};

function seedTokens(): JoinToken[] {
  return [
    { id: uuid('join:mac'), cluster_id: IDS.cluster, token_prefix: 'gdj_7c41', node_name_hint: 'mac-m5-studio', labels: { role: 'dev' }, expires_at: daysAgo(21 - 0.01), max_uses: 1, use_count: 1, last_used_at: daysAgo(21), used_at: daysAgo(21), used_by_node_id: IDS.nodes.mac, revoked_at: null, created_at: daysAgo(21.01) },
    { id: uuid('join:gpu'), cluster_id: IDS.cluster, token_prefix: 'gdj_e09a', node_name_hint: 'gpu-box-01', labels: { role: 'serving' }, expires_at: daysAgo(14 - 0.01), max_uses: 1, use_count: 1, last_used_at: daysAgo(14), used_at: daysAgo(14), used_by_node_id: IDS.nodes.gpu, revoked_at: null, created_at: daysAgo(14.01) },
    { id: uuid('join:revoked'), cluster_id: IDS.cluster, token_prefix: 'gdj_51bd', node_name_hint: 'gpu-box-02', labels: {}, expires_at: daysAgo(2), max_uses: 1, use_count: 0, used_at: null, used_by_node_id: null, revoked_at: daysAgo(2.005), created_at: daysAgo(2.01) },
    { id: uuid('join:active'), cluster_id: IDS.cluster, token_prefix: 'gdj_a3f0', node_name_hint: 'gpu-box-02', labels: { role: 'serving', rack: 'r1' }, expires_at: iso(T0 + 11 * 60_000), max_uses: 1, use_count: 0, used_at: null, used_by_node_id: null, revoked_at: null, created_at: iso(T0 - 4 * 60_000) },
  ];
}

function seedCertificates(): NodeCertificate[] {
  const certs: NodeCertificate[] = [];
  for (const node of NODES) {
    const fp = hexId(`fp:${node.id}`, 32) + hexId(`fp2:${node.id}`, 32);
    certs.push({
      id: uuid(`cert:${node.id}:active`),
      kind: 'node_agent',
      node_id: node.id,
      serial: hexId(`serial:${node.id}`, 16).toUpperCase(),
      fingerprint_sha256: fp,
      subject: `CN=${node.id},O=gpudeck-nodes`,
      not_before: node.created_at,
      not_after: iso(new Date(node.created_at).getTime() + 365 * 86_400_000),
      issued_at: node.created_at,
      revoked_at: null,
      revocation_reason: null,
      is_active: true,
    });
  }
  // A rotated certificate on the Linux box (old one revoked 10 min after the new one connected).
  certs.push({
    id: uuid('cert:gpu:old'),
    kind: 'node_agent',
    node_id: IDS.nodes.gpu,
    serial: hexId('serial:gpu:old', 16).toUpperCase(),
    fingerprint_sha256: hexId('fp:gpu:old', 32) + hexId('fp2:gpu:old', 32),
    subject: `CN=${IDS.nodes.gpu},O=gpudeck-nodes`,
    not_before: daysAgo(14),
    not_after: iso(T0 + 351 * 86_400_000),
    issued_at: daysAgo(14),
    revoked_at: daysAgo(13.99),
    revocation_reason: 'superseded (rotation)',
    is_active: false,
  });
  return certs;
}

const COMMAND_SEEDS: [string, AgentCommand['state'], number, string | null][] = [
  ['load_model', 'succeeded', 0.42, IDS.instances.qwenL40s],
  ['unload_model', 'succeeded', 0.45, null],
  ['drain_instance', 'succeeded', 0.47, null],
  ['prefetch_model', 'succeeded', 3.2, null],
  ['update_config', 'succeeded', 6.5, null],
  ['run_benchmark', 'succeeded', 19, IDS.instances.qwenL40s],
  ['evict_cache', 'failed', 27, null],
  ['load_model', 'succeeded', 26, IDS.instances.qwenMac],
  ['start_engine', 'succeeded', 26.01, null],
  ['rotate_cert', 'timed_out', 49, null],
];

function seedCommands(): AgentCommand[] {
  const out: AgentCommand[] = [];
  for (const node of NODES) {
    COMMAND_SEEDS.forEach(([type, state, h, instanceId], i) => {
      const at = T0 - (h + (node.id === IDS.nodes.mac ? 0.3 : 0)) * 3_600_000;
      out.push({
        id: uuid(`cmd:${node.id}:${i}`),
        node_id: node.id,
        command_type: type,
        state,
        attempts: state === 'timed_out' ? 3 : 1,
        swap_operation_id: type.endsWith('_model') || type === 'drain_instance' ? uuid(`swap:${node.id}:${i}`) : null,
        instance_id: instanceId,
        progress_pct: state === 'succeeded' ? 100 : null,
        result: state === 'succeeded' ? { ok: true } : null,
        error_code: state === 'failed' ? 'file_in_use' : state === 'timed_out' ? 'timeout' : null,
        error_message: state === 'failed' ? 'blob is mapped by a running llama-server (pid 41822)' : state === 'timed_out' ? 'agent did not answer within 900 s' : null,
        expires_at: iso(at + 900_000),
        sent_at: iso(at + 40),
        accepted_at: iso(at + 180),
        completed_at: state === 'pending' ? null : iso(at + 2_000 + (hash32(`${node.id}${i}`) % 40_000)),
        created_at: iso(at),
      });
    });
  }
  return out;
}

/**
 * The fixture records are shared by identity (not cloned): the topology,
 * observability and automation mocks read the same objects, so a drain or a
 * new deployment shows up on every screen. Removal
 * mutates the arrays in place for the same reason.
 */
export const fleetStore = {
  clusters: [CLUSTER, extraCluster] as Cluster[],
  nodes: NODES as Node[],
  devices: DEVICES as Device[],
  slices: DEVICE_SLICES as DeviceSlice[],
  instances: INSTANCES as Instance[],
  deployments: DEPLOYMENTS as Deployment[],
  joinTokens: seedTokens(),
  certificates: seedCertificates(),
  commands: seedCommands(),
  credentials: CREDENTIALS,
  /** Filled by handlers/models.ts (cache items reference model versions). */
  cacheItems: [] as CacheItem[],
  /**
   * Instances created in this session borrow the metric profile of a fixture
   * instance of the same model, so their live numbers are not flat zeros.
   */
  metricAlias: new Map<string, string>(),
};

/** Remove matching records from a store array in place (other mocks hold the same array). */
export function removeWhere<T>(list: T[], predicate: (item: T) => boolean): void {
  for (let i = list.length - 1; i >= 0; i -= 1) if (predicate(list[i])) list.splice(i, 1);
}

// ─── Derived views ──────────────────────────────────────────────────────────

const LIVE_NODE_STATES: Node['status'][] = ['online', 'draining', 'drained', 'unhealthy'];

export function activeSlices(deviceId: string): DeviceSlice[] {
  return fleetStore.slices.filter((s) => s.device_id === deviceId && s.status !== 'released');
}

/** Scheduler headroom the control plane keeps free (SCHEDULER_VRAM_HEADROOM_PCT). */
const HEADROOM_PCT = 10;
/** A process GPU Deck neither runs nor observes, on the second L40S (another tenant). */
const STRAY_PROCESS = { pid: 61007, name: 'python3', bytes: 512 * 1024 * 1024 };

export function deviceView(device: Device, now = Date.now()): Device {
  const slices = activeSlices(device.id);
  const node = fleetStore.nodes.find((n) => n.id === device.node_id);
  const online = !!node && LIVE_NODE_STATES.includes(node.status);
  const onDevice = fleetStore.instances.filter((i) => i.device_ids.includes(device.id) && i.state !== 'stopped');
  const instanceIds = onDevice.map((i) => i.id);
  const reserved = slices.reduce((sum, s) => sum + s.vram_bytes, 0);
  // Per-process list (NVIDIA only): managed engines, observed external servers, stray processes.
  const processes: NonNullable<Device['processes']> = [];
  if (online && device.vendor === 'nvidia') {
    for (const inst of onDevice) {
      const deployment = fleetStore.deployments.find((d) => d.id === inst.deployment_id);
      const external = inst.origin === 'external';
      const bytes = external
        ? Number(deployment?.external?.process_vram_bytes ?? 0)
        : Math.round(Number((inst.resources as { vram_bytes?: number } | undefined)?.vram_bytes ?? 0) * 0.72);
      processes.push({
        pid: (inst.pid ?? 40_000) + (external ? 1 : 0),
        name: external ? 'VLLM::EngineCor' : inst.engine_name === 'llama_cpp' ? 'llama-server' : 'python3',
        used_memory_bytes: bytes,
        instance_id: inst.id,
        deployment_id: inst.deployment_id,
        deployment_name: inst.deployment_name ?? null,
        origin: external ? 'external' : 'managed',
      });
    }
    if (device.index === 1) processes.push({ pid: STRAY_PROCESS.pid, name: STRAY_PROCESS.name, used_memory_bytes: STRAY_PROCESS.bytes, instance_id: null, deployment_id: null, deployment_name: null, origin: null });
  }
  const outside = processes.filter((p) => p.origin !== 'managed').reduce((sum, p) => sum + (p.used_memory_bytes ?? 0), 0);
  const memoryUsed = online ? Math.min(device.vram_total_bytes, Math.max(gpuMemoryUsed(device.id, now), Math.round(reserved * 0.72) + outside)) : null;
  const headroom = Math.round((device.vram_total_bytes * HEADROOM_PCT) / 100);
  const known = processes.length > 0 || device.vendor === 'nvidia';
  const occupied = memoryUsed === null ? reserved : known ? Math.max(reserved, memoryUsed - outside) + outside : Math.max(reserved, memoryUsed);
  return {
    ...device,
    vram_reserved_bytes: reserved,
    slices,
    instance_ids: instanceIds,
    health: online ? device.health : 'unknown',
    last_seen_at: online ? iso(now - (hash32(device.id) % 3000)) : device.last_seen_at,
    vram_used_bytes: memoryUsed,
    vram_used_outside_bytes: memoryUsed !== null && known ? outside : null,
    vram_headroom_bytes: headroom,
    vram_free_bytes: Math.max(0, device.vram_total_bytes - headroom - occupied),
    vram_usage_stale: !online,
    processes,
    processes_ts: processes.length ? iso(now) : null,
    activity: !online ? 'detached' : processes.some((p) => p.origin === 'managed') || onDevice.some((i) => i.origin !== 'external') ? (processes.some((p) => p.origin !== 'managed') ? 'mixed' : 'gpu_deck') : processes.length ? 'external' : 'idle',
    outside_processes: processes.filter((p) => p.origin !== 'managed').length,
    node_detached: !online,
    live: online
      ? {
          ts: iso(now),
          utilization_pct: instanceIds.length ? gpuUtilization(device.id, now) : 0,
          memory_used_bytes: memoryUsed,
          power_watts: gpuPower(device.id, now),
          temperature_c: gpuTemperature(device.id, now),
        }
      : { ts: null, utilization_pct: null, memory_used_bytes: null, power_watts: null, temperature_c: null },
  };
}

export function nodeView(node: Node, now = Date.now()): Node {
  const fresh = LIVE_NODE_STATES.includes(node.status);
  const cached = fleetStore.cacheItems.filter((c) => c.node_id === node.id && c.state !== 'evicted');
  return {
    ...node,
    cache_used_bytes: cached.length ? cached.reduce((sum, c) => sum + c.size_bytes, 0) : node.cache_used_bytes,
    device_count: fleetStore.devices.filter((d) => d.node_id === node.id).length,
    instance_count: fleetStore.instances.filter((i) => i.node_id === node.id && i.state !== 'stopped').length,
    last_heartbeat_at: fresh ? iso(now - 900 - (hash32(node.id + Math.floor(now / 5000)) % 4200)) : node.last_heartbeat_at,
    ...nodeActivity(node, now),
  };
}

/** Node roll-up of the devices' live activity (as the control plane computes it). */
function nodeActivity(node: Node, now: number): Pick<Node, 'activity' | 'outside_processes' | 'detached'> {
  const detached = node.status === 'offline' || node.status === 'removed';
  const devices = fleetStore.devices.filter((d) => d.node_id === node.id).map((d) => deviceView(d, now));
  const kinds = new Set(devices.map((d) => d.activity));
  const outside = devices.reduce((sum, d) => sum + (d.outside_processes ?? 0), 0);
  const activity = detached ? 'detached' : kinds.has('mixed') || (kinds.has('external') && kinds.has('gpu_deck')) ? 'mixed' : kinds.has('external') ? 'external' : kinds.has('gpu_deck') ? 'gpu_deck' : kinds.has('stale') ? 'stale' : 'idle';
  return { activity, outside_processes: outside, detached };
}

export function clusterView(cluster: Cluster, now = Date.now()): Cluster {
  const nodes = fleetStore.nodes.filter((n) => n.cluster_id === cluster.id && n.status !== 'removed');
  const nodeIds = new Set(nodes.map((n) => n.id));
  const devices = fleetStore.devices.filter((d) => nodeIds.has(d.node_id)).map((d) => deviceView(d, now));
  return {
    ...cluster,
    counts: {
      nodes: nodes.length,
      nodes_online: nodes.filter((n) => n.status === 'online' || n.status === 'draining').length,
      devices: devices.length,
      instances: fleetStore.instances.filter((i) => nodeIds.has(i.node_id) && i.state !== 'stopped').length,
      vram_total_bytes: devices.reduce((s, d) => s + d.vram_total_bytes, 0),
      vram_used_bytes: devices.reduce((s, d) => s + (d.live?.memory_used_bytes ?? 0), 0),
    },
  };
}

export function instanceView(instance: Instance, now = Date.now()): Instance {
  if (instance.state !== 'ready') return { ...instance, live: {} };
  const key = fleetStore.metricAlias.get(instance.id) ?? instance.id;
  const s = engineSample(key, now);
  const base = instance.traffic_weight === 0 ? 0.1 : 1;
  return {
    ...instance,
    last_health_at: iso(now - 1500 - (hash32(instance.id) % 3000)),
    live: {
      ts: iso(now),
      active_sessions: Math.round(Number(s.active_sessions ?? 0) * base),
      waiting_sessions: Math.round(Number(s.waiting_sessions ?? 0) * base),
      kv_cache_usage_pct: Number(s.kv_cache_usage_pct ?? 0),
      generation_tps: Number(s.generation_tps ?? 0) * base,
      ttft_ms_p95: Number(s.ttft_ms_p95 ?? 0),
      requests_per_minute: (engineMetricValue('requests_per_minute', key, now, 60) ?? 0) * base,
      error_rate_pct: engineMetricValue('error_rate_pct', key, now, 60),
    },
  };
}

export function deploymentView(deployment: Deployment, now = Date.now()): Deployment {
  const instances = fleetStore.instances.filter((i) => i.deployment_id === deployment.id && i.state !== 'stopped').map((i) => instanceView(i, now));
  const ready = instances.filter((i) => i.state === 'ready');
  const sum = (k: keyof Schemas['InstanceLive']) => ready.reduce((acc, i) => acc + Number(i.live?.[k] ?? 0), 0);
  return {
    ...deployment,
    instances,
    ready_replicas: ready.length,
    live: ready.length
      ? {
          ts: iso(now),
          active_sessions: sum('active_sessions'),
          waiting_sessions: sum('waiting_sessions'),
          kv_cache_usage_pct: sum('kv_cache_usage_pct') / ready.length,
          generation_tps: sum('generation_tps'),
          ttft_ms_p95: Math.max(...ready.map((i) => Number(i.live?.ttft_ms_p95 ?? 0))),
          requests_per_minute: sum('requests_per_minute'),
          error_rate_pct: sum('error_rate_pct') / ready.length,
        }
      : {},
  };
}

// ─── Agent commands ─────────────────────────────────────────────────────────

/** Record an agent command and complete it after `ms` (accepted → running → succeeded). */
export function recordCommand(nodeId: string, type: string, opts: { instanceId?: string | null; ms?: number; swapId?: string | null } = {}): AgentCommand {
  const now = Date.now();
  const command: AgentCommand = {
    id: newId('cmd'),
    node_id: nodeId,
    command_type: type,
    state: 'sent',
    attempts: 1,
    swap_operation_id: opts.swapId ?? null,
    instance_id: opts.instanceId ?? null,
    progress_pct: 0,
    result: null,
    error_code: null,
    error_message: null,
    expires_at: iso(now + 900_000),
    sent_at: iso(now),
    accepted_at: null,
    completed_at: null,
    created_at: iso(now),
  };
  fleetStore.commands.unshift(command);
  setTimeout(() => {
    command.state = 'running';
    command.accepted_at = new Date().toISOString();
    command.progress_pct = 40;
  }, 400);
  setTimeout(() => {
    command.state = 'succeeded';
    command.progress_pct = 100;
    command.result = { ok: true };
    command.completed_at = new Date().toISOString();
  }, opts.ms ?? 2500);
  return command;
}

/** NodeFileCommandResult view of a recorded command (scan / hash endpoints). */
function fileCommand(cmd: AgentCommand, status: 200 | 202) {
  const done = cmd.state === 'succeeded' || cmd.state === 'failed';
  return {
    status: done ? 200 : status,
    data: {
      command_id: cmd.id,
      command: cmd.command_type,
      node_id: cmd.node_id,
      state: cmd.state,
      progress_pct: cmd.progress_pct ?? null,
      result: cmd.state === 'succeeded' ? cmd.result : null,
      error_code: cmd.error_code ?? null,
      error_message: cmd.error_message ?? null,
    },
  };
}

const OLLAMA_DIGESTS = {
  qwen3: '7f4030143c1c477224c5434f8272c662a8b042079a0a584f0a27a1684fe2e1fa',
  tinyllama: '2af3b81862c6be03c769683af18efdadb2c33f60ff32ab6f83e42c043d6c7816',
  nomic: '970aa74c0a90ef7482477cf803618e776e173c007bf957f635f1015bfcfef0e6',
};

/** agent-protocol ScanPathResult for the fixture Mac's model stores. */
function mockScan(path: string, home: string) {
  const p = path.replace(/^~(?=\/|$)/, home).replace(/\/+$/, '');
  const blob = (store: string, digest: string, size: number) => ({ path: `sha256-${digest}`, absolute_path: `${store}/blobs/sha256-${digest}`, size_bytes: size, format: 'gguf', sha256: digest, is_required: true });
  const candidates: Record<string, unknown>[] = [];
  if (p.includes('.ollama')) {
    const store = p.endsWith('/models') ? p : `${p}/models`;
    candidates.push(
      { path: `${store}/manifests/registry.ollama.ai/library/qwen3/0.6b`, kind: 'ollama_blob', format: 'gguf', files: [blob(store, OLLAMA_DIGESTS.qwen3, 522_653_767)], total_size_bytes: 522_653_767, suggested: { name: 'qwen3:0.6b', ollama_name: 'qwen3:0.6b', architecture: 'qwen3', quantization: 'Q4_K_M', context_size: 40960, parameter_count: 751_632_384 }, metadata: { 'general.architecture': 'qwen3', 'qwen3.context_length': 40960, 'qwen3.block_count': 28 } },
      { path: `${store}/manifests/registry.ollama.ai/library/tinyllama/latest`, kind: 'ollama_blob', format: 'gguf', files: [blob(store, OLLAMA_DIGESTS.tinyllama, 637_699_456)], total_size_bytes: 637_699_456, suggested: { name: 'tinyllama:latest', ollama_name: 'tinyllama:latest', architecture: 'llama', quantization: 'Q4_0', context_size: 2048 }, metadata: { 'general.architecture': 'llama', 'llama.context_length': 2048 } },
      { path: `${store}/manifests/registry.ollama.ai/library/nomic-embed-text/latest`, kind: 'ollama_blob', format: 'gguf', files: [blob(store, OLLAMA_DIGESTS.nomic, 274_290_656)], total_size_bytes: 274_290_656, suggested: { name: 'nomic-embed-text:latest', ollama_name: 'nomic-embed-text:latest', architecture: 'nomic-bert', quantization: 'F16', context_size: 2048 }, metadata: { 'general.architecture': 'nomic-bert' } },
    );
  } else if (p.includes('.lmstudio') || p.includes('huggingface') || p.endsWith('/models')) {
    const dir = `${p}/mlx-community/gemma-3-1b-it-qat-4bit`;
    candidates.push({
      path: dir,
      kind: p.includes('.lmstudio') ? 'lmstudio' : 'mlx_dir',
      format: 'mlx',
      files: [
        { path: 'model.safetensors', absolute_path: `${dir}/model.safetensors`, size_bytes: 731_825_420, format: 'mlx', sha256: null, is_required: true },
        { path: 'config.json', absolute_path: `${dir}/config.json`, size_bytes: 1_643, format: 'other', sha256: null, is_required: true },
        { path: 'tokenizer.json', absolute_path: `${dir}/tokenizer.json`, size_bytes: 33_384_570, format: 'other', sha256: null, is_required: true },
      ],
      total_size_bytes: 765_211_633,
      suggested: { name: 'gemma-3-1b-it-qat-4bit', architecture: 'gemma3_text', quantization: '4bit', context_size: 32768, repo_id: 'mlx-community/gemma-3-1b-it-qat-4bit' },
      metadata: { model_type: 'gemma3_text', max_position_embeddings: 32768 },
    });
  }
  return { path: p, candidates, truncated: false, visited: 40 + candidates.length * 17, seconds: 0.08 + candidates.length * 0.03 };
}

// ─── Mutations shared with the models feature ───────────────────────────────

export function releaseInstance(instance: Instance): void {
  instance.state = 'stopped';
  instance.stopped_at = new Date().toISOString();
  instance.state_changed_at = instance.stopped_at;
  instance.health = 'unknown';
  for (const slice of fleetStore.slices) {
    if (slice.instance_id === instance.id && slice.status !== 'released') {
      slice.status = 'released';
      slice.released_at = instance.stopped_at;
    }
  }
}

/** Walk an instance through draining → unloading → stopped. */
export function unloadInstance(instance: Instance, ms = 3500): AgentCommand {
  instance.state = 'draining';
  instance.state_changed_at = new Date().toISOString();
  const cmd = recordCommand(instance.node_id, 'unload_model', { instanceId: instance.id, ms });
  setTimeout(() => {
    if (instance.state === 'draining') instance.state = 'unloading';
  }, ms * 0.5);
  setTimeout(() => releaseInstance(instance), ms);
  return cmd;
}

function filterNodes(req: MockRequest, list: Node[]): Node[] {
  const q = req.query;
  const search = (q.get('search') ?? '').toLowerCase();
  const clusterId = q.get('cluster_id');
  const status = q.get('status');
  const os = q.get('os');
  const labels = q.getAll('label');
  return list.filter((n) => {
    if (n.status === 'removed' && status !== 'removed') return false;
    if (clusterId && n.cluster_id !== clusterId) return false;
    if (status && n.status !== status) return false;
    if (os && n.os !== os) return false;
    if (search && ![n.name, n.hostname ?? '', n.ip_address ?? '', ...Object.entries(n.labels).map(([k, v]) => `${k}=${v}`)].some((s) => s.toLowerCase().includes(search))) return false;
    for (const label of labels) {
      const [k, v] = label.split('=');
      if (n.labels[k] !== v) return false;
    }
    return true;
  });
}

// ─── Join simulation ────────────────────────────────────────────────────────

/** A token created in this session is "used" by a new agent ~25 s later, so the join page can show the node arriving. */
function simulateJoin(token: JoinToken): void {
  setTimeout(() => {
    if (token.revoked_at || token.used_at || Date.parse(token.expires_at) < Date.now()) return;
    const now = Date.now();
    const nodeId = newId('node');
    const name = token.node_name_hint || `gpu-node-${nodeId.slice(0, 4)}`;
    token.use_count = (token.use_count ?? 0) + 1;
    token.last_used_at = iso(now);
    if (token.use_count >= (token.max_uses ?? 1)) token.used_at = iso(now);
    token.used_by_node_id = nodeId;
    const node: Node = {
      id: nodeId,
      cluster_id: token.cluster_id,
      name,
      hostname: `${name}.lab.example.internal`,
      ip_address: '192.168.1.72',
      proxy_address: '192.168.1.72',
      proxy_port: 9443,
      os: 'linux',
      os_version: 'Ubuntu 24.04.2 LTS',
      arch: 'x86_64',
      kernel_version: '6.8.0-52-generic',
      agent_version: '1.1.0',
      protocol_version: 1,
      status: 'pending',
      status_message: 'Waiting for the first inventory report',
      labels: (token.labels as Record<string, string>) ?? {},
      capabilities: { engines: ['llama_cpp', 'vllm'] },
      cert_fingerprint: `SHA256:${hexId(`fp:${nodeId}`, 16)}`,
      cpu_cores: 24,
      memory_total_bytes: 128 * GiB,
      disks: [{ mount: '/', total_bytes: 900 * GiB, free_bytes: 812 * GiB, is_cache_dir: true }],
      cache_dir: '/var/lib/gpudeck/cache',
      cache_limit_bytes: 600 * GiB,
      cache_used_bytes: 0,
      last_heartbeat_at: iso(now),
      last_inventory_at: null,
      connected_at: iso(now),
      disconnected_at: null,
      drain_requested_at: null,
      drain_reason: null,
      drained_at: null,
      price_plan_id: null,
      kubernetes_node_name: null,
      cloud_instance_id: null,
      cloud_instance_type: null,
      metadata: {},
      created_at: iso(now),
      updated_at: iso(now),
    };
    fleetStore.nodes.push(node);
    fleetStore.certificates.push({
      id: newId('cert'),
      kind: 'node_agent',
      node_id: nodeId,
      serial: hexId(`serial:${nodeId}`, 16).toUpperCase(),
      fingerprint_sha256: hexId(`fp:${nodeId}`, 32) + hexId(`fp2:${nodeId}`, 32),
      subject: `CN=${nodeId},O=gpudeck-nodes`,
      not_before: iso(now),
      not_after: iso(now + 365 * 86_400_000),
      issued_at: iso(now),
      revoked_at: null,
      revocation_reason: null,
      is_active: true,
    });
    // Inventory arrives a few seconds later: one RTX 6000 Ada.
    setTimeout(() => {
      node.status = 'online';
      node.status_message = null;
      node.last_inventory_at = new Date().toISOString();
      fleetStore.devices.push({
        id: newId('device'),
        node_id: nodeId,
        node_name: name,
        cluster_id: token.cluster_id,
        vendor: 'nvidia',
        index: 0,
        uuid: `GPU-${hexId(`gpu:${nodeId}`, 32).replace(/(.{8})(.{4})(.{4})(.{4})(.{12})/, '$1-$2-$3-$4-$5')}`,
        name: 'NVIDIA RTX 6000 Ada Generation',
        vram_total_bytes: 49140 * MiB,
        vram_reserved_bytes: 0,
        is_unified_memory: false,
        driver_version: '570.124.06',
        runtime_version: 'CUDA 12.8',
        compute_capability: '8.9',
        pci_bus_id: '0000:01:00.0',
        numa_node: 0,
        max_power_watts: 300,
        core_count: 18176,
        health: 'healthy',
        health_reason: null,
        labels: {},
        extra: { ecc: 'enabled', mig: 'unsupported' },
        last_seen_at: new Date().toISOString(),
        live: {},
        slices: [],
        instance_ids: [],
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      });
    }, 6000);
  }, 25_000);
}

// ─── Handlers ───────────────────────────────────────────────────────────────

function clusterById(id: string) {
  return fleetStore.clusters.find((c) => c.id === id);
}
function nodeRecord(id: string) {
  return fleetStore.nodes.find((n) => n.id === id && n.status !== 'removed');
}

registerMockHandlers('fleet', [
  // ── Clusters ──────────────────────────────────────────────────────────────
  {
    method: 'GET',
    path: '/clusters',
    handle: (req) => {
      const q = req.query;
      const search = (q.get('search') ?? '').toLowerCase();
      const type = q.get('type');
      const status = q.get('status');
      const now = Date.now();
      const list = fleetStore.clusters
        .filter((c) => (!type || c.type === type) && (!status || c.status === status))
        .filter((c) => !search || [c.name, c.description ?? '', c.region ?? ''].some((s) => s.toLowerCase().includes(search)))
        .map((c) => clusterView(c, now));
      return paginate(sortBy(list as unknown as Record<string, unknown>[], q.get('sort')), q, 25);
    },
  },
  {
    method: 'POST',
    path: '/clusters',
    handle: (req) => {
      const body = bodyOf<Schemas['ClusterCreate']>(req);
      if (!body.name || !body.type) return fail(422, 'VALIDATION_ERROR', 'name and type are required');
      if (fleetStore.clusters.some((c) => c.name === body.name)) return fail(409, 'CONFLICT', `A cluster named "${body.name}" already exists`);
      const now = new Date().toISOString();
      const cluster: Cluster = {
        id: newId('cluster'),
        name: body.name,
        type: body.type,
        description: body.description ?? null,
        region: body.region ?? null,
        status: 'active',
        credential_id: body.credential_id ?? null,
        price_plan_id: body.price_plan_id ?? null,
        config: body.config ?? {},
        egress_allowlist: body.egress_allowlist ?? [],
        labels: body.labels ?? {},
        counts: {},
        created_at: now,
        updated_at: now,
      };
      fleetStore.clusters.push(cluster);
      return created(clusterView(cluster));
    },
  },
  { method: 'GET', path: '/clusters/:cluster_id', handle: (req) => { const c = clusterById(req.params.cluster_id); return c ? ok(clusterView(c)) : notFound('Cluster'); } },
  {
    method: 'DELETE',
    path: '/clusters/:cluster_id',
    handle: (req) => {
      const c = clusterById(req.params.cluster_id);
      if (!c) return notFound('Cluster');
      if (fleetStore.nodes.some((n) => n.cluster_id === c.id && n.status !== 'removed')) {
        return fail(409, 'CLUSTER_NOT_EMPTY', 'Remove or move every node before deleting the cluster');
      }
      removeWhere(fleetStore.clusters, (x) => x.id === c.id);
      return noContent();
    },
  },
  {
    method: 'GET',
    path: '/clusters/:cluster_id/nodes',
    handle: (req) => {
      const now = Date.now();
      const list = filterNodes(req, fleetStore.nodes.filter((n) => n.cluster_id === req.params.cluster_id)).map((n) => nodeView(n, now));
      return paginate(sortBy(list as unknown as Record<string, unknown>[], req.query.get('sort'), 'name'), req.query);
    },
  },

  // ── Join tokens ───────────────────────────────────────────────────────────
  {
    method: 'GET',
    path: '/nodes/join-tokens',
    handle: (req) => {
      const clusterId = req.query.get('cluster_id');
      const activeOnly = req.query.get('active_only') === 'true';
      const now = Date.now();
      const list = fleetStore.joinTokens
        .filter((t) => !clusterId || t.cluster_id === clusterId)
        .filter((t) => !activeOnly || (!t.used_at && !t.revoked_at && Date.parse(t.expires_at) > now));
      return paginate(sortBy(list as unknown as Record<string, unknown>[], '-created_at'), req.query);
    },
  },
  {
    method: 'POST',
    path: '/nodes/join-tokens',
    handle: (req) => {
      const body = bodyOf<Schemas['JoinTokenCreate']>(req);
      if (!body.cluster_id || !clusterById(body.cluster_id)) return fail(422, 'VALIDATION_ERROR', 'cluster_id must reference an existing cluster');
      const maxUses = Math.min(100000, Math.max(1, body.max_uses ?? 1));
      const ttl = Math.min(maxUses > 1 ? 525600 : 1440, Math.max(1, body.ttl_minutes ?? 15));
      const now = Date.now();
      const secret = hexId(`join-secret:${now}:${Math.random()}`, 32) + hexId(`join-secret-2:${now}`, 16).slice(0, 8);
      const token = `gdj_${secret}`;
      const record: JoinToken = {
        id: newId('join'),
        cluster_id: body.cluster_id,
        token_prefix: token.slice(0, 8),
        node_name_hint: body.node_name_hint ?? null,
        labels: body.labels ?? {},
        expires_at: iso(now + ttl * 60_000),
        max_uses: maxUses,
        use_count: 0,
        last_used_at: null,
        used_at: null,
        used_by_node_id: null,
        revoked_at: null,
        created_at: iso(now),
      };
      fleetStore.joinTokens.unshift(record);
      simulateJoin(record);
      const caFp = 'sha256:4e1f9c27a8b3d6e0f5c2a91b7d48e36f0c5a2b8d9e1f4a7c3b6d0e2f5a8c1b94';
      const hub = 'https://gpu-deck.example.com:8443';
      const caPemSha = '9b2c4e71a0d3f58e6c1b7a94d2e0f3c5b8a6d1e4f7c0b3a9d6e2f5c8a1b4d7e0';
      const nameArg = body.node_name_hint ? ` --name ${body.node_name_hint}` : '';
      return created({
        ...record,
        token,
        ca_fingerprint_sha256: caFp,
        join_command: `gpudeck-agent join --hub ${hub} --token ${token}${nameArg} --ca-fingerprint ${caFp}`,
        ca_pem_sha256: caPemSha,
        hub_url: hub,
        install_command: `sh -c 'c=$(mktemp) && curl -fsSk -o "$c" ${hub}/agent/v1/ca.pem && echo "${caPemSha}  $c" | (sha256sum -c - 2>/dev/null || shasum -a 256 -c -) >/dev/null && curl -fsSL --cacert "$c" -H "X-GPUDeck-Join-Token: ${token}" ${hub}/agent/v1/install.sh | sh'`,
        install_script_url: `${hub}/agent/v1/install.sh`,
      });
    },
  },
  {
    method: 'DELETE',
    path: '/nodes/join-tokens/:token_id',
    handle: (req) => {
      const t = fleetStore.joinTokens.find((x) => x.id === req.params.token_id);
      if (!t) return notFound('Join token');
      if (t.used_at) return fail(409, 'TOKEN_USED', 'The token was already used to join a node');
      t.revoked_at = new Date().toISOString();
      return noContent();
    },
  },

  // ── Nodes ─────────────────────────────────────────────────────────────────
  {
    method: 'GET',
    path: '/nodes',
    handle: (req) => {
      const now = Date.now();
      const list = filterNodes(req, fleetStore.nodes).map((n) => nodeView(n, now));
      return paginate(sortBy(list as unknown as Record<string, unknown>[], req.query.get('sort'), 'name'), req.query, 25);
    },
  },
  { method: 'GET', path: '/nodes/:node_id', handle: (req) => { const n = nodeRecord(req.params.node_id); return n ? ok(nodeView(n)) : notFound('Node'); } },
  {
    method: 'DELETE',
    path: '/nodes/:node_id',
    handle: (req) => {
      const n = nodeRecord(req.params.node_id);
      if (!n) return notFound('Node');
      const running = fleetStore.instances.filter((i) => i.node_id === n.id && i.state !== 'stopped');
      if (running.length > 0 && req.query.get('force') !== 'true') {
        return fail(409, 'NODE_HAS_INSTANCES', `${running.length} instance(s) still run on this node — drain it first or force removal`);
      }
      running.forEach((i) => releaseInstance(i));
      n.status = 'removed';
      n.disconnected_at = new Date().toISOString();
      for (const c of fleetStore.certificates) {
        if (c.node_id === n.id && !c.revoked_at) {
          c.revoked_at = n.disconnected_at;
          c.revocation_reason = 'node removed';
          c.is_active = false;
        }
      }
      removeWhere(fleetStore.devices, (d) => d.node_id === n.id);
      return noContent();
    },
  },
  {
    method: 'POST',
    path: '/nodes/:node_id/drain',
    handle: (req) => {
      const n = nodeRecord(req.params.node_id);
      if (!n) return notFound('Node');
      if (n.status === 'draining' || n.status === 'drained') return fail(409, 'ALREADY_DRAINING', 'The node is already draining');
      const body = bodyOf<Schemas['DrainRequest']>(req);
      const now = new Date().toISOString();
      n.status = 'draining';
      n.drain_requested_at = now;
      n.drain_reason = body.reason ?? null;
      const running = fleetStore.instances.filter((i) => i.node_id === n.id && i.state !== 'stopped');
      const commands = running.map((i) => unloadInstance(i, 5000));
      setTimeout(() => {
        if (n.status !== 'draining') return;
        n.status = 'drained';
        n.drained_at = new Date().toISOString();
      }, 6500);
      return accepted({ command_ids: commands.map((c) => c.id), swap_operation_ids: running.map(() => newId('swap')) });
    },
  },
  {
    method: 'POST',
    path: '/nodes/:node_id/undrain',
    handle: (req) => {
      const n = nodeRecord(req.params.node_id);
      if (!n) return notFound('Node');
      n.status = 'online';
      n.drain_requested_at = null;
      n.drain_reason = null;
      n.drained_at = null;
      return ok(nodeView(n));
    },
  },
  {
    method: 'GET',
    path: '/nodes/:node_id/devices',
    handle: (req) => {
      const now = Date.now();
      return paginate(fleetStore.devices.filter((d) => d.node_id === req.params.node_id).map((d) => deviceView(d, now)), req.query);
    },
  },
  {
    method: 'GET',
    path: '/nodes/:node_id/instances',
    handle: (req) => {
      const now = Date.now();
      return paginate(fleetStore.instances.filter((i) => i.node_id === req.params.node_id && i.state !== 'stopped').map((i) => instanceView(i, now)), req.query);
    },
  },
  {
    method: 'GET',
    path: '/nodes/:node_id/commands',
    handle: (req) => {
      const state = req.query.get('state');
      const list = fleetStore.commands.filter((c) => c.node_id === req.params.node_id && (!state || c.state === state));
      return paginate(list, req.query, 25);
    },
  },
  {
    method: 'POST',
    path: '/nodes/:node_id/scan',
    handle: (req) => {
      const n = nodeRecord(req.params.node_id);
      if (!n) return notFound('Node');
      const body = bodyOf<Schemas['NodeScanRequest']>(req);
      if (!body.path || !(body.path.startsWith('/') || body.path.startsWith('~'))) return fail(422, 'VALIDATION_ERROR', 'path must be absolute (or start with ~ on the node)');
      if (n.status === 'offline' || n.status === 'pending') return fail(409, 'NODE_OFFLINE', 'The agent is not connected');
      const cmd = recordCommand(n.id, 'scan_path', { ms: 1200 });
      const result = mockScan(body.path, n.os === 'macos' ? '/Users/alice' : '/home/gpudeck');
      setTimeout(() => {
        cmd.result = result as unknown as Record<string, unknown>;
      }, 1200);
      return fileCommand(cmd, 202);
    },
  },
  {
    method: 'POST',
    path: '/nodes/:node_id/hash',
    handle: (req) => {
      const n = nodeRecord(req.params.node_id);
      if (!n) return notFound('Node');
      const body = bodyOf<Schemas['NodeHashRequest']>(req);
      if (!body.paths?.length) return fail(422, 'VALIDATION_ERROR', 'paths must not be empty');
      if (n.status === 'offline' || n.status === 'pending') return fail(409, 'NODE_OFFLINE', 'The agent is not connected');
      const ms = Math.min(6000, 1500 + body.paths.length * 600);
      const cmd = recordCommand(n.id, 'hash_files', { ms });
      const files = body.paths.map((path) => {
        const digest = /sha256[-:]([0-9a-f]{64})$/.exec(path)?.[1];
        const missing = path.includes('missing');
        return missing
          ? { path, sha256: null, size_bytes: null, error: { code: 'path_not_found', message: `${path}: no such file` } }
          : { path, sha256: digest ?? (hexId(`sha:${path}`, 32) + hexId(`sha2:${path}`, 32)), size_bytes: 1_000_000 + (hash32(path) % 900_000_000), error: null };
      });
      const steps = [25, 55, 85];
      steps.forEach((pct, i) => setTimeout(() => (cmd.progress_pct = pct), ((i + 1) * ms) / (steps.length + 1)));
      setTimeout(() => {
        cmd.result = {
          root: body.root ?? null,
          files,
          hashes: Object.fromEntries(files.filter((f) => f.sha256).map((f) => [f.path, f.sha256])),
          bytes_hashed: files.reduce((s, f) => s + (f.size_bytes ?? 0), 0),
          seconds: ms / 1000,
        };
      }, ms);
      return fileCommand(cmd, 202);
    },
  },
  {
    method: 'GET',
    path: '/nodes/:node_id/commands/:command_id',
    handle: (req) => {
      const cmd = fleetStore.commands.find((c) => c.id === req.params.command_id && c.node_id === req.params.node_id);
      return cmd ? fileCommand(cmd, 200) : notFound('Agent command');
    },
  },
  { method: 'GET', path: '/nodes/:node_id/certificates', handle: (req) => paginate(sortBy(fleetStore.certificates.filter((c) => c.node_id === req.params.node_id) as unknown as Record<string, unknown>[], '-issued_at'), req.query) },
  {
    method: 'POST',
    path: '/nodes/:node_id/certificates/rotate',
    handle: (req) => {
      const n = nodeRecord(req.params.node_id);
      if (!n) return notFound('Node');
      if (n.status !== 'online' && n.status !== 'draining' && n.status !== 'drained') return fail(409, 'NODE_OFFLINE', 'The agent must be connected to rotate its certificate');
      const cmd = recordCommand(n.id, 'rotate_cert', { ms: 3000 });
      setTimeout(() => {
        const now = Date.now();
        const old = fleetStore.certificates.find((c) => c.node_id === n.id && c.is_active);
        const fresh: NodeCertificate = {
          id: newId('cert'),
          kind: 'node_agent',
          node_id: n.id,
          serial: hexId(`serial:${now}`, 16).toUpperCase(),
          fingerprint_sha256: hexId(`fp:${now}`, 32) + hexId(`fp2:${now}`, 32),
          subject: `CN=${n.id},O=gpudeck-nodes`,
          not_before: iso(now),
          not_after: iso(now + 365 * 86_400_000),
          issued_at: iso(now),
          revoked_at: null,
          revocation_reason: null,
          is_active: true,
        };
        fleetStore.certificates.push(fresh);
        if (old) {
          old.is_active = false;
          old.revoked_at = iso(now);
          old.revocation_reason = 'superseded (rotation)';
        }
        n.cert_fingerprint = `SHA256:${fresh.fingerprint_sha256.slice(0, 4)}…${fresh.fingerprint_sha256.slice(-4)}`;
      }, 3200);
      return accepted({ command_ids: [cmd.id], swap_operation_ids: [] });
    },
  },
  {
    method: 'POST',
    path: '/nodes/:node_id/certificates/:cert_id/revoke',
    handle: (req) => {
      const cert = fleetStore.certificates.find((c) => c.id === req.params.cert_id && c.node_id === req.params.node_id);
      if (!cert) return notFound('Certificate');
      if (cert.revoked_at) return fail(409, 'ALREADY_REVOKED', 'The certificate is already revoked');
      const body = bodyOf<Schemas['RevokeRequest']>(req);
      cert.revoked_at = new Date().toISOString();
      cert.revocation_reason = body.reason ?? 'revoked by admin';
      if (cert.is_active) {
        cert.is_active = false;
        const n = nodeRecord(req.params.node_id);
        if (n) {
          n.status = 'offline';
          n.status_message = 'Certificate revoked — the hub closed the connection (4003)';
          n.disconnected_at = cert.revoked_at;
          n.last_heartbeat_at = cert.revoked_at;
        }
      }
      return ok(cert);
    },
  },

  // ── Devices and slices ────────────────────────────────────────────────────
  {
    method: 'GET',
    path: '/devices',
    handle: (req) => {
      const q = req.query;
      const nodeId = q.get('node_id');
      const clusterId = q.get('cluster_id');
      const vendor = q.get('vendor');
      const health = q.get('health');
      const minFree = Number(q.get('free_vram_min_bytes') ?? 0);
      const now = Date.now();
      const list = fleetStore.devices
        .map((d) => deviceView(d, now))
        .filter(
          (d) =>
            (!nodeId || d.node_id === nodeId) &&
            (!clusterId || d.cluster_id === clusterId) &&
            (!vendor || d.vendor === vendor) &&
            (!health || d.health === health) &&
            (!minFree || d.vram_total_bytes - (d.vram_reserved_bytes ?? 0) >= minFree),
        );
      return paginate(sortBy(list as unknown as Record<string, unknown>[], q.get('sort'), 'node_name'), q, 50);
    },
  },
  { method: 'GET', path: '/devices/:device_id', handle: (req) => { const d = fleetStore.devices.find((x) => x.id === req.params.device_id); return d ? ok(deviceView(d)) : notFound('Device'); } },
  {
    method: 'GET',
    path: '/devices/:device_id/slices',
    handle: (req) => {
      const includeReleased = req.query.get('include_released') === 'true';
      const list = fleetStore.slices.filter((s) => s.device_id === req.params.device_id && (includeReleased || s.status !== 'released'));
      return paginate(sortBy(list as unknown as Record<string, unknown>[], '-created_at'), req.query);
    },
  },
  {
    method: 'GET',
    path: '/device-slices',
    handle: (req) => {
      const nodeId = req.query.get('node_id');
      const deploymentId = req.query.get('deployment_id');
      const deviceIds = nodeId ? new Set(fleetStore.devices.filter((d) => d.node_id === nodeId).map((d) => d.id)) : null;
      const list = fleetStore.slices.filter((s) => s.status !== 'released' && (!deviceIds || deviceIds.has(s.device_id)) && (!deploymentId || s.deployment_id === deploymentId));
      return paginate(list, req.query, 100);
    },
  },
]);

// Read-only pickers for resources other mocks own (vault)
// — fallbacks, so the owning handlers win.
registerMockHandlers(
  'fleet-pickers',
  asFallback([
    {
      method: 'GET',
      path: '/vault',
      handle: (req) => {
        const type = req.query.get('type');
        const status = req.query.get('status');
        const list = fleetStore.credentials.filter((c) => (!type || c.type === type) && (!status || c.status === status));
        return paginate(list, req.query, 200);
      },
    },
  ]),
);

/**
 * Cluster / node updates (rename, labels).
 */
const OWNED_PATCHES: MockHandler[] = [
  {
    method: 'PATCH',
    path: '/clusters/:cluster_id',
    handle: (req) => {
      const c = clusterById(req.params.cluster_id);
      if (!c) return notFound('Cluster');
      Object.assign(c, bodyOf<Schemas['ClusterUpdate']>(req), { updated_at: new Date().toISOString() });
      return ok(clusterView(c));
    },
  },
  {
    method: 'PATCH',
    path: '/nodes/:node_id',
    handle: (req) => {
      const n = nodeRecord(req.params.node_id);
      if (!n) return notFound('Node');
      const body = bodyOf<Schemas['NodeUpdate']>(req);
      if (body.name && fleetStore.nodes.some((x) => x.id !== n.id && x.cluster_id === n.cluster_id && x.name === body.name)) {
        return fail(409, 'NODE_NAME_TAKEN', `Another node in this cluster is called "${body.name}"`);
      }
      Object.assign(n, body, { updated_at: new Date().toISOString() });
      for (const d of fleetStore.devices) if (d.node_id === n.id) d.node_name = n.name;
      for (const i of fleetStore.instances) if (i.node_id === n.id) i.node_name = n.name;
      return ok(nodeView(n));
    },
  },
];

registerMockHandlers('fleet-updates', OWNED_PATCHES);

// ─── Direct slices (POST / PATCH / DELETE /devices/:id/slices) ─────────────

function freeBytes(device: Device, exceptId?: string): number {
  const usable = device.vram_total_bytes * (device.is_unified_memory ? 0.75 : 0.9);
  return usable - activeSlices(device.id).filter((s) => s.id !== exceptId).reduce((sum, s) => sum + s.vram_bytes, 0);
}

function sliceResult(slice: DeviceSlice, extra: Partial<Schemas['DeviceSliceResult']> = {}): Schemas['DeviceSliceResult'] {
  return { slice, deployment_id: slice.deployment_id ?? null, instance_id: slice.instance_id ?? null, gpu_instance_id: slice.gpu_instance_id ?? null, rolling_update: false, command_ids: [], message: null, ...extra };
}

registerMockHandlers('fleet-slices', [
  {
    method: 'POST',
    path: '/devices/:device_id/slices',
    handle: (req) => {
      const device = fleetStore.devices.find((d) => d.id === req.params.device_id);
      if (!device) return notFound('Device');
      const body = bodyOf<Schemas['DeviceSliceCreate']>(req);
      const deployment = body.deployment_id ? fleetStore.deployments.find((d) => d.id === body.deployment_id) : undefined;
      if (body.deployment_id && !deployment) return notFound('Deployment');
      if (deployment && !deployment.slice_vram_bytes) return fail(422, 'VALIDATION_ERROR', `${deployment.name} does not run on GPU slices`);
      const vram = deployment ? (deployment.slice_vram_bytes ?? 0) : (body.vram_bytes ?? 0);
      if (vram <= 0) return fail(422, 'VALIDATION_ERROR', 'vram_bytes is required for a standalone slice');
      const free = freeBytes(device);
      if (vram > free) return fail(409, 'NO_CAPACITY', `Only ${(free / GiB).toFixed(1)} GiB is unreserved on ${device.node_name} / ${device.name} #${device.index}`);
      const now = new Date().toISOString();
      const slice: DeviceSlice = {
        id: newId('slice'),
        device_id: device.id,
        instance_id: null,
        deployment_id: deployment?.id ?? null,
        gpu_instance_id: null,
        name: deployment ? deployment.name : (body.name ?? null),
        vram_bytes: vram,
        compute_pct: deployment ? (deployment.slice_compute_pct ?? 100) : (body.compute_pct ?? 100),
        status: 'reserved',
        created_at: now,
        released_at: null,
      };
      fleetStore.slices.push(slice);
      if (deployment) {
        deployment.replicas += 1;
        // The replica loads in a few seconds.
        setTimeout(() => {
          if (slice.status === 'reserved') slice.status = 'active';
        }, 4000);
      }
      return created(sliceResult(slice, deployment ? { message: `Placing replica ${deployment.replicas} of ${deployment.name} on this GPU`, command_ids: [newId('command')] } : {}));
    },
  },
  {
    method: 'PATCH',
    path: '/devices/:device_id/slices/:slice_id',
    handle: (req) => {
      const device = fleetStore.devices.find((d) => d.id === req.params.device_id);
      const slice = fleetStore.slices.find((s) => s.id === req.params.slice_id && s.device_id === req.params.device_id);
      if (!device || !slice || slice.status === 'released') return notFound('Slice');
      if (slice.gpu_instance_id) return fail(409, 'CONFLICT', 'The slice is held by an SSH GPU instance — stop it first');
      const body = bodyOf<Schemas['DeviceSliceUpdate']>(req);
      const vram = body.vram_bytes ?? slice.vram_bytes;
      const free = freeBytes(device, slice.id);
      if (vram > free) return fail(409, 'NO_CAPACITY', `Only ${(free / GiB).toFixed(1)} GiB is unreserved on this GPU`);
      if (slice.deployment_id) {
        const deployment = fleetStore.deployments.find((d) => d.id === slice.deployment_id);
        if (deployment) {
          deployment.slice_vram_bytes = vram;
          deployment.slice_compute_pct = body.compute_pct ?? deployment.slice_compute_pct;
          for (const s of fleetStore.slices) {
            if (s.deployment_id === deployment.id && s.status !== 'released') {
              s.vram_bytes = vram;
              s.compute_pct = body.compute_pct ?? s.compute_pct;
            }
          }
          return { status: 202, data: sliceResult(slice, { rolling_update: true, message: `Rolling update of ${deployment.name} started — ${deployment.replicas} replica(s) are replaced one at a time`, command_ids: [newId('command')] }) };
        }
      }
      slice.vram_bytes = vram;
      slice.compute_pct = body.compute_pct ?? slice.compute_pct;
      if (body.name !== undefined) slice.name = body.name;
      return ok(sliceResult(slice));
    },
  },
  {
    method: 'DELETE',
    path: '/devices/:device_id/slices/:slice_id',
    handle: (req) => {
      const slice = fleetStore.slices.find((s) => s.id === req.params.slice_id && s.device_id === req.params.device_id);
      if (!slice || slice.status === 'released') return notFound('Slice');
      const inUse = !!slice.instance_id || !!slice.gpu_instance_id;
      if (inUse && req.query.get('force') !== 'true') return fail(409, 'CONFLICT', 'The slice is in use — release it with force=true to remove the replica / stop the instance');
      if (slice.deployment_id) {
        const deployment = fleetStore.deployments.find((d) => d.id === slice.deployment_id);
        if (deployment) deployment.replicas = Math.max(deployment.min_replicas ?? 0, deployment.replicas - 1);
      }
      slice.status = 'released';
      slice.released_at = new Date().toISOString();
      return ok(sliceResult(slice, { message: slice.gpu_instance_id ? 'Stopping the SSH instance, then releasing the slice' : slice.deployment_id ? 'Removing the replica, then releasing the slice' : 'Slice released' }));
    },
  },
]);

// ─── Agent downloads (GET /agent/downloads, /agent/downloads/:v/:target) ────

const AGENT_VERSION = '0.2.0';
const AGENT_BUILDS: Schemas['AgentBuild'][] = [
  { target: 'x86_64-unknown-linux-gnu', os: 'linux', arch: 'x86_64', size: 18_734_112 },
  { target: 'aarch64-unknown-linux-gnu', os: 'linux', arch: 'aarch64', size: 17_201_504 },
  { target: 'aarch64-apple-darwin', os: 'macos', arch: 'aarch64', size: 15_992_368 },
].map((b) => ({
  version: AGENT_VERSION,
  target: b.target,
  filename: 'gpudeck-agent',
  size_bytes: b.size,
  sha256: hexId(`agent-sha:${b.target}`, 32) + hexId(`agent-sha2:${b.target}`, 32),
  os: b.os as Schemas['AgentBuild']['os'],
  arch: b.arch as Schemas['AgentBuild']['arch'],
  built_at: iso(T0 - 2 * 86_400_000),
  download_path: `/agent/downloads/${AGENT_VERSION}/${b.target}`,
}));

registerMockHandlers('agent-downloads', [
  {
    method: 'GET',
    path: '/agent/downloads',
    handle: () => ok(AGENT_BUILDS, { latest_version: AGENT_VERSION, base_url: 'https://gpu-deck.example.com:8443/agent/v1', release_url: 'https://github.com/sur950/gpu-deck/releases/download' }),
  },
  {
    method: 'GET',
    path: '/agent/downloads/:version/:target/sha256',
    handle: (req) => {
      const build = AGENT_BUILDS.find((b) => b.target === req.params.target);
      return build ? { raw: `${build.sha256}  ${build.filename}\n` } : notFound('Agent build');
    },
  },
  {
    method: 'GET',
    path: '/agent/downloads/:version/:target',
    handle: (req) => {
      const build = AGENT_BUILDS.find((b) => b.target === req.params.target);
      if (!build) return notFound('Agent build');
      return { raw: `mock gpudeck-agent ${build.version} for ${build.target}\n`, headers: { 'content-type': 'application/octet-stream', 'content-disposition': `attachment; filename="${build.filename}"`, 'x-checksum-sha256': build.sha256 } };
    },
  },
]);
