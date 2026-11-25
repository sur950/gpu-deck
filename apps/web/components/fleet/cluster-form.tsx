'use client';

import { useEffect, useMemo } from 'react';
import { useForm, useWatch } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import { Loader2, Save } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Form, FormControl, FormDescription, FormField, FormItem, FormLabel, FormMessage } from '@/components/ui/form';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { KeyValueEditor, recordToRows, rowsToRecord } from '@/components/shared/key-value-editor';
import { CLUSTER_TYPE_ICONS } from '@/components/fleet/cluster-type-badge';
import { CredentialPicker } from '@/components/vault/credential-picker';
import { useCredentialKinds, useCredentialOptionsAll } from '@/hooks/use-vault';
import { cn } from '@/lib/utils';
import { isCompatible } from '@/lib/vault/credential-form';
import {
  CLUSTER_TYPE_LABELS,
  CLUSTER_TYPE_OPTIONS,
  credentialKindsForCluster,
  isCloudCluster,
  type Cluster,
  type ClusterCreate,
  type ClusterType,
  type ClusterUpdate,
} from '@/types/fleet';

const TYPE_HINTS: Record<ClusterType, string> = {
  bare_metal: 'GPU servers you own; agents join with a token.',
  vm: 'GPU virtual machines on any hypervisor.',
  kubernetes: 'Agent runs as a DaemonSet on GPU nodes.',
  cloud_aws: 'EC2 GPU instances in one region.',
  cloud_azure: 'NC/ND-series VMs in one region.',
  cloud_gcp: 'GPU VMs on Compute Engine.',
  cloud_do: 'DigitalOcean GPU droplets.',
};

/** Where each cloud launch setting lives in `config.cloud` (read by the cloud provisioner). */
const CLOUD_KEYS: Partial<Record<ClusterType, { vpc?: string; subnet?: string }>> = {
  cloud_aws: { subnet: 'subnet_id' },
  cloud_azure: { subnet: 'subnet_id' },
  cloud_gcp: { vpc: 'network', subnet: 'subnetwork' },
  cloud_do: { vpc: 'vpc_uuid' },
};

const schema = z.object({
  name: z
    .string()
    .min(2, 'At least 2 characters')
    .max(64, 'At most 64 characters')
    .regex(/^[a-z0-9]([a-z0-9-]*[a-z0-9])?$/, 'Lowercase letters, digits and dashes only'),
  type: z.enum(['bare_metal', 'vm', 'kubernetes', 'cloud_aws', 'cloud_azure', 'cloud_gcp', 'cloud_do']),
  description: z.string().max(500, 'At most 500 characters'),
  region: z.string().max(64),
  credentialId: z.string(),
  egressAllowlist: z.string(),
  labels: z.array(z.object({ key: z.string(), value: z.string() })),
  namespace: z.string().max(63),
  vpc: z.string().max(128),
  subnet: z.string().max(512),
  instanceTypes: z.string().max(256),
  resourceGroup: z.string().max(90),
  sshKeyCredentialId: z.string(),
  proxySourceCidrs: z.string(),
  sshDefaultCidrs: z.string(),
});

export type ClusterFormValues = z.infer<typeof schema>;

function configString(config: Record<string, unknown> | undefined, key: string): string {
  const value = config?.[key];
  if (Array.isArray(value)) return value.join(', ');
  return typeof value === 'string' ? value : '';
}

/** `config.node_security` lists (signed into every node's security policy). */
function securityList(config: Record<string, unknown> | undefined, key: string): string {
  const sec = config?.node_security as Record<string, unknown> | undefined;
  const value = sec?.[key];
  return Array.isArray(value) ? value.join('\n') : '';
}

function lines(value: string): string[] {
  return value
    .split(/[\n,]/)
    .map((s) => s.trim())
    .filter(Boolean);
}

