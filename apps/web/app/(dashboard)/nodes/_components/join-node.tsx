'use client';

import { useMemo, useState } from 'react';
import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import { ArrowLeft, ArrowRight, CheckCircle2, Download, Eye, EyeOff, KeyRound, Loader2, PackageOpen, RefreshCw, ShieldCheck, Sparkles, X } from 'lucide-react';
import { Button, buttonVariants } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { PageContainer } from '@/components/shared/PageContainer';
import { PageHeader } from '@/components/shared/PageHeader';
import { Field } from '@/components/shared/form-dialog';
import { CopyButton } from '@/components/shared/copy-button';
import { CodeBlock } from '@/components/shared/code-block';
import { StatusBadge, type BadgeVariant } from '@/components/shared/StatusBadge';
import { KeyValueEditor, rowsToRecord, type KeyValueRow } from '@/components/shared/key-value-editor';
import { LabelChips } from '@/components/shared/label-chips';
import { ExpiryCountdown, useNow, useRemaining } from '@/components/fleet/expiry-countdown';
import { InstallCommands, joinOptionFlags, withInstallOptions, type ServingMode } from '@/components/fleet/install-commands';
import { Checkbox } from '@/components/ui/checkbox';
import { useAgentBuilds, useClusters, useCreateJoinToken, useJoinTokens, useNode, useRevokeJoinToken } from '@/hooks/api/fleet';
import { agentDownloadUrl } from '@/lib/api/fleet';
import { EmptyState } from '@/components/shared/EmptyState';
import { useSystemInfo } from '@/hooks/api/models';
import { cn } from '@/lib/utils';
import { formatBytes, formatRelativeTime } from '@/utils/format';
import type { AgentBuild, JoinToken, JoinTokenCreated } from '@/types/fleet';

const TTL_OPTIONS = [
  { value: 5, label: '5 minutes' },
  { value: 15, label: '15 minutes (default)' },
  { value: 30, label: '30 minutes' },
  { value: 60, label: '1 hour' },
  { value: 240, label: '4 hours' },
  { value: 1440, label: '24 hours (max)' },
];

/** Multi-use enrolment tokens (DaemonSets, node pools) may live up to a year. */
const MULTI_TTL_OPTIONS = [
  { value: 60, label: '1 hour' },
  { value: 1440, label: '1 day' },
  { value: 10080, label: '7 days (default)' },
  { value: 43200, label: '30 days' },
  { value: 129600, label: '90 days' },
  { value: 525600, label: '1 year (max)' },
];

const USES_OPTIONS = [
  { value: 1, label: 'One machine (one-time token)' },
  { value: 5, label: 'Up to 5 machines' },
  { value: 25, label: 'Up to 25 machines' },
  { value: 100, label: 'Up to 100 machines' },
  { value: 1000, label: 'Up to 1000 machines' },
];

/** Whether a token can still enrol a machine (not revoked, not used up, not expired). */
function isUsable(t: JoinToken, now: number): boolean {
  return !t.revoked_at && !t.used_at && Date.parse(t.expires_at) > now;
}

function tokenState(t: JoinToken, now: number): { label: string; variant: BadgeVariant } {
  const multi = (t.max_uses ?? 1) > 1;
  if (t.revoked_at) return { label: 'Revoked', variant: 'revoked' };
  if (t.used_at) return { label: multi ? `Used up · ${t.use_count}/${t.max_uses}` : 'Used', variant: 'success' };
  if (Date.parse(t.expires_at) <= now) return { label: 'Expired', variant: 'expired' };
  if (multi) return { label: `Active · ${t.use_count ?? 0}/${t.max_uses}`, variant: 'info' };
  return { label: 'Waiting', variant: 'info' };
}

function hubFromCommand(command: string | undefined, fallback: string | undefined): string {
  const fromCommand = command ? /--hub\s+(\S+)/.exec(command)?.[1] : undefined;
  if (fromCommand) return fromCommand;
  if (fallback) return fallback.replace(/^wss:/, 'https:').replace(/^ws:/, 'http:');
  return 'https://<control-plane>:8443';
}

