'use client';

import { useState } from 'react';
import { Hash, Loader2, Mail, Webhook } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Switch } from '@/components/ui/switch';
import { CredentialPicker } from '@/components/vault/credential-picker';
import { useCreateAlertChannel, useUpdateAlertChannel } from '@/hooks/api/automation';
import { useCredentialOptionsAll } from '@/hooks/use-vault';
import type { AlertChannel, AlertChannelType, AlertSeverity } from '@/hooks/api/automation-api';
import { cn } from '@/lib/utils';
import { Segmented } from '@/components/automation/segmented';

export const CHANNEL_ICONS = { slack: Hash, email: Mail, webhook: Webhook } as const;

/** Event types the control plane emits (rules.md §4 plus fleet / billing events); globs allowed. */
export const EVENT_TYPES = [
  { value: 'rule.*', label: 'All rule events' },
  { value: 'rule.fired', label: 'Rule acted' },
  { value: 'rule.dry_run', label: 'Dry-run decisions' },
  { value: 'rule.error', label: 'Rule errors' },
  { value: 'swap.*', label: 'All swap events' },
  { value: 'swap.failed', label: 'Swap failed' },
  { value: 'swap.rolled_back', label: 'Swap rolled back' },
  { value: 'heal.*', label: 'Auto-heal actions' },
  { value: 'node.*', label: 'Node offline / back' },
  { value: 'device.*', label: 'GPU unhealthy' },
];

interface ChannelDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  channel?: AlertChannel | null;
  /** Called after a new channel was created (the page switches to the Channels tab). */
  onCreated?: () => void;
}

