'use client';

import { useMemo, useState } from 'react';
import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import { ArrowLeft, BadgeCheck, CloudUpload, Gauge, KeyRound, Loader2, MapPin, Server, ShieldAlert } from 'lucide-react';
import { Button, buttonVariants } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Switch } from '@/components/ui/switch';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { PageContainer } from '@/components/shared/PageContainer';
import { PageHeader } from '@/components/shared/PageHeader';
import { Field } from '@/components/shared/form-dialog';
import { KeyValueEditor, rowsToRecord, type KeyValueRow } from '@/components/shared/key-value-editor';
import { Segmented } from '@/components/automation/segmented';
import { InstanceTypeBrowser } from '@/components/cloud/instance-type-browser';
import { formatHourly, gpuSummary } from '@/components/cloud/meta';
import { useClusters, useCredentialOptions, useUpdateCluster } from '@/hooks/api/fleet';
import { CredentialPicker } from '@/components/vault/credential-picker';
import { credentialKindsForCluster, type ClusterType } from '@/types/fleet';
import { cloudErrorMessage, useCheckCloudCredential, useCloudProviders, useCloudQuotas, useCloudRegions, useCreateCloudNodes, usePreviewCloudNodes } from '@/hooks/use-cloud';
import { useDebounce } from '@/hooks/use-debounce';
import { cn } from '@/lib/utils';
import { CLOUD_PROVIDER_LABELS, providerForClusterType, type CloudInstanceType, type CloudNodeCreate, type CloudProviderId } from '@/types/cloud';

const PROVIDER_ORDER: CloudProviderId[] = ['aws', 'azure', 'gcp', 'digitalocean'];
const IDLE_OPTIONS = [0, 30, 60, 120, 240, 1440];

function Section({ step, icon: Icon, title, description, children, muted }: { step: number; icon: typeof Server; title: string; description: string; children: React.ReactNode; muted?: boolean }) {
  return (
    <Card className={cn('gap-0 rounded-md py-0 shadow-premium transition-opacity', muted && 'pointer-events-none opacity-50')}>
      <CardHeader className="border-b border-border px-5 py-4">
        <CardTitle className="flex items-center gap-2.5 text-sm font-semibold">
          <span className="flex size-6 items-center justify-center rounded-full border border-border font-mono text-[11px] text-muted-foreground">{step}</span>
          <Icon className="size-4 text-muted-foreground" />
          {title}
        </CardTitle>
        <CardDescription>{description}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4 px-5 py-5">{children}</CardContent>
    </Card>
  );
}

/** A cloud cluster without a credential: pick (or add) one and save it on the cluster. */
function AttachCredential({ clusterId, clusterType }: { clusterId: string; clusterType: ClusterType }) {
  const updateCluster = useUpdateCluster();
  return (
    <CredentialPicker
      value=""
      kinds={credentialKindsForCluster(clusterType)}
      disabled={updateCluster.isPending}
      onChange={(id) => {
        if (id) updateCluster.mutate({ id: clusterId, body: { credential_id: id } });
      }}
    />
  );
}