function toDefaults(cluster?: Cluster, presetType?: ClusterType): ClusterFormValues {
  return {
    name: cluster?.name ?? '',
    type: cluster?.type ?? presetType ?? 'bare_metal',
    description: cluster?.description ?? '',
    region: cluster?.region ?? '',
    credentialId: cluster?.credential_id ?? '',
    egressAllowlist: (cluster?.egress_allowlist ?? []).join('\n'),
    labels: recordToRows(cluster?.labels),
    namespace: configString(cluster?.config, 'namespace'),
    vpc: configString(cluster?.config, 'vpc'),
    subnet: configString(cluster?.config, 'subnet'),
    instanceTypes: configString(cluster?.config, 'instance_types'),
    resourceGroup: configString((cluster?.config?.cloud ?? undefined) as Record<string, unknown> | undefined, 'resource_group'),
    sshKeyCredentialId: configString(cluster?.config, 'ssh_key_credential_id'),
    proxySourceCidrs: securityList(cluster?.config, 'proxy_allowed_source_cidrs'),
    sshDefaultCidrs: securityList(cluster?.config, 'ssh_default_allowed_cidrs'),
  };
}

/**
 * Build the API payload. `ClusterUpdate` is a superset of `ClusterCreate` minus required fields.
 * Keys the form does not manage (e.g. `config.cloud.max_nodes` set via the API) survive an edit:
 * pass the stored config as `base`.
 */
export function toClusterPayload(values: ClusterFormValues, base: Record<string, unknown> = {}): ClusterCreate {
  const managed = ['namespace', 'kubeconfig_credential_id', 'vpc', 'subnet', 'instance_types', 'ssh_key_credential_id'];
  const config: Record<string, unknown> = Object.fromEntries(Object.entries(base).filter(([k]) => !managed.includes(k) && k !== 'cloud'));
  if (values.type === 'kubernetes') {
    if (values.namespace.trim()) config.namespace = values.namespace.trim();
    if (values.credentialId) config.kubeconfig_credential_id = values.credentialId;
  }
  if (isCloudCluster(values.type)) {
    const cloudKeys = CLOUD_KEYS[values.type] ?? {};
    const managedCloud = ['resource_group', ...Object.values(cloudKeys)];
    const baseCloud = (base.cloud && typeof base.cloud === 'object' ? base.cloud : {}) as Record<string, unknown>;
    const cloud: Record<string, unknown> = Object.fromEntries(Object.entries(baseCloud).filter(([k]) => !managedCloud.includes(k)));
    if (values.vpc.trim()) {
      config.vpc = values.vpc.trim();
      if (cloudKeys.vpc) cloud[cloudKeys.vpc] = values.vpc.trim();
    }
    if (values.subnet.trim()) {
      config.subnet = values.subnet.trim();
      if (cloudKeys.subnet) cloud[cloudKeys.subnet] = values.subnet.trim();
    }
    if (values.type === 'cloud_azure' && values.resourceGroup.trim()) cloud.resource_group = values.resourceGroup.trim();
    const types = values.instanceTypes
      .split(/[,\n]/)
      .map((s) => s.trim())
      .filter(Boolean);
    if (types.length > 0) config.instance_types = types;
    if (values.sshKeyCredentialId) config.ssh_key_credential_id = values.sshKeyCredentialId;
    if (Object.keys(cloud).length > 0) config.cloud = cloud;
  } else if (base.cloud !== undefined) {
    config.cloud = base.cloud;
  }
  const proxy = lines(values.proxySourceCidrs);
  const ssh = lines(values.sshDefaultCidrs);
  if (proxy.length || ssh.length) {
    config.node_security = { proxy_allowed_source_cidrs: proxy, ssh_default_allowed_cidrs: ssh };
  } else {
    delete config.node_security;
  }
  return {
    name: values.name.trim(),
    type: values.type,
    description: values.description.trim() || null,
    region: values.region.trim() || null,
    credential_id: values.credentialId || null,
    egress_allowlist: values.egressAllowlist
      .split(/[\n,]/)
      .map((s) => s.trim())
      .filter(Boolean),
    labels: rowsToRecord(values.labels),
    config,
  };
}

