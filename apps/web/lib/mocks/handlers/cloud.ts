/**
 * Cloud mocks: providers, regions, GPU instance
 * types with list prices, quotas, credential checks and the VM lifecycle
 * (pending → provisioning → booting → running with the node joined; draining
 * → stopping → stopped; draining → terminating → terminated). A VM that
 * "joins" adds a node + GPU to the shared fleet store, so it shows up on the
 * Nodes, GPUs and Topology screens like a real join.
 */

import { CLUSTER, DEVICES, GiB, NODES } from '@/lib/mocks/fixtures/fleet';
import { hash32, uuid } from '@/lib/mocks/fixtures/random';
import { fleetStore, newId } from '@/lib/mocks/handlers/fleet';
import { recordAudit } from '@/lib/mocks/handlers/security';
import { bodyOf, created, fail, noContent, notFound, ok, paginate, registerMockHandlers, sortBy } from '@/lib/mocks/registry';
import type { MockRequest, Schemas } from '@/lib/mocks/types';

type CloudNode = Schemas['CloudNode'];
type InstanceType = Schemas['CloudInstanceType'];
type Provider = Schemas['cloud_provider'];

const iso = (ms: number) => new Date(ms).toISOString();
const NOW = Date.now();
const MIN = 60_000;
const HOUR = 60 * MIN;

// ─── Catalog ────────────────────────────────────────────────────────────────

const PROVIDERS: Schemas['CloudProvider'][] = [
  { id: 'aws', name: 'Amazon Web Services', cluster_type: 'cloud_aws', credential_type: 'cloud_account', credential_fields: ['access_key_id', 'secret_access_key', 'session_token?', 'role_arn?'], sdk: 'boto3', enabled: true, pricing: 'AWS Pricing API (on-demand, Linux)', launch_options: ['subnet_id', 'security_group_ids', 'key_name', 'iam_instance_profile', 'ami'], verified_live: false, notes: 'Verified with moto + recorded responses.' },
  { id: 'azure', name: 'Microsoft Azure', cluster_type: 'cloud_azure', credential_type: 'cloud_account', credential_fields: ['tenant_id', 'client_id', 'client_secret', 'subscription_id'], sdk: 'azure-mgmt-compute', enabled: true, pricing: 'Azure Retail Prices API', launch_options: ['resource_group', 'vnet', 'subnet', 'admin_username', 'ssh_public_key'], verified_live: false, notes: 'Recorded responses only.' },
  { id: 'gcp', name: 'Google Cloud', cluster_type: 'cloud_gcp', credential_type: 'service_account', credential_fields: ['service_account_json', 'project_id?'], sdk: 'google-cloud-compute', enabled: true, pricing: 'none (Cloud Billing Catalog not queried) — prices are null', launch_options: ['project', 'network', 'subnetwork', 'service_account_email'], verified_live: false, notes: 'Recorded responses only.' },
  { id: 'digitalocean', name: 'DigitalOcean', cluster_type: 'cloud_do', credential_type: 'api_key', credential_fields: ['token'], sdk: 'httpx (REST v2)', enabled: true, pricing: 'DigitalOcean sizes API', launch_options: ['vpc_uuid', 'ssh_keys', 'tags'], verified_live: false, notes: 'Recorded responses only.' },
];

const REGIONS: Record<Provider, Schemas['CloudRegion'][]> = {
  aws: [
    { id: 'us-east-1', name: 'US East (N. Virginia)', zones: ['us-east-1a', 'us-east-1b', 'us-east-1c', 'us-east-1d', 'us-east-1f'], available: true },
    { id: 'us-west-2', name: 'US West (Oregon)', zones: ['us-west-2a', 'us-west-2b', 'us-west-2c'], available: true },
    { id: 'ap-south-1', name: 'Asia Pacific (Mumbai)', zones: ['ap-south-1a', 'ap-south-1b', 'ap-south-1c'], available: true },
    { id: 'eu-central-1', name: 'Europe (Frankfurt)', zones: ['eu-central-1a', 'eu-central-1b'], available: true },
  ],
  azure: [
    { id: 'eastus', name: 'East US', zones: ['1', '2', '3'], available: true },
    { id: 'westeurope', name: 'West Europe', zones: ['1', '2', '3'], available: true },
    { id: 'centralindia', name: 'Central India', zones: ['1', '2', '3'], available: true },
  ],
  gcp: [
    { id: 'us-central1', name: 'Iowa', zones: ['us-central1-a', 'us-central1-b', 'us-central1-c'], available: true },
    { id: 'asia-south1', name: 'Mumbai', zones: ['asia-south1-a', 'asia-south1-b'], available: true },
    { id: 'europe-west4', name: 'Netherlands', zones: ['europe-west4-a', 'europe-west4-b'], available: true },
  ],
  digitalocean: [
    { id: 'nyc2', name: 'New York 2', zones: [], available: true },
    { id: 'tor1', name: 'Toronto 1', zones: [], available: true },
    { id: 'ams3', name: 'Amsterdam 3', zones: [], available: false },
  ],
};

