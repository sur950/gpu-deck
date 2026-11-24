'use client';

import { useState } from 'react';
import { ArrowDownToLine, Pencil, ShieldOff, Trash2 } from 'lucide-react';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { Switch } from '@/components/ui/switch';
import { Checkbox } from '@/components/ui/checkbox';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { FormDialog, Field } from '@/components/shared/form-dialog';
import { KeyValueEditor, recordToRows, rowsToRecord, type KeyValueRow } from '@/components/shared/key-value-editor';
import { useDrainNode, usePricePlans, useRemoveNode, useRevokeNodeCertificate, useUpdateNode } from '@/hooks/api/fleet';
import { formatBytes } from '@/utils/format';
import type { Node, NodeCertificate } from '@/types/fleet';

const GiB = 1024 ** 3;
const NONE = '__none__';

// ─── Drain ──────────────────────────────────────────────────────────────────

export function DrainNodeDialog({ node, open, onOpenChange }: { node: Node | null; open: boolean; onOpenChange: (open: boolean) => void }) {
  const drain = useDrainNode();
  const [reason, setReason] = useState('');
  const [timeout, setTimeout_] = useState('300');
  const [move, setMove] = useState(true);
  if (!node) return null;
  const count = node.instance_count ?? 0;
  return (
    <FormDialog
      open={open}
      onOpenChange={onOpenChange}
      icon={<ArrowDownToLine className="size-4 text-warning" />}
      title={`Drain ${node.name}`}
      description="The scheduler stops placing work here. Running instances are drained safely: routing stops, in-flight requests finish (up to the timeout), then they are moved or unloaded."
      submitLabel="Start drain"
      isPending={drain.isPending}
      submitDisabled={!Number.isFinite(Number(timeout)) || Number(timeout) < 0 || Number(timeout) > 3600}
      footerNote={count > 0 ? `${count} instance${count === 1 ? '' : 's'} affected` : 'No instances on this node'}
      onSubmit={() =>
        drain.mutate(
          { id: node.id, body: { reason: reason.trim() || null, timeout_seconds: Number(timeout), move_instances: move } },
          { onSuccess: () => onOpenChange(false) },
        )
      }
    >
      <Field label="Reason" hint="Shown on the node and in the audit log." htmlFor="drain-reason">
        <Input id="drain-reason" value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Driver upgrade to 575.x" />
      </Field>
      <Field label="Drain timeout (seconds)" hint="Wait at most this long for running and queued requests to reach zero (0–3600)." htmlFor="drain-timeout">
        <Input id="drain-timeout" type="number" min={0} max={3600} value={timeout} onChange={(e) => setTimeout_(e.target.value)} className="w-32 font-mono" />
      </Field>
      <label className="flex items-start justify-between gap-4 rounded-lg border border-border bg-muted/30 p-3">
        <span className="space-y-0.5">
          <span className="block text-xs font-medium">Move instances to other nodes</span>
          <span className="block text-[11px] text-muted-foreground">The scheduler re-places each replica before unloading it here. Off: just unload.</span>
        </span>
        <Switch checked={move} onCheckedChange={setMove} aria-label="Move instances to other nodes" />
      </label>
    </FormDialog>
  );
}

// ─── Edit ───────────────────────────────────────────────────────────────────