interface ClusterFormProps {
  cluster?: Cluster;
  presetType?: ClusterType;
  isPending: boolean;
  submitLabel?: string;
  onSubmit: (payload: ClusterCreate | ClusterUpdate) => void;
  onCancel: () => void;
}

export function ClusterForm({ cluster, presetType, isPending, submitLabel, onSubmit, onCancel }: ClusterFormProps) {
  const form = useForm<ClusterFormValues>({
    resolver: zodResolver(schema),
    defaultValues: toDefaults(cluster, presetType),
  });
  const type = useWatch({ control: form.control, name: 'type' });
  const accepted = useMemo(() => credentialKindsForCluster(type), [type]);
  const credentials = useCredentialOptionsAll();
  const { kinds, byId } = useCredentialKinds();

  // Switching the type invalidates a credential picked for another kind.
  useEffect(() => {
    const current = form.getValues('credentialId');
    const picked = credentials.data?.find((c) => c.id === current);
    if (current && picked && kinds.length && !isCompatible(picked, accepted, kinds)) {
      form.setValue('credentialId', '');
    }
  }, [credentials.data, accepted, kinds, form]);

  const cloud = isCloudCluster(type);
  const credentialLabel = accepted.map((k) => byId[k]?.label ?? k).join(' / ');

  return (
    <Form {...form}>
      <form onSubmit={form.handleSubmit((values) => onSubmit(toClusterPayload(values, (cluster?.config ?? {}) as Record<string, unknown>)))} className="space-y-6">
        <Card className="rounded-md shadow-premium">
          <CardHeader>
            <CardTitle>Basics</CardTitle>
            <CardDescription>Name and where the GPU machines live. The type decides how nodes join.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-5">
            <div className="grid gap-4 sm:grid-cols-2">
              <FormField
                control={form.control}
                name="name"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Name</FormLabel>
                    <FormControl>
                      <Input placeholder="prod-blr" autoComplete="off" className="font-mono text-sm" {...field} />
                    </FormControl>
                    <FormDescription>Used in the CLI and the API.</FormDescription>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="region"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Region</FormLabel>
                    <FormControl>
                      <Input placeholder={cloud ? 'ap-south-1' : 'blr-office'} autoComplete="off" {...field} />
                    </FormControl>
                    <FormDescription>Free text for on-prem; the provider region for cloud.</FormDescription>
                    <FormMessage />
                  </FormItem>
                )}
              />
            </div>

            <FormField
              control={form.control}
              name="type"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Type</FormLabel>
                  <FormControl>
                    <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4" role="radiogroup" aria-label="Cluster type">
                      {CLUSTER_TYPE_OPTIONS.map((opt) => {
                        const Icon = CLUSTER_TYPE_ICONS[opt.value];
                        const selected = field.value === opt.value;
                        return (
                          <button
                            key={opt.value}
                            type="button"
                            role="radio"
                            aria-checked={selected}
                            disabled={!!cluster}
                            onClick={() => field.onChange(opt.value)}
                            className={cn(
                              'flex items-start gap-3 rounded-lg border p-3 text-left transition-colors outline-none focus-visible:ring-2 focus-visible:ring-ring/30 disabled:cursor-not-allowed disabled:opacity-60',
                              selected
                                ? 'border-foreground bg-muted/40 ring-1 ring-foreground'
                                : 'border-border hover:border-foreground/30 hover:bg-muted/30',
                            )}
                          >
                            <span className="flex size-8 shrink-0 items-center justify-center rounded-md border border-border bg-background">
                              <Icon className="size-4 text-muted-foreground" />
                            </span>
                            <span className="min-w-0">
                              <span className="block text-[13px] font-medium">{opt.label}</span>
                              <span className="block text-[11px] leading-snug text-muted-foreground">{TYPE_HINTS[opt.value]}</span>
                            </span>
                          </button>
                        );
                      })}
                    </div>
                  </FormControl>
                  {cluster && <FormDescription>The type cannot change once nodes have joined.</FormDescription>}
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="description"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Description</FormLabel>
                  <FormControl>
                    <Textarea rows={2} placeholder="What runs here and who owns it" {...field} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
          </CardContent>
        </Card>

        <Card className="rounded-md shadow-premium">
          <CardHeader>
            <CardTitle>Access</CardTitle>
            <CardDescription>
              {type === 'kubernetes'
                ? 'The kubeconfig lives in the vault; GPU Deck only ever reads it from there.'
                : cloud
                  ? `${CLUSTER_TYPE_LABELS[type]} credentials come from the vault, encrypted at rest.`
                  : 'Optional SSH key from the vault for the install helper.'}
            </CardDescription>
          </CardHeader>
          <CardContent className="grid gap-4 sm:grid-cols-2">
            <FormField
              control={form.control}
              name="credentialId"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>{type === 'kubernetes' ? 'Kubeconfig credential' : cloud ? 'Cloud credential' : 'SSH credential'}</FormLabel>
                  <FormControl>
                    <CredentialPicker value={field.value} onChange={(id) => field.onChange(id)} kinds={accepted} allowNone={!cloud} noneLabel="None" />
                  </FormControl>
                  <FormDescription>
                    {cloud ? `Needed to browse GPU types and launch VMs. Only ${credentialLabel} credentials fit.` : `Optional. Only ${credentialLabel} credentials fit.`}
                  </FormDescription>
                  <FormMessage />
                </FormItem>
              )}
            />
          </CardContent>
        </Card>

        {(type === 'kubernetes' || cloud) && (
          <Card className="rounded-md shadow-premium">
            <CardHeader>
              <CardTitle>{type === 'kubernetes' ? 'Kubernetes' : `${CLUSTER_TYPE_LABELS[type]} settings`}</CardTitle>
              <CardDescription>
                {type === 'kubernetes'
                  ? 'Namespace for the gpudeck-agent DaemonSet.'
                  : 'Network placement and the instance types GPU Deck may launch.'}
              </CardDescription>
            </CardHeader>
            <CardContent className="grid gap-4 sm:grid-cols-2">
              {type === 'kubernetes' && (
                <FormField
                  control={form.control}
                  name="namespace"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Namespace</FormLabel>
                      <FormControl>
                        <Input placeholder="gpudeck-system" className="font-mono text-sm" {...field} />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
              )}
              {cloud && (
                <>
                  <FormField
                    control={form.control}
                    name="vpc"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>VPC / network</FormLabel>
                        <FormControl>
                          <Input placeholder="vpc-0a1b2c3d" className="font-mono text-sm" {...field} />
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                  <FormField
                    control={form.control}
                    name="subnet"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Subnet</FormLabel>
                        <FormControl>
                          <Input placeholder="subnet-0f9e8d7c" className="font-mono text-sm" {...field} />
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                  <FormField
                    control={form.control}
                    name="instanceTypes"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Instance types</FormLabel>
                        <FormControl>
                          <Input placeholder="g6e.xlarge, p5.48xlarge" className="font-mono text-sm" {...field} />
                        </FormControl>
                        <FormDescription>Comma separated, in order of preference.</FormDescription>
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                  {type === 'cloud_azure' && (
                    <FormField
                      control={form.control}
                      name="resourceGroup"
                      render={({ field }) => (
                        <FormItem>
                          <FormLabel>Resource group</FormLabel>
                          <FormControl>
                            <Input placeholder="gpu-deck-rg" className="font-mono text-sm" {...field} />
                          </FormControl>
                          <FormDescription>Azure needs it (and the subnet resource id) to launch VMs.</FormDescription>
                          <FormMessage />
                        </FormItem>
                      )}
                    />
                  )}
                  <FormField
                    control={form.control}
                    name="sshKeyCredentialId"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>SSH key</FormLabel>
                        <FormControl>
                          <CredentialPicker value={field.value} onChange={(id) => field.onChange(id)} kinds={['ssh_key']} allowNone placeholder="Public key for VM login…" />
                        </FormControl>
                        <FormDescription>{type === 'cloud_azure' || type === 'cloud_gcp' ? 'Added to new VMs for the admin user.' : 'Stored on the cluster; AWS and DigitalOcean use their own key pairs (launch options).'}</FormDescription>
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                </>
              )}
            </CardContent>
          </Card>
        )}

        <Card className="rounded-md shadow-premium">
          <CardHeader>
            <CardTitle>Labels and egress</CardTitle>
            <CardDescription>Labels drive placement and rule targets. The egress allowlist is pushed to every agent in the cluster.</CardDescription>
          </CardHeader>
          <CardContent className="grid gap-5 lg:grid-cols-2">
            <FormField
              control={form.control}
              name="labels"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Labels</FormLabel>
                  <FormControl>
                    <KeyValueEditor rows={field.value} onChange={field.onChange} keyPlaceholder="env" valuePlaceholder="prod" addLabel="Add label" />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name="egressAllowlist"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Egress allowlist</FormLabel>
                  <FormControl>
                    <Textarea rows={5} placeholder={'huggingface.co\ncdn-lfs.huggingface.co\nmirror.internal:443'} className="font-mono" {...field} />
                  </FormControl>
                  <FormDescription>One host per line. The control plane and model sources are always allowed.</FormDescription>
                  <FormMessage />
                </FormItem>
              )}
            />
          </CardContent>
        </Card>

        <Card className="rounded-md shadow-premium">
          <CardHeader>
            <CardTitle>Node network access</CardTitle>
            <CardDescription>
              Signed into every node&apos;s security policy. Nodes in the default outbound-tunnel mode open no inbound port; these lists only matter for direct-mode nodes and SSH GPU instances.
            </CardDescription>
          </CardHeader>
          <CardContent className="grid gap-5 lg:grid-cols-2">
            <FormField
              control={form.control}
              name="proxySourceCidrs"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Direct mode: API layer source addresses</FormLabel>
                  <FormControl>
                    <Textarea rows={4} placeholder={'10.20.0.15/32\n10.20.0.16/32'} className="font-mono" {...field} />
                  </FormControl>
                  <FormDescription>One CIDR per line. Only these may reach a direct-mode node&apos;s :9443 (closed before TLS otherwise). Empty: private ranges only.</FormDescription>
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name="sshDefaultCidrs"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>SSH GPU instances: default source CIDRs</FormLabel>
                  <FormControl>
                    <Textarea rows={4} placeholder={'192.168.10.0/24'} className="font-mono" {...field} />
                  </FormControl>
                  <FormDescription>For instances started without their own CIDRs. Empty: such instances accept nobody.</FormDescription>
                  <FormMessage />
                </FormItem>
              )}
            />
          </CardContent>
        </Card>

        {form.formState.errors.root && (
          <p className="rounded-md bg-destructive/10 p-3 text-sm text-destructive">{form.formState.errors.root.message}</p>
        )}

        <div className="flex items-center justify-end gap-3">
          <Button type="button" variant="outline" size="sm" className="rounded-sm" onClick={onCancel} disabled={isPending}>
            Cancel
          </Button>
          <Button type="submit" size="sm" className="rounded-sm" disabled={isPending}>
            {isPending ? <Loader2 className="size-4 animate-spin" /> : <Save className="size-4" />}
            {submitLabel ?? (cluster ? 'Save changes' : 'Create cluster')}
          </Button>
        </div>
      </form>
    </Form>
  );
}