function t(id: string, family: string, gpuModel: string | null, vendor: string | null, gpus: number, vramGiB: number | null, vcpus: number, memGiB: number, price: number | null, priceSource: string | null, regions: string[], extra: Record<string, unknown> = {}): InstanceType {
  return {
    id,
    family,
    gpu_model: gpuModel,
    gpu_vendor: vendor,
    gpu_count: gpus,
    gpu_vram_bytes: vramGiB ? vramGiB * GiB : null,
    total_vram_bytes: vramGiB ? vramGiB * GiB * Math.max(1, gpus) : null,
    vcpus,
    memory_bytes: memGiB * GiB,
    price_per_hour_usd: price,
    price_source: priceSource,
    regions,
    description: null,
    extra,
  };
}

const AWS_ALL = REGIONS.aws.map((r) => r.id);
const AZ_ALL = REGIONS.azure.map((r) => r.id);
const GCP_ALL = REGIONS.gcp.map((r) => r.id);
const DO_ALL = ['nyc2', 'tor1'];

const TYPES: Record<Provider, InstanceType[]> = {
  aws: [
    t('g4dn.xlarge', 'g4dn', 'Tesla T4', 'nvidia', 1, 16, 4, 16, 0.526, 'AWS Pricing API', AWS_ALL, { architectures: ['x86_64'] }),
    t('g5.xlarge', 'g5', 'A10G', 'nvidia', 1, 24, 4, 16, 1.006, 'AWS Pricing API', AWS_ALL, { architectures: ['x86_64'] }),
    t('g5.12xlarge', 'g5', 'A10G', 'nvidia', 4, 24, 48, 192, 5.672, 'AWS Pricing API', AWS_ALL, { architectures: ['x86_64'] }),
    t('g6.xlarge', 'g6', 'L4', 'nvidia', 1, 24, 4, 16, 0.805, 'AWS Pricing API', AWS_ALL, { architectures: ['x86_64'] }),
    t('g6e.xlarge', 'g6e', 'L40S', 'nvidia', 1, 48, 4, 32, 1.861, 'AWS Pricing API', AWS_ALL, { architectures: ['x86_64'] }),
    t('g6e.12xlarge', 'g6e', 'L40S', 'nvidia', 4, 48, 48, 384, 10.493, 'AWS Pricing API', ['us-east-1', 'us-west-2'], { architectures: ['x86_64'] }),
    t('p4d.24xlarge', 'p4d', 'A100', 'nvidia', 8, 40, 96, 1152, 32.773, 'AWS Pricing API', ['us-east-1', 'us-west-2'], { architectures: ['x86_64'] }),
    t('p5.48xlarge', 'p5', 'H100', 'nvidia', 8, 80, 192, 2048, 98.32, 'AWS Pricing API', ['us-east-1', 'us-west-2', 'ap-south-1'], { architectures: ['x86_64'] }),
    t('g5g.xlarge', 'g5g', 'T4G', 'nvidia', 1, 16, 4, 8, 0.42, 'AWS Pricing API', AWS_ALL, { architectures: ['arm64'] }),
  ],
  azure: [
    t('Standard_NC4as_T4_v3', 'NCasT4_v3', 'Tesla T4', 'nvidia', 1, 16, 4, 28, 0.526, 'Azure Retail Prices', AZ_ALL, { zones: ['1', '2', '3'] }),
    t('Standard_NV36ads_A10_v5', 'NVadsA10_v5', 'A10', 'nvidia', 1, 24, 36, 440, 3.2, 'Azure Retail Prices', AZ_ALL, { zones: ['1', '2'], partial_gpu: false }),
    t('Standard_NV12ads_A10_v5', 'NVadsA10_v5', 'A10 (1/3)', 'nvidia', 1, 8, 12, 110, 0.908, 'Azure Retail Prices', AZ_ALL, { zones: ['1', '2'], partial_gpu: true }),
    t('Standard_NC24ads_A100_v4', 'NCadsA100_v4', 'A100', 'nvidia', 1, 80, 24, 220, 3.673, 'Azure Retail Prices', ['eastus', 'westeurope'], { zones: ['1', '2', '3'] }),
    t('Standard_ND96isr_H100_v5', 'NDisrH100_v5', 'H100', 'nvidia', 8, 80, 96, 1900, 98.32, 'Azure Retail Prices', ['eastus'], { zones: ['1'] }),
    t('Standard_ND96isr_MI300X_v5', 'NDisrMI300X_v5', 'MI300X', 'amd', 8, 192, 96, 1850, 48.0, 'Azure Retail Prices', ['eastus'], { zones: ['1'] }),
  ],
  gcp: [
    t('g2-standard-4', 'g2', 'L4', 'nvidia', 1, 24, 4, 16, null, null, GCP_ALL, { machine_type: 'g2-standard-4' }),
    t('g2-standard-48', 'g2', 'L4', 'nvidia', 4, 24, 48, 192, null, null, GCP_ALL, { machine_type: 'g2-standard-48' }),
    t('a2-highgpu-1g', 'a2', 'A100', 'nvidia', 1, 40, 12, 85, null, null, ['us-central1', 'europe-west4'], { machine_type: 'a2-highgpu-1g' }),
    t('a3-highgpu-8g', 'a3', 'H100', 'nvidia', 8, 80, 208, 1872, null, null, ['us-central1'], { machine_type: 'a3-highgpu-8g' }),
    t('n1-standard-8+nvidia-tesla-t4x1', 'n1', 'Tesla T4', 'nvidia', 1, 16, 8, 30, null, null, GCP_ALL, { machine_type: 'n1-standard-8', accelerator: 'nvidia-tesla-t4' }),
  ],
  digitalocean: [
    t('gpu-l40sx1-48gb', 'gpu', 'L40S', 'nvidia', 1, 48, 8, 64, 1.57, 'DigitalOcean sizes', DO_ALL),
    t('gpu-6000adax1-48gb', 'gpu', 'RTX 6000 Ada', 'nvidia', 1, 48, 8, 64, 1.57, 'DigitalOcean sizes', DO_ALL),
    t('gpu-h100x1-80gb', 'gpu', 'H100', 'nvidia', 1, 80, 20, 240, 3.39, 'DigitalOcean sizes', DO_ALL),
    t('gpu-h100x8-640gb', 'gpu', 'H100', 'nvidia', 8, 80, 160, 1920, 23.92, 'DigitalOcean sizes', ['nyc2']),
    t('gpu-mi300x1-192gb', 'gpu', 'MI300X', 'amd', 1, 192, 20, 240, 1.99, 'DigitalOcean sizes', ['tor1']),
  ],
};