export function EditNodeDialog({ node, open, onOpenChange }: { node: Node; open: boolean; onOpenChange: (open: boolean) => void }) {
  const update = useUpdateNode();
  const plans = usePricePlans();
  const [name, setName] = useState(node.name);
  const [labels, setLabels] = useState<KeyValueRow[]>(() => recordToRows(node.labels));
  const [plan, setPlan] = useState(node.price_plan_id ?? '');
  const [proxyAddress, setProxyAddress] = useState(node.proxy_address ?? '');
  const [proxyPort, setProxyPort] = useState(String(node.proxy_port ?? 9443));
  const [cacheLimit, setCacheLimit] = useState(node.cache_limit_bytes ? String(Math.round(node.cache_limit_bytes / GiB)) : '');
  const nameValid = /^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$/.test(name) && name.length <= 63;
  const portValid = Number(proxyPort) >= 1 && Number(proxyPort) <= 65535;
  return (
    <FormDialog
      open={open}
      onOpenChange={onOpenChange}
      icon={<Pencil className="size-4 text-muted-foreground" />}
      title={`Edit ${node.name}`}
      description="Labels drive placement and rule targets. Changes apply to new placements; running instances keep serving."
      submitLabel="Save changes"
      widthClass="sm:max-w-xl"
      isPending={update.isPending}
      submitDisabled={!nameValid || !portValid}
      onSubmit={() =>
        update.mutate(
          {
            id: node.id,
            body: {
              name,
              labels: rowsToRecord(labels),
              price_plan_id: plan || null,
              proxy_address: proxyAddress.trim() || null,
              proxy_port: Number(proxyPort),
              cache_limit_bytes: cacheLimit ? Math.round(Number(cacheLimit) * GiB) : null,
            },
          },
          { onSuccess: () => onOpenChange(false) },
        )
      }
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Name" hint={nameValid ? 'Unique within the cluster.' : 'Lowercase letters, digits, dots and dashes.'} htmlFor="node-name">
          <Input id="node-name" value={name} onChange={(e) => setName(e.target.value)} className="font-mono text-sm" aria-invalid={!nameValid} />
        </Field>
        <Field label="Price plan" hint="Overrides the cluster's plan for GPU-minute billing.">
          <Select value={plan || NONE} onValueChange={(v) => setPlan(v === NONE ? '' : String(v))}>
            <SelectTrigger className="w-full">
              <SelectValue>{(v: unknown) => (plans.data ?? []).find((p) => p.id === v)?.name ?? 'Inherit from cluster'}</SelectValue>
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={NONE}>Inherit from cluster</SelectItem>
              {(plans.data ?? []).map((p) => (
                <SelectItem key={p.id} value={p.id}>
                  {p.name} · ${p.usd_per_gpu_minute}/GPU-min
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </Field>
        <Field label="Proxy address" hint="Where the API layer reaches this node's mTLS proxy." htmlFor="node-proxy">
          <Input id="node-proxy" value={proxyAddress} onChange={(e) => setProxyAddress(e.target.value)} placeholder="10.0.4.12" className="font-mono text-sm" />
        </Field>
        <Field label="Proxy port" htmlFor="node-port">
          <Input id="node-port" type="number" value={proxyPort} onChange={(e) => setProxyPort(e.target.value)} className="font-mono text-sm" aria-invalid={!portValid} />
        </Field>
        <Field label="Model cache limit (GiB)" hint="Empty = 70 % of the cache volume (agent default)." htmlFor="node-cache">
          <Input id="node-cache" type="number" min={1} value={cacheLimit} onChange={(e) => setCacheLimit(e.target.value)} placeholder="auto" className="font-mono text-sm" />
        </Field>
      </div>
      <Field label="Labels">
        <KeyValueEditor rows={labels} onChange={setLabels} keyPlaceholder="zone" valuePlaceholder="rack-1" addLabel="Add label" />
      </Field>
    </FormDialog>
  );
}

// ─── Remove ─────────────────────────────────────────────────────────────────

export function RemoveNodeDialog({ node, open, onOpenChange, onRemoved }: { node: Node | null; open: boolean; onOpenChange: (open: boolean) => void; onRemoved?: () => void }) {
  const remove = useRemoveNode();
  const [force, setForce] = useState(false);
  const [confirm, setConfirm] = useState('');
  if (!node) return null;
  const busy = (node.instance_count ?? 0) > 0;
  return (
    <FormDialog
      open={open}
      onOpenChange={onOpenChange}
      variant="destructive"
      icon={<Trash2 className="size-4 text-destructive" />}
      title={`Remove ${node.name}`}
      description="The node leaves the fleet and every certificate it holds is revoked, so the agent is cut off immediately. Its model cache stays on disk."
      submitLabel="Remove node"
      isPending={remove.isPending}
      submitDisabled={confirm !== node.name || (busy && !force)}
      onSubmit={() => remove.mutate({ id: node.id, force }, { onSuccess: () => { onOpenChange(false); onRemoved?.(); } })}
    >
      {busy && (
        <label className="flex items-start gap-3 rounded-lg border border-warning/40 bg-warning/10 p-3 text-xs">
          <Checkbox checked={force} onCheckedChange={(v) => setForce(v === true)} className="mt-0.5" />
          <span>
            <span className="block font-medium text-warning-dark dark:text-warning-light">{node.instance_count} instance(s) still run here</span>
            <span className="text-muted-foreground">Drain first for a clean hand-off, or force removal — in-flight requests on this node will fail.</span>
          </span>
        </label>
      )}
      <Field label={<>Type <span className="font-mono">{node.name}</span> to confirm</>} htmlFor="remove-confirm">
        <Input id="remove-confirm" value={confirm} onChange={(e) => setConfirm(e.target.value)} autoComplete="off" className="font-mono text-sm" />
      </Field>
    </FormDialog>
  );
}

// ─── Revoke certificate ─────────────────────────────────────────────────────

export function RevokeCertificateDialog({ node, cert, open, onOpenChange }: { node: Node; cert: NodeCertificate | null; open: boolean; onOpenChange: (open: boolean) => void }) {
  const revoke = useRevokeNodeCertificate();
  const [reason, setReason] = useState('');
  if (!cert) return null;
  return (
    <FormDialog
      open={open}
      onOpenChange={onOpenChange}
      variant="destructive"
      icon={<ShieldOff className="size-4 text-destructive" />}
      title="Revoke certificate"
      description={
        cert.is_active
          ? `This is ${node.name}'s active certificate. The hub closes the agent's connection (4003), the CRL is pushed to every agent and the API layer stops routing to this node. Join it again with a new token to restore it.`
          : 'The certificate is added to the revocation list pushed to every agent and the API layer.'
      }
      submitLabel="Revoke"
      isPending={revoke.isPending}
      onSubmit={() => revoke.mutate({ nodeId: node.id, certId: cert.id, reason: reason.trim() || undefined }, { onSuccess: () => onOpenChange(false) })}
    >
      <div className="rounded-lg border border-border bg-muted/30 p-3 font-mono text-[11px] leading-relaxed">
        <div>serial {cert.serial}</div>
        <div className="break-all text-muted-foreground">sha256 {cert.fingerprint_sha256}</div>
      </div>
      <Field label="Reason" htmlFor="revoke-reason" hint="Recorded in the CRL entry and the audit log.">
        <Textarea id="revoke-reason" rows={2} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Machine retired / key compromised" />
      </Field>
    </FormDialog>
  );
}

/** "12.4 GiB of 600 GiB" for cache bars. */
export function cacheLabel(used: number | null | undefined, limit: number | null | undefined): string {
  if (!limit) return used ? formatBytes(used) : '—';
  return `${formatBytes(used ?? 0)} of ${formatBytes(limit)}`;
}