/** Create or edit a Slack / email / signed-webhook channel. Secrets are vault credentials picked (or added) here. */
export function ChannelDialog({ open, onOpenChange, channel, onCreated }: ChannelDialogProps) {
  const editing = !!channel;
  const create = useCreateAlertChannel();
  const update = useUpdateAlertChannel();
  const cfg = (channel?.config ?? {}) as Record<string, unknown>;
  const [type, setType] = useState<AlertChannelType>(channel?.type ?? 'slack');
  const [name, setName] = useState(channel?.name ?? '');
  const [slackChannel, setSlackChannel] = useState(String(cfg.channel ?? '#gpu-ops'));
  const [recipients, setRecipients] = useState(Array.isArray(cfg.recipients) ? (cfg.recipients as string[]).join(', ') : '');
  const [from, setFrom] = useState(String(cfg.from ?? ''));
  const [url, setUrl] = useState(String(cfg.url ?? ''));
  const [credentialId, setCredentialId] = useState(channel?.credentialId ?? '');
  const credentials = useCredentialOptionsAll();
  const picked = (credentials.data ?? []).find((c) => c.id === credentialId);
  const pickedUrl = typeof picked?.fields?.url === 'string' ? picked.fields.url : '';
  const [filter, setFilter] = useState<string[]>(channel?.eventFilter ?? []);
  const [custom, setCustom] = useState('');
  const [minSeverity, setMinSeverity] = useState<AlertSeverity>(channel?.minSeverity ?? 'warning');
  const [enabled, setEnabled] = useState(channel?.enabled ?? true);
  const [submitted, setSubmitted] = useState(false);

  const recipientList = recipients.split(/[,\s]+/).map((r) => r.trim()).filter(Boolean);
  const errors: Record<string, string> = {};
  if (!name.trim()) errors.name = 'Name the channel';
  if (type === 'slack' && !credentialId) errors.credential = 'Pick the Slack webhook credential (or add one)';
  if (type === 'email' && recipientList.length === 0) errors.recipients = 'Add at least one address';
  if (type === 'email' && recipientList.some((r) => !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(r))) errors.recipients = 'One of the addresses looks wrong';
  if (type === 'webhook' && !pickedUrl && !/^https?:\/\/.+/.test(url.trim())) errors.url = 'Enter an http(s) URL, or pick a signed-webhook credential that has one';
  const show = (k: string) => (submitted ? errors[k] : undefined);

  function config(): Record<string, unknown> {
    // Keep settings this form does not edit (headers, username, legacy SMTP keys).
    const keep = Object.fromEntries(Object.entries(cfg).filter(([k]) => !['channel', 'recipients', 'from', 'url', 'signing'].includes(k)));
    if (type === 'slack') return { ...keep, channel: slackChannel.trim() || null };
    if (type === 'email') return { ...keep, recipients: recipientList, ...(from.trim() ? { from: from.trim() } : {}) };
    return { ...keep, ...(url.trim() ? { url: url.trim() } : {}), signing: credentialId ? 'hmac-sha256' : 'none' };
  }

  function submit() {
    setSubmitted(true);
    if (Object.keys(errors).length) return;
    const done = { onSuccess: () => onOpenChange(false) };
    if (editing && channel) {
      update.mutate({ id: channel.id, body: { name: name.trim(), config: config(), credential_id: credentialId || null, event_filter: filter, min_severity: minSeverity, enabled } }, done);
    } else {
      create.mutate(
        { name: name.trim(), type, config: config(), credential_id: credentialId || null, event_filter: filter, min_severity: minSeverity, enabled },
        { onSuccess: () => { onOpenChange(false); onCreated?.(); } },
      );
    }
  }

  const busy = create.isPending || update.isPending;
  const err = (k: string) => show(k) && <span className="block text-[11px] font-medium text-destructive">{show(k)}</span>;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[88vh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{editing ? `Edit “${channel?.name}”` : 'New alert channel'}</DialogTitle>
          <DialogDescription>Rule actions, failed swaps, auto-heal and fleet health alerts are sent here. The webhook URL, SMTP login or signing secret is a vault credential.</DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          {!editing && (
            <Segmented
              variant="block"
              value={type}
              onChange={(t) => {
                setType(t);
                setCredentialId('');
              }}
              options={[
                { value: 'slack', label: 'Slack', icon: Hash, hint: 'Incoming webhook' },
                { value: 'email', label: 'Email', icon: Mail, hint: 'SMTP' },
                { value: 'webhook', label: 'Webhook', icon: Webhook, hint: 'HMAC-signed POST' },
              ]}
              aria-label="Channel type"
            />
          )}
          <label className="block space-y-1.5 text-xs font-medium">
            Name
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder={type === 'slack' ? 'Slack #gpu-ops' : type === 'email' ? 'Email on-call' : 'FinOps webhook'} aria-invalid={!!show('name')} />
            {err('name')}
          </label>
          {type === 'slack' && (
            <>
              <label className="block space-y-1.5 text-xs font-medium">
                Slack channel <span className="font-normal text-muted-foreground">shown in messages</span>
                <Input value={slackChannel} onChange={(e) => setSlackChannel(e.target.value)} className="font-mono text-xs" />
              </label>
              <div className="space-y-1.5 text-xs font-medium">
                Incoming webhook
                <CredentialPicker value={credentialId} onChange={(id) => setCredentialId(id)} kinds={['slack']} />
                {err('credential')}
              </div>
            </>
          )}
          {type === 'email' && (
            <>
              <label className="block space-y-1.5 text-xs font-medium">
                Recipients
                <Input value={recipients} onChange={(e) => setRecipients(e.target.value)} placeholder="oncall@example.com, platform@example.com" aria-invalid={!!show('recipients')} />
                {err('recipients')}
              </label>
              <div className="space-y-1.5 text-xs font-medium">
                SMTP server <span className="font-normal text-muted-foreground">optional — without one the control plane&apos;s SMTP_* settings are used</span>
                <CredentialPicker value={credentialId} onChange={(id) => setCredentialId(id)} kinds={['smtp']} allowNone noneLabel="Server default (SMTP_*)" />
              </div>
              <label className="block space-y-1.5 text-xs font-medium">
                From <span className="font-normal text-muted-foreground">optional — overrides the credential&apos;s address</span>
                <Input value={from} onChange={(e) => setFrom(e.target.value)} placeholder="gpudeck@example.com" />
              </label>
            </>
          )}
          {type === 'webhook' && (
            <>
              <div className="space-y-1.5 text-xs font-medium">
                Signed webhook <span className="font-normal text-muted-foreground">URL + HMAC secret (X-GPUDeck-Signature)</span>
                <CredentialPicker value={credentialId} onChange={(id) => setCredentialId(id)} kinds={['webhook']} allowNone noneLabel="Unsigned (URL below)" />
              </div>
              <label className="block space-y-1.5 text-xs font-medium">
                URL <span className="font-normal text-muted-foreground">{pickedUrl ? `optional — the credential's ${pickedUrl.replace(/^https?:\/\//, '').split('/')[0]} is used when empty` : 'required without a credential'}</span>
                <Input value={url} onChange={(e) => setUrl(e.target.value)} placeholder={pickedUrl || 'https://hooks.example.com/gpu-deck'} className="font-mono text-xs" aria-invalid={!!show('url')} />
                {err('url')}
              </label>
            </>
          )}
          <div className="space-y-2 text-xs font-medium">
            Send
            <div className="flex flex-wrap gap-1.5">
              <button type="button" onClick={() => setFilter([])} aria-pressed={filter.length === 0} className={cn('h-7 rounded-full border px-3 text-xs outline-none focus-visible:ring-2 focus-visible:ring-ring/30', filter.length === 0 ? 'border-foreground/30 bg-foreground/[0.06] text-foreground' : 'border-border text-muted-foreground hover:text-foreground')}>
                Everything
              </button>
              {[...EVENT_TYPES, ...filter.filter((f) => !EVENT_TYPES.some((e) => e.value === f)).map((f) => ({ value: f, label: f }))].map((e) => {
                const on = filter.includes(e.value);
                return (
                  <button key={e.value} type="button" aria-pressed={on} onClick={() => setFilter(on ? filter.filter((f) => f !== e.value) : [...filter, e.value])} className={cn('h-7 rounded-full border px-3 text-xs font-normal outline-none focus-visible:ring-2 focus-visible:ring-ring/30', on ? 'border-foreground/30 bg-foreground/[0.06] text-foreground' : 'border-border text-muted-foreground hover:text-foreground')} title={e.value}>
                    {e.label}
                  </button>
                );
              })}
            </div>
            <div className="flex gap-2">
              <Input value={custom} onChange={(e) => setCustom(e.target.value)} placeholder="custom glob, e.g. instance.*" className="h-8 font-mono text-xs" />
              <Button type="button" variant="outline" size="sm" disabled={!custom.trim()} onClick={() => { setFilter([...new Set([...filter, custom.trim()])]); setCustom(''); }}>
                Add
              </Button>
            </div>
            <p className="text-[11px] font-normal text-muted-foreground">Dry-run decisions only reach channels that list them explicitly.</p>
          </div>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="space-y-1.5 text-xs font-medium">
              Minimum severity
              <Segmented size="sm" value={minSeverity} onChange={setMinSeverity} options={[{ value: 'info', label: 'Info' }, { value: 'warning', label: 'Warning' }, { value: 'critical', label: 'Critical' }]} aria-label="Minimum severity" />
            </div>
            <label className="flex items-center gap-2 text-xs font-medium">
              Enabled <Switch checked={enabled} onCheckedChange={setEnabled} />
            </label>
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" size="sm" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button size="sm" disabled={busy} onClick={submit}>
            {busy && <Loader2 className="size-4 animate-spin" />}
            {editing ? 'Save channel' : 'Create channel'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