// ─── Cloud clusters (added next to the fleet fixtures' aws-mumbai) ──────────

const DO_CLUSTER: Schemas['Cluster'] = {
  ...CLUSTER,
  id: uuid('cluster:do-nyc-burst'),
  name: 'do-nyc-burst',
  type: 'cloud_do',
  description: 'DigitalOcean GPU droplets for overflow traffic',
  region: 'nyc2',
  status: 'active',
  credential_id: uuid('cred:do-token'),
  price_plan_id: null,
  config: { cloud: { region: 'nyc2', max_nodes: 4, auto_terminate_idle_minutes: 60, disk_gb: 200 } },
  labels: { env: 'burst' },
  counts: {},
  created_at: iso(NOW - 5 * 24 * HOUR),
  updated_at: iso(NOW - 2 * HOUR),
};
const AZURE_CLUSTER: Schemas['Cluster'] = {
  ...CLUSTER,
  id: uuid('cluster:azure-eastus-ml'),
  name: 'azure-eastus-ml',
  type: 'cloud_azure',
  description: 'Azure subscription for A100 fine-tunes',
  region: 'eastus',
  status: 'active',
  credential_id: uuid('cred:azure-sub'),
  price_plan_id: null,
  config: { cloud: { region: 'eastus', resource_group: 'gpu-deck-rg', max_nodes: 2 } },
  labels: { env: 'ml' },
  counts: {},
  created_at: iso(NOW - 3 * 24 * HOUR),
  updated_at: iso(NOW - 3 * 24 * HOUR),
};
for (const c of [DO_CLUSTER, AZURE_CLUSTER]) if (!fleetStore.clusters.some((x) => x.id === c.id)) fleetStore.clusters.push(c);

function providerOf(cluster: Schemas['Cluster'] | undefined): Provider | null {
  return (PROVIDERS.find((p) => p.cluster_type === cluster?.type)?.id as Provider | undefined) ?? null;
}

function cloudConfig(cluster: Schemas['Cluster']): Record<string, unknown> {
  const cfg = (cluster.config ?? {}) as Record<string, unknown>;
  return (cfg.cloud as Record<string, unknown> | undefined) ?? {};
}

// ─── VM records + lifecycle ─────────────────────────────────────────────────

interface Rec {
  vm: CloudNode;
  enteredAt: number;
  /** Simulated provider failure at provisioning. */
  failWith?: { code: string; message: string };
}

const PHASE: Partial<Record<CloudNode['status'], number>> = {
  pending: 1_200,
  provisioning: 3_500,
  booting: 9_000,
  draining: 2_500,
  stopping: 2_500,
  starting: 4_000,
  terminating: 3_000,
};

function base(partial: Partial<CloudNode> & Pick<CloudNode, 'id' | 'cluster_id' | 'provider' | 'name' | 'region' | 'instance_type' | 'status'>): CloudNode {
  const now = iso(Date.now());
  return {
    cluster_name: fleetStore.clusters.find((c) => c.id === partial.cluster_id)?.name ?? null,
    node_id: null,
    node_name: null,
    node_status: null,
    credential_id: fleetStore.clusters.find((c) => c.id === partial.cluster_id)?.credential_id ?? null,
    join_token_id: null,
    zone: null,
    image: null,
    disk_gb: 200,
    provider_instance_id: null,
    provider_state: null,
    status_message: null,
    pending_action: null,
    public_ip: null,
    private_ip: null,
    gpu_model: null,
    gpu_count: 0,
    gpu_vram_bytes: null,
    vcpus: null,
    memory_bytes: null,
    price_per_hour_usd: null,
    price_source: null,
    price_plan_suggestion: null,
    auto_terminate_idle_minutes: null,
    idle_since: null,
    labels: {},
    tags: {},
    options: {},
    last_error: null,
    created_by: null,
    launched_at: null,
    joined_at: null,
    drain_started_at: null,
    stopped_at: null,
    terminated_at: null,
    last_polled_at: now,
    created_at: now,
    updated_at: now,
    ...partial,
  };
}