export function CloudProvision() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const providers = useCloudProviders();
  const clusters = useClusters({ page_size: 200 });
  const credentials = useCredentialOptions();
  const check = useCheckCloudCredential();
  const create = useCreateCloudNodes();

  const cloudClusters = useMemo(() => (clusters.data?.items ?? []).filter((c) => providerForClusterType(c.type) !== null), [clusters.data]);
  const preset = cloudClusters.find((c) => c.id === searchParams.get('cluster_id'));
  const [provider, setProvider] = useState<CloudProviderId>(providerForClusterType(preset?.type) ?? 'aws');
  const [clusterId, setClusterId] = useState(preset?.id ?? '');
  // Region / type picks belong to the account they were made with; switching
  // the cluster drops them without an effect.
  const [picks, setPicks] = useState<{ accountId: string; region: string; type: CloudInstanceType | null }>({ accountId: '', region: '', type: null });
  const [name, setName] = useState('');
  const [count, setCount] = useState(1);
  const [diskGb, setDiskGb] = useState('');
  const [idle, setIdle] = useState(60);
  const [labels, setLabels] = useState<KeyValueRow[]>([]);
  const [applyPlan, setApplyPlan] = useState(true);

  const providerClusters = cloudClusters.filter((c) => providerForClusterType(c.type) === provider);
  // Picking the provider picks its only cluster.
  const effectiveClusterId = clusterId && providerClusters.some((c) => c.id === clusterId) ? clusterId : providerClusters.length === 1 ? providerClusters[0].id : '';
  const cluster = providerClusters.find((c) => c.id === effectiveClusterId);
  const credential = (credentials.data ?? []).find((c) => c.id === cluster?.credential_id);
  const account = useMemo(() => (effectiveClusterId ? { cluster_id: effectiveClusterId } : {}), [effectiveClusterId]);
  const samePicks = picks.accountId === effectiveClusterId;
  const region = samePicks ? picks.region : '';
  const type = samePicks ? picks.type : null;
  const setRegion = (r: string) => setPicks({ accountId: effectiveClusterId, region: r, type: null });
  const setType = (t: CloudInstanceType | null) => setPicks((prev) => ({ accountId: effectiveClusterId, region: prev.accountId === effectiveClusterId ? prev.region : '', type: t }));
  // Provider lookups need the cluster's credential; until one is saved the API rightly refuses them.
  const ready = !!cluster?.credential_id;
  const regions = useCloudRegions(ready ? provider : null, account);
  const effectiveRegion = region || cluster?.region || '';
  const quotas = useCloudQuotas(ready && effectiveRegion ? provider : null, effectiveRegion, account);

  const body: CloudNodeCreate | null =
    cluster && type
      ? {
          cluster_id: cluster.id,
          instance_type: type.id,
          region: effectiveRegion || null,
          name: name.trim() || null,
          count,
          disk_gb: diskGb ? Number(diskGb) : null,
          labels: rowsToRecord(labels),
          auto_terminate_idle_minutes: idle || null,
          apply_price_plan: applyPlan,
          dry_run: true,
        }
      : null;
  const debouncedBody = useDebounce(body, 400);
  const preview = usePreviewCloudNodes(debouncedBody);
  const p = preview.data;
  const providerInfo = providers.data?.find((x) => x.id === provider);
  const nameValid = name === '' || /^[a-z0-9]([a-z0-9-]{0,48}[a-z0-9])?$/.test(name);

  function provision() {
    if (!body) return;
    create.mutate(body, { onSuccess: (nodes) => router.push(nodes.length === 1 ? `/cloud/${nodes[0].id}` : `/cloud?cluster_id=${body.cluster_id}`) });
  }

  return (
    <PageContainer className="max-w-6xl">
      <PageHeader
        title="Provision a cloud GPU VM"
        description="GPU Deck creates the VM from a GPU image; cloud-init downloads the agent from this control plane and joins with a one-time token. Everything is tagged gpu-deck:*."
        actions={
          <Button variant="outline" size="sm" className="rounded-sm" onClick={() => router.push('/cloud')}>
            <ArrowLeft className="size-4" />
            Cloud VMs
          </Button>
        }
      />

      <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,1fr)_330px]">
        <div className="space-y-6">
          <Section step={1} icon={KeyRound} title="Account" description="A cloud cluster holds the provider credential (vault) and launch defaults; new VMs join it.">
            <Segmented
              aria-label="Cloud provider"
              value={provider}
              onChange={(v) => {
                setProvider(v);
                setClusterId('');
                check.reset();
              }}
              options={PROVIDER_ORDER.map((id) => ({ value: id, label: CLOUD_PROVIDER_LABELS[id], disabled: providers.data ? !providers.data.find((x) => x.id === id)?.enabled : false }))}
            />
            {providerClusters.length === 0 ? (
              <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-dashed border-border px-4 py-3 text-[13px]">
                <span className="text-muted-foreground">No {CLOUD_PROVIDER_LABELS[provider]} cluster yet. Create one with a {providerInfo?.credential_type ?? 'cloud'} credential first.</span>
                <Link href={`/clusters/new?type=${providerInfo?.cluster_type ?? ''}`} className={cn(buttonVariants({ variant: 'outline', size: 'sm' }), 'rounded-sm')}>
                  New cluster
                </Link>
              </div>
            ) : (
              <div className="grid gap-4 sm:grid-cols-2">
                <Field label="Cluster">
                  <Select
                    value={effectiveClusterId || null}
                    onValueChange={(v) => {
                      setClusterId(String(v ?? ''));
                      check.reset();
                    }}
                  >
                    <SelectTrigger className="w-full">
                      <SelectValue placeholder="Pick a cloud cluster">{(v: unknown) => providerClusters.find((c) => c.id === v)?.name ?? 'Pick a cluster'}</SelectValue>
                    </SelectTrigger>
                    <SelectContent>
                      {providerClusters.map((c) => (
                        <SelectItem key={c.id} value={c.id}>
                          {c.name}
                          <span className="ml-1 text-muted-foreground">· {c.region ?? 'no default region'}{c.status === 'disabled' ? ' · disabled' : ''}</span>
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </Field>
                <Field label="Credential" hint={cluster && !cluster.credential_id ? 'This cluster has no credential yet — pick or add one; it is saved on the cluster.' : 'Stored in the vault; never sent to the browser.'}>
                  {cluster && !cluster.credential_id ? (
                    <AttachCredential clusterId={cluster.id} clusterType={cluster.type} />
                  ) : (
                  <div className="flex items-center gap-2">
                    <div className="flex h-10 min-w-0 flex-1 items-center gap-2 rounded-md border border-input bg-muted/30 px-3 text-sm">
                      <KeyRound className="size-3.5 shrink-0 text-muted-foreground" />
                      <span className="truncate">{credential?.name ?? (cluster?.credential_id ? 'credential' : '—')}</span>
                      {credential?.masked_value && <span className="truncate font-mono text-[11px] text-muted-foreground">{credential.masked_value}</span>}
                    </div>
                    <Button type="button" variant="outline" size="sm" disabled={!cluster?.credential_id || check.isPending} onClick={() => cluster?.credential_id && check.mutate({ provider, credentialId: cluster.credential_id })}>
                      {check.isPending ? <Loader2 className="size-3.5 animate-spin" /> : <BadgeCheck className="size-3.5" />}
                      Check
                    </Button>
                  </div>
                  )}
                  {check.data && (
                    <p className={cn('flex items-center gap-1.5 text-[11px]', check.data.ok ? 'text-success-dark dark:text-success-light' : 'text-error-dark dark:text-error-light')}>
                      {check.data.ok ? <BadgeCheck className="size-3.5" /> : <ShieldAlert className="size-3.5" />}
                      {check.data.ok ? `Authenticated as ${check.data.account ?? 'the account'}` : (check.data.detail ?? 'Rejected')}
                    </p>
                  )}
                </Field>
              </div>
            )}
          </Section>

          <Section step={2} icon={MapPin} title="Region" description="Regions that offer GPU VMs for this account; the cluster's region is the default." muted={!cluster}>
            {regions.isError ? (
              <p className="rounded-md bg-destructive/10 p-3 text-sm text-destructive">{cloudErrorMessage(regions.error)}</p>
            ) : (
              <div className="grid gap-4 sm:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)]">
                <Field label="Region">
                  <Select
                    value={effectiveRegion || null}
                    onValueChange={(v) => setRegion(String(v ?? ''))}
                  >
                    <SelectTrigger className="w-full">
                      <SelectValue placeholder={regions.isLoading ? 'Loading regions…' : 'Pick a region'}>
                        {(v: unknown) => {
                          const r = regions.data?.find((x) => x.id === v);
                          return r ? `${r.id} · ${r.name}` : String(v ?? 'Pick a region');
                        }}
                      </SelectValue>
                    </SelectTrigger>
                    <SelectContent>
                      {(regions.data ?? []).map((r) => (
                        <SelectItem key={r.id} value={r.id} disabled={!r.available}>
                          <span className="font-mono text-xs">{r.id}</span>
                          <span className="ml-1 text-muted-foreground">
                            · {r.name}
                            {r.zones.length ? ` · ${r.zones.length} zones` : ''}
                            {!r.available ? ' · unavailable' : ''}
                          </span>
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </Field>
                <div className="space-y-1.5">
                  <p className="text-[13px] font-medium">Quotas</p>
                  {quotas.data?.length ? (
                    <ul className="flex flex-wrap gap-1.5">
                      {quotas.data.map((q) => (
                        <li key={q.name} className="rounded-md border border-border bg-muted/40 px-2 py-1 text-[11px]" title={q.name}>
                          <span className="text-muted-foreground">{q.name.replace(/^Running On-Demand /, '')}: </span>
                          <span className="font-mono font-medium tabular-nums">
                            {q.used ?? '?'} / {q.limit ?? '∞'} {q.unit}
                          </span>
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="text-[11px] text-muted-foreground">{quotas.isLoading ? 'Loading…' : effectiveRegion ? 'No quota data from the provider.' : 'Pick a region.'}</p>
                  )}
                </div>
              </div>
            )}
          </Section>

          <Section step={3} icon={Gauge} title="GPU instance type" description="Pick by GPU, VRAM and price. Compare per GPU-hour across shapes." muted={!ready || !effectiveRegion}>
            {ready && effectiveRegion ? (
              <InstanceTypeBrowser provider={provider} region={effectiveRegion} account={account} value={type?.id ?? ''} onChange={setType} />
            ) : (
              <p className="text-sm text-muted-foreground">{cluster && !ready ? 'Add a credential to this cluster first.' : 'Pick an account and a region first.'}</p>
            )}
          </Section>

          <Section step={4} icon={Server} title="VMs" description="Names become node names. Idle VMs are drained and terminated automatically if you set a limit." muted={!type}>
            <div className="grid gap-4 sm:grid-cols-[minmax(0,1fr)_110px_130px]">
              <Field label="Name" hint={nameValid ? 'Optional — generated from the cluster and family. Several VMs get -1, -2…' : 'Lowercase letters, digits and dashes.'} htmlFor="vm-name">
                <Input id="vm-name" value={name} onChange={(e) => setName(e.target.value.toLowerCase())} placeholder={p?.names[0]?.replace(/-1$/, '') ?? 'burst-l40s'} className="font-mono text-sm" aria-invalid={!nameValid} />
              </Field>
              <Field label="Count" htmlFor="vm-count">
                <Input id="vm-count" type="number" min={1} max={Math.max(1, (p?.max_nodes ?? 10) - (p?.current_nodes ?? 0))} value={count} onChange={(e) => setCount(Math.max(1, Math.min(32, Number(e.target.value) || 1)))} className="font-mono" />
              </Field>
              <Field label="Disk (GB)" htmlFor="vm-disk">
                <Input id="vm-disk" type="number" min={30} value={diskGb} onChange={(e) => setDiskGb(e.target.value)} placeholder={String(p?.disk_gb ?? 200)} className="font-mono" />
              </Field>
            </div>
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Terminate when idle">
                <Select value={String(idle)} onValueChange={(v) => setIdle(Number(v))}>
                  <SelectTrigger className="w-full">
                    <SelectValue>{(v: unknown) => (Number(v) ? `After ${Number(v) >= 60 ? `${Number(v) / 60} h` : `${v} min`} with no model loaded` : 'Never')}</SelectValue>
                  </SelectTrigger>
                  <SelectContent>
                    {IDLE_OPTIONS.map((m) => (
                      <SelectItem key={m} value={String(m)}>
                        {m ? `After ${m >= 60 ? `${m / 60} h` : `${m} min`} with no model loaded` : 'Never'}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </Field>
              <label className="flex items-center justify-between gap-3 rounded-md border border-border px-3 py-2.5 text-xs">
                <span>
                  <span className="font-medium">Bill at the list price</span>
                  <span className="block text-[11px] text-muted-foreground">Attach a price plan from the provider price when the node joins.</span>
                </span>
                <Switch checked={applyPlan} onCheckedChange={setApplyPlan} disabled={type?.price_per_hour_usd === null || type?.price_per_hour_usd === undefined} aria-label="Apply list-price plan" />
              </label>
            </div>
            <Field label="Node labels" hint="Applied through the join token — use them in node selectors and rule targets.">
              <KeyValueEditor rows={labels} onChange={setLabels} keyPlaceholder="pool" valuePlaceholder="burst" addLabel="Add label" />
            </Field>
          </Section>
        </div>

        <div className="space-y-4 lg:sticky lg:top-6">
          <Card className="relative gap-0 overflow-hidden rounded-md py-0 shadow-premium">
            <div aria-hidden className="pointer-events-none absolute -right-16 -top-20 size-48 rounded-full bg-brand/10 blur-3xl" />
            <CardHeader className="relative border-b border-border px-5 py-4">
              <CardTitle className="text-sm font-semibold">Summary</CardTitle>
              <CardDescription>{p ? 'Resolved by the control plane (dry run).' : 'Pick a type to see the price.'}</CardDescription>
            </CardHeader>
            <CardContent className="relative space-y-3 px-5 py-4 text-[13px]">
              {[
                ['Provider', CLOUD_PROVIDER_LABELS[provider]],
                ['Cluster', cluster?.name ?? '—'],
                ['Region', p ? `${p.region}${p.zone ? ` / ${p.zone}` : ''}` : effectiveRegion || '—'],
                ['Type', type ? type.id : '—'],
                ['GPUs', type ? gpuSummary(type) : '—'],
              ].map(([k, v]) => (
                <div key={k} className="flex items-baseline justify-between gap-3">
                  <span className="text-muted-foreground">{k}</span>
                  <span className={cn('text-right font-medium', k === 'Type' && 'font-mono text-xs')}>{v}</span>
                </div>
              ))}
              <div className="rounded-md border border-border bg-muted/30 px-3 py-2.5">
                <div className="flex items-baseline justify-between">
                  <span className="text-xs text-muted-foreground">{count > 1 ? `${count} VMs` : 'Per hour'}</span>
                  <span className="font-mono text-lg font-semibold tabular-nums">{type?.price_per_hour_usd ? formatHourly(type.price_per_hour_usd * count) : '—'}</span>
                </div>
                <div className="flex items-baseline justify-between text-[11px] text-muted-foreground">
                  <span>≈ per month (730 h)</span>
                  <span className="font-mono tabular-nums">{p?.estimated_monthly_usd ? `$${Math.round(p.estimated_monthly_usd).toLocaleString()}` : type ? 'no list price' : '—'}</span>
                </div>
              </div>
              {p && (
                <div className="space-y-1 text-[11px] text-muted-foreground">
                  <p>
                    Cluster cap: <span className="font-medium text-foreground">{p.current_nodes + p.count}</span> of {p.max_nodes} cloud nodes after this.
                  </p>
                  <p className="truncate" title={p.names.join(', ')}>
                    Names: <span className="font-mono text-foreground">{p.names.join(', ')}</span>
                  </p>
                </div>
              )}
              {preview.isError && <p className="rounded-md bg-destructive/10 p-2.5 text-xs text-destructive">{cloudErrorMessage(preview.error)}</p>}
              <Button className="w-full" disabled={!body || !nameValid || preview.isError || create.isPending} onClick={provision}>
                {create.isPending ? <Loader2 className="size-4 animate-spin" /> : <CloudUpload className="size-4" />}
                Provision {count > 1 ? `${count} VMs` : 'VM'}
              </Button>
              <p className="text-[11px] text-muted-foreground">Billed by {CLOUD_PROVIDER_LABELS[provider]} from the moment the VM is created until it is terminated.</p>
            </CardContent>
          </Card>
        </div>
      </div>
    </PageContainer>
  );
}