/** Watches the issued token until an agent uses it, then links to the new node. */
function JoinWatcher({ issued }: { issued: JoinTokenCreated }) {
  const tokens = useJoinTokens({ cluster_id: issued.cluster_id, page_size: 50 });
  const current = tokens.data?.items.find((t) => t.id === issued.id) ?? issued;
  const node = useNode(current.used_by_node_id ?? '');
  const remaining = useRemaining(current.expires_at);

  if (current.revoked_at) {
    return <Banner tone="error" title="Token revoked" body="Issue a new token to join this machine." />;
  }
  if ((current.max_uses ?? 1) > 1 && !current.used_at && remaining > 0) {
    return (
      <div className="flex items-center gap-3 rounded-lg border border-border bg-muted/30 px-4 py-3">
        <span className="relative flex size-2.5">
          <span className="absolute inline-flex size-full animate-ping rounded-full bg-info opacity-60" />
          <span className="relative inline-flex size-2.5 rounded-full bg-info" />
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium">
            {current.use_count ?? 0} of {current.max_uses} machines joined
          </p>
          <p className="text-xs text-muted-foreground">Every machine that runs the command joins with its own certificate; revoke the token when the rollout is done.</p>
        </div>
        {current.used_by_node_id && (
          <Link href={`/nodes/${current.used_by_node_id}`} className={cn(buttonVariants({ size: 'sm', variant: 'outline' }), 'rounded-sm')}>
            Latest node
            <ArrowRight className="size-4" />
          </Link>
        )}
      </div>
    );
  }
  if (current.used_at && current.used_by_node_id) {
    const n = node.data;
    return (
      <div className="flex items-center gap-4 rounded-lg border border-success/30 bg-success/10 px-4 py-3">
        <CheckCircle2 className="size-5 shrink-0 text-success" />
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold">{n ? `${n.name} joined the fleet` : 'An agent joined with this token'}</p>
          <p className="text-xs text-muted-foreground">
            {n ? (n.status === 'pending' ? 'Certificate issued — waiting for the first inventory report…' : `Online · ${n.device_count ?? 0} GPU(s) reported`) : 'Loading the node…'}
          </p>
        </div>
        <Link href={`/nodes/${current.used_by_node_id}`} className={cn(buttonVariants({ size: 'sm' }), 'rounded-sm')}>
          Open node
          <ArrowRight className="size-4" />
        </Link>
      </div>
    );
  }
  if (remaining <= 0) {
    return <Banner tone="warning" title="Token expired" body="It was never used. Issue a new one when the machine is ready." />;
  }
  return (
    <div className="flex items-center gap-3 rounded-lg border border-border bg-muted/30 px-4 py-3">
      <span className="relative flex size-2.5">
        <span className="absolute inline-flex size-full animate-ping rounded-full bg-info opacity-60" />
        <span className="relative inline-flex size-2.5 rounded-full bg-info" />
      </span>
      <div>
        <p className="text-sm font-medium">Waiting for the agent to join…</p>
        <p className="text-xs text-muted-foreground">This page updates as soon as the hub signs the node&apos;s certificate.</p>
      </div>
    </div>
  );
}

function Banner({ tone, title, body }: { tone: 'error' | 'warning'; title: string; body: string }) {
  return (
    <div className={cn('rounded-lg border px-4 py-3', tone === 'error' ? 'border-error/30 bg-error/10' : 'border-warning/30 bg-warning/10')}>
      <p className="text-sm font-semibold">{title}</p>
      <p className="text-xs text-muted-foreground">{body}</p>
    </div>
  );
}

const OS_LABEL: Record<string, string> = { linux: 'Linux', macos: 'macOS' };

function buildLabel(b: AgentBuild): string {
  const os = b.os ? (OS_LABEL[b.os] ?? b.os) : b.target.includes('apple') ? 'macOS' : b.target.includes('linux') ? 'Linux' : b.target;
  const arch = b.arch ?? (b.target.startsWith('aarch64') ? 'aarch64' : b.target.startsWith('x86_64') ? 'x86_64' : '');
  return `${os}${arch ? ` · ${arch === 'aarch64' && os === 'macOS' ? 'Apple Silicon' : arch}` : ''}`;
}