function fromType(type: InstanceType): Partial<CloudNode> {
  return {
    gpu_model: type.gpu_model ?? null,
    gpu_count: type.gpu_count,
    gpu_vram_bytes: type.gpu_vram_bytes ?? null,
    vcpus: type.vcpus ?? null,
    memory_bytes: type.memory_bytes ?? null,
    price_per_hour_usd: type.price_per_hour_usd ?? null,
    price_source: type.price_source ?? null,
    price_plan_suggestion:
      type.price_per_hour_usd && type.gpu_count
        ? { name: `${type.id} list price`, usd_per_hour: type.price_per_hour_usd, usd_per_gpu_minute: Math.round((type.price_per_hour_usd / 60 / type.gpu_count) * 1e6) / 1e6, source: type.price_source ?? null }
        : null,
  };
}

function providerInstanceId(provider: Provider, seed: string): string {
  const h = hash32(seed).toString(16).padStart(8, '0');
  if (provider === 'aws') return `i-0${h}${h.slice(0, 8)}`;
  if (provider === 'azure') return `/subscriptions/7f3c…0b91/resourceGroups/gpu-deck-rg/providers/Microsoft.Compute/virtualMachines/${seed}`;
  if (provider === 'gcp') return seed;
  return String(400_000_000 + (hash32(seed) % 90_000_000));
}

function tagsFor(vm: Pick<CloudNode, 'id' | 'cluster_id' | 'name'>): Record<string, string> {
  return { 'gpu-deck:managed': 'true', 'gpu-deck:cluster-id': vm.cluster_id, 'gpu-deck:cloud-node-id': vm.id, 'gpu-deck:node-name': vm.name };
}

/** The VM's agent joined: add a node (and its GPUs) to the fleet store. */
function join(rec: Rec, at: number): void {
  const vm = rec.vm;
  const nodeId = uuid(`node:cloud:${vm.id}`);
  const template = NODES[1];
  const node: Schemas['Node'] = {
    ...template,
    id: nodeId,
    cluster_id: vm.cluster_id,
    name: vm.name,
    hostname: `${vm.name}.${vm.region}.compute.internal`,
    ip_address: vm.private_ip ?? null,
    proxy_address: vm.private_ip ?? null,
    status: 'online',
    labels: { ...vm.labels, 'gpu-deck.io/cloud': vm.provider, 'gpu-deck.io/instance-type': vm.instance_type },
    cert_fingerprint: `SHA256:${hash32(nodeId).toString(16)}…`,
    cpu_cores: vm.vcpus ?? template.cpu_cores,
    memory_total_bytes: vm.memory_bytes ?? template.memory_total_bytes,
    last_heartbeat_at: iso(at),
    last_inventory_at: iso(at),
    connected_at: iso(at),
    price_plan_id: null,
    cloud_instance_id: vm.provider_instance_id ?? null,
    cloud_instance_type: vm.instance_type,
    metadata: { provider: vm.provider, region: vm.region, zone: vm.zone },
    device_count: vm.gpu_count,
    instance_count: 0,
    created_at: iso(at),
    updated_at: iso(at),
  };
  if (!fleetStore.nodes.some((n) => n.id === nodeId)) fleetStore.nodes.push(node);
  const tmpl = DEVICES[1];
  for (let index = 0; index < vm.gpu_count; index += 1) {
    const id = uuid(`device:cloud:${vm.id}:${index}`);
    if (fleetStore.devices.some((d) => d.id === id)) continue;
    fleetStore.devices.push({
      ...tmpl,
      id,
      node_id: nodeId,
      node_name: vm.name,
      cluster_id: vm.cluster_id,
      index,
      uuid: `GPU-${hash32(id).toString(16)}-cloud`,
      name: `NVIDIA ${vm.gpu_model ?? 'GPU'}`,
      vram_total_bytes: vm.gpu_vram_bytes ?? tmpl.vram_total_bytes,
      vram_reserved_bytes: 0,
      slices: [],
      instance_ids: [],
      labels: {},
      created_at: iso(at),
      updated_at: iso(at),
    });
  }
  Object.assign(vm, { node_id: nodeId, node_name: vm.name, node_status: 'online', joined_at: iso(at) });
}

function setNodeStatus(vm: CloudNode, status: Schemas['Node']['status']): void {
  const node = fleetStore.nodes.find((n) => n.id === vm.node_id);
  if (node) node.status = status;
  vm.node_status = vm.node_id ? status : null;
}

function enter(rec: Rec, status: CloudNode['status'], at: number, patch: Partial<CloudNode> = {}): void {
  Object.assign(rec.vm, patch, { status, updated_at: iso(at) });
  rec.enteredAt = at;
}

function advance(rec: Rec, now = Date.now()): CloudNode {
  const vm = rec.vm;
  for (let guard = 0; guard < 8; guard += 1) {
    const phase = PHASE[vm.status];
    if (!phase || now - rec.enteredAt < phase) break;
    const at = rec.enteredAt + phase;
    switch (vm.status) {
      case 'pending':
        enter(rec, 'provisioning', at, { status_message: 'Create call accepted by the provider' });
        break;
      case 'provisioning':
        if (rec.failWith) {
          enter(rec, 'error', at, { status_message: rec.failWith.message, last_error: { code: rec.failWith.code, message: rec.failWith.message, provider: vm.provider, provider_code: vm.provider === 'aws' ? 'VcpuLimitExceeded' : 'QuotaExceeded', stage: 'create', at: iso(at) } });
          break;
        }
        enter(rec, 'booting', at, {
          provider_instance_id: providerInstanceId(vm.provider, vm.name),
          provider_state: vm.provider === 'aws' ? 'running' : 'active',
          launched_at: iso(at),
          public_ip: `3.${hash32(vm.id) % 200}.${hash32(`b${vm.id}`) % 250}.${hash32(`c${vm.id}`) % 250}`,
          private_ip: `10.40.${hash32(vm.id) % 200}.${hash32(`d${vm.id}`) % 250}`,
          status_message: 'VM is up; cloud-init is installing the agent',
          join_token_id: newId('join'),
        });
        break;
      case 'booting':
        join(rec, at);
        enter(rec, 'running', at, { status_message: null });
        break;
      case 'draining':
        setNodeStatus(vm, 'drained');
        enter(rec, vm.pending_action === 'terminate' ? 'terminating' : 'stopping', at, { status_message: vm.pending_action === 'terminate' ? 'Terminating the VM' : 'Stopping the VM' });
        break;
      case 'stopping':
        setNodeStatus(vm, 'offline');
        enter(rec, 'stopped', at, { stopped_at: iso(at), pending_action: null, provider_state: 'stopped', status_message: null });
        break;
      case 'starting':
        setNodeStatus(vm, 'online');
        enter(rec, 'running', at, { stopped_at: null, provider_state: 'running', status_message: null });
        break;
      case 'terminating':
        setNodeStatus(vm, 'removed');
        enter(rec, 'terminated', at, { terminated_at: iso(at), pending_action: null, provider_state: 'terminated', public_ip: null, status_message: null });
        break;
      default:
        break;
    }
  }
  vm.last_polled_at = iso(now);
  return vm;
}

function seed(): Rec[] {
  const aws = fleetStore.clusters.find((c) => c.type === 'cloud_aws');
  const out: Rec[] = [];
  const g6e = TYPES.aws.find((x) => x.id === 'g6e.xlarge') as InstanceType;
  const l40s = TYPES.digitalocean[0];
  const h100 = TYPES.aws.find((x) => x.id === 'p5.48xlarge') as InstanceType;

  const running = base({ id: uuid('cloud:do-l40s-01'), cluster_id: DO_CLUSTER.id, provider: 'digitalocean', name: 'do-l40s-01', region: 'nyc2', instance_type: l40s.id, status: 'booting', auto_terminate_idle_minutes: 60, labels: { env: 'burst' }, image: 'gpu-h100x1-base', ...fromType(l40s), created_at: iso(NOW - 3 * HOUR - 5 * MIN) });
  running.tags = tagsFor(running);
  const runningRec: Rec = { vm: running, enteredAt: NOW - 3 * HOUR };
  Object.assign(running, { provider_instance_id: providerInstanceId('digitalocean', running.name), launched_at: iso(NOW - 3 * HOUR - 4 * MIN), public_ip: '159.203.88.17', private_ip: '10.116.0.7', provider_state: 'active' });
  join(runningRec, NOW - 3 * HOUR);
  enter(runningRec, 'running', NOW - 3 * HOUR);
  out.push(runningRec);

  if (aws) {
    const done = base({ id: uuid('cloud:eval-burst-1'), cluster_id: aws.id, provider: 'aws', name: 'eval-burst-1', region: 'ap-south-1', zone: 'ap-south-1a', instance_type: g6e.id, status: 'terminated', ...fromType(g6e), provider_instance_id: providerInstanceId('aws', 'eval-burst-1'), provider_state: 'terminated', launched_at: iso(NOW - 30 * HOUR), joined_at: iso(NOW - 30 * HOUR + 4 * MIN), terminated_at: iso(NOW - 22 * HOUR), auto_terminate_idle_minutes: 30, created_at: iso(NOW - 30 * HOUR - 2 * MIN), status_message: 'Idle for 30 min — auto-terminated after drain' });
    done.tags = tagsFor(done);
    out.push({ vm: done, enteredAt: NOW - 22 * HOUR });

    const quota = base({ id: uuid('cloud:eval-h100'), cluster_id: aws.id, provider: 'aws', name: 'eval-h100', region: 'ap-south-1', instance_type: h100.id, status: 'error', ...fromType(h100), created_at: iso(NOW - 26 * HOUR), status_message: 'You have requested more vCPU capacity than your current vCPU limit of 64 allows for the instance bucket that the specified instance type belongs to.', last_error: { code: 'CLOUD_QUOTA_EXCEEDED', message: 'vCPU limit of 64 for P instances in ap-south-1', provider: 'aws', provider_code: 'VcpuLimitExceeded', stage: 'create', at: iso(NOW - 26 * HOUR) } });
    quota.tags = tagsFor(quota);
    out.push({ vm: quota, enteredAt: NOW - 26 * HOUR });
  }
  return out;
}

const records: Rec[] = seed();

// ─── Handlers ───────────────────────────────────────────────────────────────