/** Agent binaries served by this control plane (GET /agent/downloads), with sha256 to verify. */
function AgentBuildsCard() {
  const { data, isLoading, isError } = useAgentBuilds();
  const builds = data?.builds ?? [];
  const latest = data?.latestVersion;
  return (
    <Card className="rounded-md shadow-premium">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          Agent downloads
          {latest && <span className="rounded-full border border-border px-2 py-0.5 font-mono text-[11px] font-medium text-muted-foreground">v{latest}</span>}
        </CardTitle>
        <CardDescription>
          Builds the agent hub serves to GPU machines: local ones (AGENT_DIST_DIR) and the GitHub release of this version, fetched and cached on first download. Verify the sha256 before installing.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <p className="py-6 text-center text-sm text-muted-foreground">Loading builds…</p>
        ) : isError || builds.length === 0 ? (
          <EmptyState
            icon={PackageOpen}
            title={isError ? 'Could not list agent builds' : 'No agent builds published'}
            description={isError ? 'The control plane did not answer.' : 'The release is not reachable and AGENT_DIST_DIR is empty: copy the release files there for offline installs.'}
          />
        ) : (
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Platform</TableHead>
                  <TableHead>Version</TableHead>
                  <TableHead className="text-right">Size</TableHead>
                  <TableHead>sha256</TableHead>
                  <TableHead className="w-28" />
                </TableRow>
              </TableHeader>
              <TableBody>
                {builds.map((b) => (
                  <TableRow key={`${b.version}-${b.target}`}>
                    <TableCell>
                      <span className="block text-[13px] font-medium">{buildLabel(b)}</span>
                      <span className="block font-mono text-[11px] text-muted-foreground">{b.target}</span>
                    </TableCell>
                    <TableCell className="font-mono text-xs">{b.version}</TableCell>
                    <TableCell className="text-right font-mono text-xs tabular-nums">
                      {b.source === 'release' ? <span className="text-muted-foreground" title="Downloaded from the release on first use">release</span> : formatBytes(b.size_bytes)}
                    </TableCell>
                    <TableCell>
                      <span className="inline-flex items-center gap-1">
                        <span className="max-w-40 truncate font-mono text-[11px] text-muted-foreground" title={b.sha256}>
                          {b.sha256}
                        </span>
                        <CopyButton value={b.sha256} successText="sha256 copied" />
                      </span>
                    </TableCell>
                    <TableCell className="text-right">
                      <a href={agentDownloadUrl(b)} download={b.filename} className={cn(buttonVariants({ variant: 'outline', size: 'xs' }))}>
                        <Download className="size-3" />
                        Download
                      </a>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

export function JoinNode() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const clusters = useClusters({ page_size: 100 });
  const systemInfo = useSystemInfo();
  const create = useCreateJoinToken();
  const revoke = useRevokeJoinToken();
  const recent = useJoinTokens({ page_size: 8 });
  const agentBuilds = useAgentBuilds();

  const presetCluster = searchParams.get('cluster_id') ?? '';
  const [clusterId, setClusterId] = useState(presetCluster);
  const [name, setName] = useState('');
  const [labels, setLabels] = useState<KeyValueRow[]>([]);
  const [ttl, setTtl] = useState(15);
  const [maxUses, setMaxUses] = useState(1);
  const multiUse = maxUses > 1;
  const ttlOptions = multiUse ? MULTI_TTL_OPTIONS : TTL_OPTIONS;
  const [issued, setIssued] = useState<JoinTokenCreated | null>(null);
  const [reveal, setReveal] = useState(false);
  // Network exposure of the GPU machine: outbound tunnel (no inbound port) by default.
  const [servingMode, setServingMode] = useState<ServingMode>('tunnel');
  const [firewall, setFirewall] = useState(true);

  const clusterList = useMemo(() => (clusters.data?.items ?? []).filter((c) => c.status === 'active'), [clusters.data]);
  const effectiveCluster = clusterId || (clusterList.length === 1 ? clusterList[0].id : '');
  const cluster = clusters.data?.items.find((c) => c.id === (issued?.cluster_id ?? effectiveCluster));
  const nameValid = name === '' || /^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$/.test(name);
  const now = useNow(5000);

  function issue() {
    create.mutate(
      { cluster_id: effectiveCluster, node_name_hint: multiUse ? null : name || null, labels: rowsToRecord(labels), ttl_minutes: ttl, max_uses: maxUses },
      {
        onSuccess: (token) => {
          setIssued(token);
          setReveal(false);
        },
      },
    );
  }

  const ctx = issued
    ? {
        hubUrl: issued.hub_url ?? hubFromCommand(issued.join_command, systemInfo.data?.agent_hub_url),
        token: issued.token,
        caFingerprint: issued.ca_fingerprint_sha256 ?? null,
        nodeName: issued.node_name_hint ?? null,
        labels: (issued.labels as Record<string, string>) ?? {},
        downloadBase: agentBuilds.data?.baseUrl ?? `${issued.hub_url ?? hubFromCommand(issued.join_command, systemInfo.data?.agent_hub_url)}/agent/v1`,
        caPemSha256: issued.ca_pem_sha256 ?? null,
        version: agentBuilds.data?.latestVersion ?? null,
        multiUse: (issued.max_uses ?? 1) > 1,
        servingMode,
        firewall,
      }
    : null;
  const joinCommand = issued ? [issued.join_command, ...joinOptionFlags(servingMode, firewall)].join(' ') : '';

  return (
    <PageContainer className="max-w-6xl">
      <PageHeader
        title="Add a node"
        description="Issue a join token, run the agent on the GPU machine, and it appears here with its own certificate. GPU machines need no inbound port: outbound access to the agent hub (8443) and the API layer tunnel (8444) only."
        actions={
          <Button variant="outline" size="sm" className="rounded-sm" onClick={() => router.push('/nodes')}>
            <ArrowLeft className="size-4" />
            Back to nodes
          </Button>
        }
      />

      <ol className="grid gap-3 sm:grid-cols-3" aria-label="How joining works">
        {[
          { n: 1, title: 'Issue a token', body: 'One-time or multi-use (node pools), expires, stored only as a hash.' },
          { n: 2, title: 'Run the agent', body: 'It generates a key, sends a CSR and receives a certificate signed by the internal CA.' },
          { n: 3, title: 'Agent dials out', body: 'mTLS WebSocket to :8443 — the machine needs no inbound port for control.' },
        ].map((s) => (
          <li key={s.n} className={cn('flex gap-3 rounded-lg border bg-card p-3', (issued ? s.n <= 2 : s.n === 1) ? 'border-foreground/25' : 'border-border')}>
            <span className={cn('flex size-6 shrink-0 items-center justify-center rounded-full border text-[11px] font-semibold', issued && s.n === 1 ? 'border-foreground bg-foreground text-background' : 'border-border')}>
              {issued && s.n === 1 ? '✓' : s.n}
            </span>
            <span>
              <span className="block text-[13px] font-medium">{s.title}</span>
              <span className="block text-[11px] leading-snug text-muted-foreground">{s.body}</span>
            </span>
          </li>
        ))}
      </ol>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,380px)_minmax(0,1fr)]">
        <Card className="h-fit rounded-md shadow-premium">
          <CardHeader>
            <CardTitle>Token settings</CardTitle>
            <CardDescription>The node joins this cluster. Name and labels are applied on join; the agent can still override labels in agent.toml.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <Field label="Cluster">
              <Select value={effectiveCluster || null} onValueChange={(v) => setClusterId(String(v ?? ''))} disabled={!!issued}>
                <SelectTrigger className="w-full">
                  <SelectValue placeholder={clusters.isLoading ? 'Loading clusters…' : 'Pick a cluster'}>{(v: unknown) => clusterList.find((c) => c.id === v)?.name ?? 'Pick a cluster'}</SelectValue>
                </SelectTrigger>
                <SelectContent>
                  {clusterList.map((c) => (
                    <SelectItem key={c.id} value={c.id}>
                      {c.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {!clusters.isLoading && clusterList.length === 0 && (
                <p className="text-[11px] text-muted-foreground">
                  No active cluster. <Link href="/clusters/new" className="underline underline-offset-2">Create one first</Link>.
                </p>
              )}
            </Field>
            <Field label="Machines" hint={multiUse ? 'Multi-use enrolment token: for Kubernetes DaemonSets and node pools. Each machine joins once, under its hostname.' : undefined}>
              <Select
                value={String(maxUses)}
                onValueChange={(v) => {
                  const next = Number(v);
                  setMaxUses(next);
                  setTtl(next > 1 ? 10080 : 15);
                }}
                disabled={!!issued}
              >
                <SelectTrigger className="w-full">
                  <SelectValue>{(v: unknown) => USES_OPTIONS.find((o) => String(o.value) === v)?.label}</SelectValue>
                </SelectTrigger>
                <SelectContent>
                  {USES_OPTIONS.map((o) => (
                    <SelectItem key={o.value} value={String(o.value)}>
                      {o.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
            <Field label="Node name" hint={multiUse ? 'Each machine uses its own hostname.' : nameValid ? 'Optional — defaults to the machine hostname. Must be unique in the cluster.' : 'Lowercase letters, digits, dots and dashes.'} htmlFor="join-name">
              <Input id="join-name" value={multiUse ? '' : name} onChange={(e) => setName(e.target.value.toLowerCase())} placeholder={multiUse ? 'hostname of each machine' : 'gpu-box-02'} className="font-mono text-sm" disabled={!!issued || multiUse} aria-invalid={!nameValid} />
            </Field>
            <Field label="Labels" hint="Used by placement (node selectors) and rule targets.">
              {issued ? <LabelChips labels={(issued.labels as Record<string, string>) ?? {}} max={10} emptyText="No labels" /> : <KeyValueEditor rows={labels} onChange={setLabels} keyPlaceholder="zone" valuePlaceholder="rack-2" addLabel="Add label" />}
            </Field>
            <Field label="Expires after">
              <Select value={String(ttl)} onValueChange={(v) => setTtl(Number(v))} disabled={!!issued}>
                <SelectTrigger className="w-full">
                  <SelectValue>{(v: unknown) => ttlOptions.find((o) => String(o.value) === v)?.label}</SelectValue>
                </SelectTrigger>
                <SelectContent>
                  {ttlOptions.map((o) => (
                    <SelectItem key={o.value} value={String(o.value)}>
                      {o.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
            {issued ? (
              <Button variant="outline" size="sm" className="w-full rounded-sm" onClick={() => { setIssued(null); setName(''); setLabels([]); setMaxUses(1); setTtl(15); setServingMode('tunnel'); setFirewall(true); }}>
                <RefreshCw className="size-4" />
                Join another machine
              </Button>
            ) : (
              <Button size="sm" className="w-full rounded-sm" disabled={!effectiveCluster || !nameValid || create.isPending} onClick={issue}>
                {create.isPending ? <Loader2 className="size-4 animate-spin" /> : <KeyRound className="size-4" />}
                Generate join token
              </Button>
            )}
          </CardContent>
        </Card>

        {issued && ctx ? (
          <div className="space-y-6">
            <Card className="relative overflow-hidden rounded-md shadow-premium">
              <div aria-hidden className="pointer-events-none absolute -right-16 -top-20 size-56 rounded-full bg-brand/10 blur-3xl" />
              <CardContent className="relative flex flex-col gap-5 sm:flex-row sm:items-center">
                <ExpiryCountdown createdAt={issued.created_at} expiresAt={issued.expires_at} />
                <div className="min-w-0 flex-1 space-y-3">
                  <div>
                    <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
                      {(issued.max_uses ?? 1) > 1 ? `Enrolment token · up to ${issued.max_uses} machines` : 'One-time join token'} · {cluster?.name}
                    </p>
                    <p className="mt-0.5 text-[11px] text-muted-foreground">
                      {(issued.max_uses ?? 1) > 1
                        ? 'Copy it now — it is never shown again. Anyone with it can join machines until it expires, is used up or is revoked.'
                        : 'Copy it now — it is never shown again. Anyone with it can join a machine until it expires or is used.'}
                    </p>
                  </div>
                  <div className="flex items-center gap-2 rounded-lg border border-border bg-muted/40 px-3 py-2">
                    <code className="min-w-0 flex-1 truncate font-mono text-[13px]">{reveal ? issued.token : `${issued.token.slice(0, 8)}${'•'.repeat(28)}`}</code>
                    <Button variant="ghost" size="icon-xs" onClick={() => setReveal((v) => !v)} aria-label={reveal ? 'Hide token' : 'Show token'}>
                      {reveal ? <EyeOff className="size-3.5" /> : <Eye className="size-3.5" />}
                    </Button>
                    <CopyButton value={issued.token} successText="Token copied" label="Copy" size="xs" variant="outline" />
                  </div>
                  {issued.ca_fingerprint_sha256 && (
                    <div className="flex items-start gap-2 text-[11px] text-muted-foreground">
                      <ShieldCheck className="mt-0.5 size-3.5 shrink-0 text-success" />
                      <span>
                        Hub CA pinned with <span className="font-mono text-foreground">--ca-fingerprint</span>, so the first connection cannot be intercepted (no trust-on-first-use).
                        <span className="mt-0.5 block break-all font-mono">{issued.ca_fingerprint_sha256}</span>
                      </span>
                    </div>
                  )}
                  <Button variant="ghost" size="xs" className="text-muted-foreground hover:text-destructive" disabled={revoke.isPending} onClick={() => revoke.mutate(issued.id)}>
                    <X className="size-3" />
                    Revoke this token
                  </Button>
                </div>
              </CardContent>
            </Card>

            <JoinWatcher issued={issued} />

            <Card className="rounded-md shadow-premium">
              <CardHeader>
                <CardTitle>Network exposure</CardTitle>
                <CardDescription>How the serving API reaches this machine. Only this control plane is ever trusted: its keys are pinned by a CA-signed policy.</CardDescription>
              </CardHeader>
              <CardContent className="space-y-3">
                <div className="grid gap-2 sm:grid-cols-2" role="radiogroup" aria-label="Serving mode">
                  {(
                    [
                      ['tunnel', 'Outbound tunnel (recommended)', 'The agent dials out to the API layer; the GPU machine opens no inbound port at all.'],
                      ['direct', 'Direct', 'The API layer dials the agent on :9443 (mTLS, its key pinned, allow-listed sources only). For low-latency private networks.'],
                    ] as const
                  ).map(([value, label, hint]) => (
                    <button
                      key={value}
                      type="button"
                      role="radio"
                      aria-checked={servingMode === value}
                      onClick={() => setServingMode(value)}
                      className={cn(
                        'rounded-lg border p-3 text-left text-xs transition-colors',
                        servingMode === value ? 'border-brand bg-brand/5' : 'border-border hover:bg-muted/40',
                      )}
                    >
                      <span className="block text-sm font-medium">{label}</span>
                      <span className="mt-0.5 block text-muted-foreground">{hint}</span>
                    </button>
                  ))}
                </div>
                <label className="flex items-start gap-2 text-xs">
                  <Checkbox checked={firewall} onCheckedChange={(v) => setFirewall(Boolean(v))} className="mt-0.5" />
                  <span>
                    <span className="block font-medium">Host firewall for the agent&apos;s ports (nftables / pf)</span>
                    <span className="text-muted-foreground">Applied and verified by the installer; the ports your sshd listens on stay open. Untick only if you manage the firewall yourself.</span>
                  </span>
                </label>
              </CardContent>
            </Card>

            <Card className="rounded-md shadow-premium">
              <CardHeader>
                <CardTitle>Install and join</CardTitle>
                <CardDescription>
                  Run on the GPU machine. The agent runs as its own non-root user with access to the GPU devices and the model cache only.
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">
                {issued.install_command && (
                  <div className="space-y-1.5">
                    <p className="text-xs font-medium text-muted-foreground">
                      Fastest: one command, talking only to the agent hub, pins the hub&apos;s CA, downloads the right build, checks its sha256, joins and installs the service (systemd or launchd)
                    </p>
                    <CodeBlock code={withInstallOptions(issued.install_command, servingMode, firewall)} language="shell" />
                  </div>
                )}
                <div className="space-y-1.5">
                  <p className="text-xs font-medium text-muted-foreground">Agent already installed? Just join:</p>
                  <CodeBlock code={joinCommand} language="shell" />
                </div>
                <div className="space-y-1.5 border-t border-border pt-4">
                  <p className="text-xs font-medium text-muted-foreground">Or step by step:</p>
                  <InstallCommands ctx={ctx} />
                </div>
              </CardContent>
            </Card>
          </div>
        ) : (
          <Card className="rounded-md border-dashed shadow-none">
            <CardContent className="flex h-full min-h-[320px] flex-col items-center justify-center gap-3 text-center">
              <div className="flex size-12 items-center justify-center rounded-xl border border-border bg-muted/60 shadow-premium">
                <Sparkles className="size-5 text-muted-foreground" />
              </div>
              <div className="max-w-sm space-y-1">
                <h3 className="text-sm font-semibold">Install commands appear here</h3>
                <p className="text-sm text-muted-foreground">
                  Generate a token to get a one-line installer plus copy-paste steps for Linux (systemd), macOS (launchd) and Kubernetes (DaemonSet), with a live expiry countdown.
                </p>
              </div>
            </CardContent>
          </Card>
        )}
      </div>

      <AgentBuildsCard />

      <Card className="rounded-md shadow-premium">
        <CardHeader>
          <CardTitle>Recent join tokens</CardTitle>
          <CardDescription>Only the prefix is stored in clear; tokens that can still enrol a machine can be revoked.</CardDescription>
        </CardHeader>
        <CardContent>
          {(recent.data?.items.length ?? 0) === 0 ? (
            <p className="py-6 text-center text-sm text-muted-foreground">No tokens issued yet.</p>
          ) : (
            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Token</TableHead>
                    <TableHead>Node name</TableHead>
                    <TableHead>Cluster</TableHead>
                    <TableHead>State</TableHead>
                    <TableHead>Issued</TableHead>
                    <TableHead>Expires / used</TableHead>
                    <TableHead className="w-10" />
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {recent.data?.items.map((t) => {
                    const state = tokenState(t, now);
                    return (
                      <TableRow key={t.id}>
                        <TableCell className="font-mono text-xs">{t.token_prefix}…</TableCell>
                        <TableCell className="text-xs">{t.node_name_hint ?? <span className="text-muted-foreground">hostname</span>}</TableCell>
                        <TableCell className="text-xs">{clusters.data?.items.find((c) => c.id === t.cluster_id)?.name ?? '—'}</TableCell>
                        <TableCell>
                          <StatusBadge variant={state.variant}>{state.label}</StatusBadge>
                        </TableCell>
                        <TableCell className="text-xs text-muted-foreground">{formatRelativeTime(t.created_at)}</TableCell>
                        <TableCell className="text-xs">
                          {t.used_at ? (
                            t.used_by_node_id ? (
                              <Link href={`/nodes/${t.used_by_node_id}`} className="hover:underline">
                                {formatRelativeTime(t.used_at)} → node
                              </Link>
                            ) : (
                              formatRelativeTime(t.used_at)
                            )
                          ) : isUsable(t, now) ? (
                            <ExpiryCountdown variant="inline" createdAt={t.created_at} expiresAt={t.expires_at} />
                          ) : (
                            <span className="text-muted-foreground">{formatRelativeTime(t.revoked_at ?? t.expires_at)}</span>
                          )}
                        </TableCell>
                        <TableCell>
                          {isUsable(t, now) && (
                            <Button variant="ghost" size="icon-xs" className="text-muted-foreground hover:text-destructive" onClick={() => revoke.mutate(t.id)} aria-label="Revoke token">
                              <X className="size-3.5" />
                            </Button>
                          )}
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </div>
          )}
        </CardContent>
      </Card>
    </PageContainer>
  );
}