function account(req: MockRequest): { provider: Provider; ok: boolean; error?: ReturnType<typeof fail> } {
  const provider = req.params.provider as Provider;
  if (!PROVIDERS.some((p) => p.id === provider)) return { provider, ok: false, error: notFound('Cloud provider') };
  const clusterId = req.query.get('cluster_id');
  const credentialId = req.query.get('credential_id');
  if (!clusterId && !credentialId) return { provider, ok: false, error: fail(422, 'VALIDATION_ERROR', 'Pass cluster_id or credential_id') };
  const credential = credentialId ?? fleetStore.clusters.find((c) => c.id === clusterId)?.credential_id;
  const cred = fleetStore.credentials.find((c) => c.id === credential);
  if (!cred) return { provider, ok: false, error: fail(422, 'CLOUD_CREDENTIAL_INVALID', 'The cluster has no cloud credential') };
  if (cred.provider && cred.provider !== provider) return { provider, ok: false, error: fail(422, 'CLOUD_CREDENTIAL_INVALID', `The credential is for ${cred.provider}, not ${provider}`) };
  return { provider, ok: true };
}

function resolveType(provider: Provider, id: string): InstanceType | undefined {
  return TYPES[provider].find((x) => x.id === id);
}

function preview(body: Schemas['CloudNodeCreate']): { error?: ReturnType<typeof fail>; preview?: Schemas['CloudProvisionPreview']; cluster?: Schemas['Cluster']; type?: InstanceType } {
  const cluster = fleetStore.clusters.find((c) => c.id === body.cluster_id);
  const provider = providerOf(cluster);
  if (!cluster || !provider) return { error: fail(422, 'VALIDATION_ERROR', 'cluster_id must be a cloud cluster') };
  if (!cluster.credential_id) return { error: fail(422, 'CLOUD_CREDENTIAL_INVALID', `${cluster.name} has no cloud credential`) };
  const cfg = cloudConfig(cluster);
  const region = body.region ?? cluster.region ?? (cfg.region as string | undefined) ?? REGIONS[provider][0].id;
  const type = resolveType(provider, body.instance_type);
  if (!type || !type.regions.includes(region)) return { error: fail(422, 'CLOUD_INVALID_REQUEST', `${body.instance_type} is not offered in ${region}`) };
  const count = Math.max(1, body.count ?? 1);
  const maxNodes = Number(cfg.max_nodes ?? 10);
  const current = records.filter((r) => r.vm.cluster_id === cluster.id && !['terminated', 'error'].includes(r.vm.status)).length;
  if (current + count > maxNodes) return { error: fail(409, 'CLOUD_MAX_NODES', `${cluster.name} allows ${maxNodes} cloud node(s); ${current} exist`) };
  const prefix = (body.name ?? `${cluster.name}-${type.family ?? 'gpu'}`).toLowerCase().replace(/[^a-z0-9-]+/g, '-').slice(0, 50);
  const names = count === 1 ? [prefix] : Array.from({ length: count }, (_, i) => `${prefix}-${i + 1}`);
  const zone = body.zone ?? REGIONS[provider].find((r) => r.id === region)?.zones[0] ?? null;
  const price = type.price_per_hour_usd ?? null;
  return {
    cluster,
    type,
    preview: {
      cluster_id: cluster.id,
      provider,
      region,
      zone,
      instance_type: type,
      count,
      names,
      image: body.image ?? null,
      disk_gb: body.disk_gb ?? Number(cfg.disk_gb ?? 200),
      options: { ...cfg, ...(body.options ?? {}) },
      auto_terminate_idle_minutes: body.auto_terminate_idle_minutes ?? (cfg.auto_terminate_idle_minutes as number | undefined) ?? null,
      max_nodes: maxNodes,
      current_nodes: current,
      price_per_hour_usd: price,
      estimated_monthly_usd: price === null ? null : Math.round(price * 730 * count * 100) / 100,
      price_plan_suggestion: fromType(type).price_plan_suggestion ?? null,
    },
  };
}

function rec(req: MockRequest): Rec | undefined {
  return records.find((r) => r.vm.id === req.params.cloud_node_id);
}

registerMockHandlers('cloud', [
  { method: 'GET', path: '/cloud/providers', handle: () => ok(PROVIDERS, { total: PROVIDERS.length }) },
  {
    method: 'POST',
    path: '/cloud/providers/:provider/check-credential',
    handle: (req) => {
      const provider = req.params.provider as Provider;
      const body = bodyOf<Schemas['CloudCredentialCheckRequest']>(req);
      const cred = fleetStore.credentials.find((c) => c.id === body.credential_id);
      if (!cred) return fail(404, 'NOT_FOUND', 'Credential not found');
      if (cred.provider && cred.provider !== provider) return fail(422, 'CLOUD_CREDENTIAL_INVALID', `The credential is for ${cred.provider}, not ${provider}`);
      const accounts: Record<Provider, string> = { aws: 'arn:aws:iam::412398765210:user/gpu-deck', azure: 'subscription 7f3c…0b91 (Research ML)', gcp: 'project gpu-deck-lab (gpu-deck@gpu-deck-lab.iam)', digitalocean: 'ops@example.com (team Research)' };
      return ok({ ok: true, provider, account: accounts[provider], detail: 'Authenticated call succeeded (recorded response)' });
    },
  },
  {
    method: 'GET',
    path: '/cloud/providers/:provider/regions',
    handle: (req) => {
      const a = account(req);
      if (!a.ok) return a.error as ReturnType<typeof fail>;
      return ok(REGIONS[a.provider], { total: REGIONS[a.provider].length });
    },
  },
  {
    method: 'GET',
    path: '/cloud/providers/:provider/instance-types',
    handle: (req) => {
      const a = account(req);
      if (!a.ok) return a.error as ReturnType<typeof fail>;
      const region = req.query.get('region') ?? '';
      const vendor = req.query.get('gpu_vendor');
      const minVram = Number(req.query.get('min_vram_gb') ?? 0);
      const count = req.query.get('gpu_count');
      const maxPrice = req.query.get('max_price');
      const search = (req.query.get('search') ?? '').toLowerCase();
      const list = TYPES[a.provider].filter(
        (x) =>
          x.regions.includes(region) &&
          (!vendor || x.gpu_vendor === vendor) &&
          (!minVram || (x.total_vram_bytes ?? 0) >= minVram * GiB) &&
          (!count || x.gpu_count === Number(count)) &&
          (!maxPrice || (x.price_per_hour_usd !== null && x.price_per_hour_usd !== undefined && x.price_per_hour_usd <= Number(maxPrice))) &&
          (!search || `${x.id} ${x.gpu_model ?? ''} ${x.family ?? ''}`.toLowerCase().includes(search)),
      );
      return ok(list, { total: list.length });
    },
  },
  {
    method: 'GET',
    path: '/cloud/providers/:provider/quotas',
    handle: (req) => {
      const a = account(req);
      if (!a.ok) return a.error as ReturnType<typeof fail>;
      const region = req.query.get('region');
      const quotas: Record<Provider, Schemas['CloudQuota'][]> = {
        aws: [
          { name: 'Running On-Demand G and VT instances', used: null, limit: 64, unit: 'vCPU', region },
          { name: 'Running On-Demand P instances', used: null, limit: 64, unit: 'vCPU', region },
        ],
        azure: [
          { name: 'Standard NCADS_A100_v4 Family vCPUs', used: 0, limit: 48, unit: 'vCPU', region },
          { name: 'Total Regional vCPUs', used: 12, limit: 100, unit: 'vCPU', region },
        ],
        gcp: [
          { name: 'NVIDIA_L4_GPUS', used: 0, limit: 8, unit: 'GPU', region },
          { name: 'NVIDIA_A100_GPUS', used: 0, limit: 1, unit: 'GPU', region },
          { name: 'CPUS', used: 6, limit: 72, unit: 'vCPU', region },
        ],
        digitalocean: [{ name: 'Droplet limit', used: records.filter((r) => r.vm.provider === 'digitalocean' && r.vm.status !== 'terminated').length + 3, limit: 25, unit: 'count', region: null }],
      };
      return ok(quotas[a.provider], { total: quotas[a.provider].length });
    },
  },
  {
    method: 'GET',
    path: '/cloud/nodes',
    handle: (req) => {
      const now = Date.now();
      const clusterId = req.query.get('cluster_id');
      const provider = req.query.get('provider');
      const status = req.query.getAll('status');
      const search = (req.query.get('search') ?? '').toLowerCase();
      const list = records
        .map((r) => advance(r, now))
        .filter((vm) => (!clusterId || vm.cluster_id === clusterId) && (!provider || vm.provider === provider) && (!status.length || status.includes(vm.status)) && (!search || `${vm.name} ${vm.instance_type} ${vm.region}`.toLowerCase().includes(search)));
      return paginate(sortBy(list as unknown as Record<string, unknown>[], req.query.get('sort'), '-created_at'), req.query);
    },
  },
  {
    method: 'POST',
    path: '/cloud/nodes',
    handle: (req) => {
      const body = bodyOf<Schemas['CloudNodeCreate']>(req) as Schemas['CloudNodeCreate'];
      const result = preview(body);
      if (result.error) return result.error;
      const p = result.preview as Schemas['CloudProvisionPreview'];
      if (body.dry_run) return ok(p);
      const now = Date.now();
      const out = p.names.map((name, i) => {
        const vm = base({
          id: newId('cloud'),
          cluster_id: p.cluster_id,
          provider: p.provider,
          name,
          region: p.region,
          zone: p.zone ?? null,
          instance_type: p.instance_type.id,
          status: 'pending',
          image: p.image ?? null,
          disk_gb: p.disk_gb,
          labels: body.labels ?? {},
          options: p.options,
          auto_terminate_idle_minutes: p.auto_terminate_idle_minutes ?? null,
          ...fromType(p.instance_type),
          created_at: iso(now + i),
        });
        vm.tags = tagsFor(vm);
        // 8 × H100 in a region with a 64 vCPU P-instance quota fails like the real thing.
        const failWith = p.instance_type.id === 'p5.48xlarge' || p.instance_type.id === 'Standard_ND96isr_H100_v5' ? { code: 'CLOUD_QUOTA_EXCEEDED', message: 'vCPU quota for this GPU family is below what the instance type needs (192 vCPU requested, 64 allowed).' } : undefined;
        records.unshift({ vm, enteredAt: now, failWith });
        recordAudit({ eventType: 'cloud_node.created', resourceType: 'cloud_node', resourceId: vm.id, resourceName: vm.name, at: now, after: { provider: vm.provider, region: vm.region, instance_type: vm.instance_type } });
        return vm;
      });
      return created(out);
    },
  },
  {
    method: 'GET',
    path: '/cloud/nodes/:cloud_node_id',
    handle: (req) => {
      const r = rec(req);
      return r ? ok(advance(r)) : notFound('Cloud node');
    },
  },
  {
    method: 'PATCH',
    path: '/cloud/nodes/:cloud_node_id',
    handle: (req) => {
      const r = rec(req);
      if (!r) return notFound('Cloud node');
      const body = bodyOf<Schemas['CloudNodeUpdate']>(req);
      if (body.auto_terminate_idle_minutes !== undefined) r.vm.auto_terminate_idle_minutes = body.auto_terminate_idle_minutes;
      if (body.labels) r.vm.labels = body.labels;
      r.vm.updated_at = iso(Date.now());
      return ok(advance(r));
    },
  },
  {
    method: 'DELETE',
    path: '/cloud/nodes/:cloud_node_id',
    handle: (req) => {
      const idx = records.findIndex((x) => x.vm.id === req.params.cloud_node_id);
      if (idx < 0) return notFound('Cloud node');
      const vm = advance(records[idx]);
      if (vm.status !== 'terminated' && !(vm.status === 'error' && !vm.provider_instance_id)) return fail(409, 'CONFLICT', 'Only terminated records (or errors that never created a VM) can be removed');
      records.splice(idx, 1);
      return noContent();
    },
  },
  {
    method: 'POST',
    path: '/cloud/nodes/:cloud_node_id/stop',
    handle: (req) => {
      const r = rec(req);
      if (!r) return notFound('Cloud node');
      const vm = advance(r);
      if (!['running', 'booting', 'error'].includes(vm.status)) return fail(409, 'CONFLICT', `Cannot stop a VM that is ${vm.status}`);
      const force = Boolean(bodyOf<Schemas['CloudNodeAction']>(req).force);
      const now = Date.now();
      if (force || !vm.node_id) enter(r, 'stopping', now, { pending_action: 'stop', status_message: 'Stopping the VM (no drain)' });
      else {
        setNodeStatus(vm, 'draining');
        enter(r, 'draining', now, { pending_action: 'stop', drain_started_at: iso(now), status_message: 'Draining the node: instances move to other GPUs first' });
      }
      return ok(vm);
    },
  },
  {
    method: 'POST',
    path: '/cloud/nodes/:cloud_node_id/start',
    handle: (req) => {
      const r = rec(req);
      if (!r) return notFound('Cloud node');
      const vm = advance(r);
      if (vm.status !== 'stopped') return fail(409, 'CONFLICT', `Only a stopped VM can be started (it is ${vm.status})`);
      enter(r, 'starting', Date.now(), { status_message: 'Waiting for the agent to reconnect' });
      return ok(vm);
    },
  },
  {
    method: 'POST',
    path: '/cloud/nodes/:cloud_node_id/terminate',
    handle: (req) => {
      const r = rec(req);
      if (!r) return notFound('Cloud node');
      const vm = advance(r);
      if (vm.status === 'terminated' || vm.status === 'terminating') return fail(409, 'CONFLICT', 'Already terminating');
      const force = Boolean(bodyOf<Schemas['CloudNodeAction']>(req).force);
      const now = Date.now();
      if (!vm.provider_instance_id) enter(r, 'terminated', now, { terminated_at: iso(now), status_message: 'No VM was created; record closed' });
      else if (force || !vm.node_id || vm.node_status !== 'online') enter(r, 'terminating', now, { pending_action: 'terminate', status_message: 'Terminating the VM' });
      else {
        setNodeStatus(vm, 'draining');
        enter(r, 'draining', now, { pending_action: 'terminate', drain_started_at: iso(now), status_message: 'Draining the node before termination' });
      }
      recordAudit({ eventType: 'cloud_node.terminated', resourceType: 'cloud_node', resourceId: vm.id, resourceName: vm.name, at: now });
      return ok(vm);
    },
  },
  {
    method: 'POST',
    path: '/cloud/nodes/:cloud_node_id/refresh',
    handle: (req) => {
      const r = rec(req);
      if (!r) return notFound('Cloud node');
      // "Run one lifecycle step now": skip what is left of the current phase.
      const phase = PHASE[r.vm.status];
      if (phase) r.enteredAt = Math.min(r.enteredAt, Date.now() - phase);
      return ok(advance(r));
    },
  },
  {
    method: 'POST',
    path: '/cloud/nodes/:cloud_node_id/apply-price-plan',
    handle: (req) => {
      const r = rec(req);
      if (!r) return notFound('Cloud node');
      const vm = advance(r);
      if (!vm.price_plan_suggestion || !vm.node_id) return fail(409, 'CONFLICT', vm.node_id ? 'The provider has no list price for this type' : 'The VM has not joined yet');
      const s = vm.price_plan_suggestion;
      let plan = fleetStore.pricePlans.find((p) => p.name === s.name);
      if (!plan) {
        plan = { ...fleetStore.pricePlans[0], id: newId('price-plan'), name: s.name, description: `List price ${s.source ?? ''}`.trim(), is_default: false, usd_per_gpu_minute: s.usd_per_gpu_minute ?? 0, created_at: iso(Date.now()), updated_at: iso(Date.now()) };
        fleetStore.pricePlans.push(plan);
      }
      const node = fleetStore.nodes.find((n) => n.id === vm.node_id);
      if (node) node.price_plan_id = plan.id;
      return ok(vm);
    },
  },
]);
