'use client';

import { useState } from 'react';
import { Ban, CircleCheck, CircleX, Globe, Loader2, LockKeyhole, MonitorSmartphone, MoreVertical, PenLine, Plus, Search, ShieldCheck, Trash2, TriangleAlert } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from '@/components/ui/dropdown-menu';
import { Input } from '@/components/ui/input';
import { Switch } from '@/components/ui/switch';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { PageContainer } from '@/components/shared/PageContainer';
import { PageHeader } from '@/components/shared/PageHeader';
import { ListPageSkeleton } from '@/components/shared/ListPageSkeleton';
import { ErrorState } from '@/components/shared/ErrorState';
import { DeleteConfirmationDialog } from '@/components/shared/DeleteConfirmationDialog';
import { useCheckIp, useCreateIpRule, useDeleteIpRule, useIpRules, useUpdateIpRule } from '@/hooks/api/security';
import type { IpRule, IpRuleAction, IpRuleScope } from '@/hooks/api/security-api';
import { cn } from '@/lib/utils';
import { Segmented } from '@/components/automation/segmented';
import { parseCidr, parseIp } from '@/components/security/cidr';

const SCOPES: Record<IpRuleScope, { label: string; icon: typeof Globe; hint: string }> = {
  admin: { label: 'Admin UI & API', icon: MonitorSmartphone, hint: 'This console and the admin API (/api/v1).' },
  api_layer: { label: 'API layer', icon: Globe, hint: 'The serving API clients call (OpenAI, Anthropic, Ollama formats).' },
};

function ActionBadge({ action }: { action: IpRuleAction }) {
  return action === 'allow' ? (
    <span className="inline-flex h-5 items-center gap-1 rounded-[5px] bg-success/10 px-1.5 text-[11px] font-semibold text-success-dark ring-1 ring-inset ring-success/30 dark:text-success-light">
      <ShieldCheck className="size-3" /> Allow
    </span>
  ) : (
    <span className="inline-flex h-5 items-center gap-1 rounded-[5px] bg-error/10 px-1.5 text-[11px] font-semibold text-error-dark ring-1 ring-inset ring-error/30 dark:text-error-light">
      <Ban className="size-3" /> Block
    </span>
  );
}

function RuleDialog({ open, onOpenChange, rule, scope: initialScope, rules }: { open: boolean; onOpenChange: (o: boolean) => void; rule: IpRule | null; scope: IpRuleScope; rules: IpRule[] }) {
  const create = useCreateIpRule();
  const update = useUpdateIpRule();
  const [scope, setScope] = useState<IpRuleScope>(rule?.scope ?? initialScope);
  const [action, setAction] = useState<IpRuleAction>(rule?.action ?? 'allow');
  const [cidr, setCidr] = useState(rule?.cidr ?? '');
  const [description, setDescription] = useState(rule?.description ?? '');
  const [enabled, setEnabled] = useState(rule?.enabled ?? true);
  const [expires, setExpires] = useState(rule?.expiresAt ? rule.expiresAt.slice(0, 10) : '');
  const [touched, setTouched] = useState(false);
  const [today] = useState(() => new Date().toISOString().slice(0, 10));
  const parsed = cidr.trim() ? parseCidr(cidr) : null;
  const error = !cidr.trim() ? 'Enter an address or a range, e.g. 10.0.0.0/8' : !parsed ? 'Not a valid IPv4 / IPv6 address or CIDR range' : null;
  const firstAllow = action === 'allow' && enabled && !rules.some((r) => r.scope === scope && r.action === 'allow' && r.enabled && r.id !== rule?.id);
  const busy = create.isPending || update.isPending;

  function submit() {
    setTouched(true);
    if (error || !parsed) return;
    const body = { scope, action, cidr: parsed.canonical, description: description.trim() || null, enabled, expires_at: expires ? new Date(`${expires}T00:00:00Z`).toISOString() : null };
    const done = { onSuccess: () => onOpenChange(false) };
    if (rule) update.mutate({ id: rule.id, body }, done);
    else create.mutate(body, done);
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{rule ? 'Edit IP rule' : 'Add IP rule'}</DialogTitle>
          <DialogDescription>Single addresses or CIDR ranges, IPv4 or IPv6. Changes apply within a couple of seconds.</DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1.5 text-xs font-medium">
              Applies to
              <Segmented value={scope} onChange={setScope} options={[{ value: 'admin', label: 'Admin' }, { value: 'api_layer', label: 'API layer' }]} aria-label="Scope" />
            </div>
            <div className="space-y-1.5 text-xs font-medium">
              Action
              <Segmented value={action} onChange={setAction} options={[{ value: 'allow', label: 'Allow', icon: ShieldCheck }, { value: 'block', label: 'Block', icon: Ban }]} aria-label="Action" />
            </div>
          </div>
          <label className="block space-y-1.5 text-xs font-medium">
            Address or range
            <Input value={cidr} onChange={(e) => setCidr(e.target.value)} onBlur={() => setTouched(true)} placeholder="192.168.1.0/24 or 2001:db8::/48" className="font-mono" aria-invalid={touched && !!error} autoFocus />
            {touched && error ? (
              <span className="block text-[11px] font-medium text-destructive">{error}</span>
            ) : parsed ? (
              <span className="block text-[11px] font-normal text-muted-foreground">
                IPv{parsed.version} · <span className="font-mono text-foreground">{parsed.canonical}</span> · {parsed.size} address{parsed.size === '1' ? '' : 'es'}
                {parsed.hadHostBits && <span className="text-warning-dark dark:text-warning-light"> — host bits dropped, saved as the network</span>}
              </span>
            ) : null}
          </label>
          <label className="block space-y-1.5 text-xs font-medium">
            Description <span className="font-normal text-muted-foreground">optional</span>
            <Input value={description} onChange={(e) => setDescription(e.target.value)} placeholder="Office LAN, VPN, AI gateway host…" />
          </label>
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="block space-y-1.5 text-xs font-medium">
              Expires <span className="font-normal text-muted-foreground">optional</span>
              <Input type="date" min={today} value={expires} onChange={(e) => setExpires(e.target.value)} />
            </label>
            <label className="flex items-center justify-between gap-3 pt-5 text-xs font-medium">
              Enabled <Switch checked={enabled} onCheckedChange={setEnabled} />
            </label>
          </div>
          {firstAllow && (
            <p className="flex items-start gap-2 rounded-md border border-warning/30 bg-warning/10 p-3 text-xs text-warning-dark dark:text-warning-light">
              <TriangleAlert className="mt-px size-3.5 shrink-0" />
              This is the first allow rule for {SCOPES[scope].label}: once saved, only listed ranges can connect. {scope === 'admin' ? 'Make sure your own address is covered, or you will lock yourself out.' : 'Clients outside the list get 403.'}
            </p>
          )}
        </div>
        <DialogFooter>
          <Button variant="outline" size="sm" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button size="sm" disabled={busy} onClick={submit}>
            {busy && <Loader2 className="size-4 animate-spin" />}
            {rule ? 'Save rule' : 'Add rule'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function Tester() {
  const check = useCheckIp();
  const [ip, setIp] = useState('');
  const [scope, setScope] = useState<IpRuleScope>('admin');
  const invalid = ip.trim() !== '' && !parseIp(ip);
  const r = check.data;
  return (
    <Card className="gap-0 rounded-xl py-0">
      <CardHeader className="border-b border-border px-5 py-4">
        <CardTitle className="flex items-center gap-2 text-sm font-semibold">
          <Search className="size-4" /> Test an address
        </CardTitle>
        <CardDescription>See exactly what the rules decide for one IP.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3 px-5 py-4">
        <form
          className="flex flex-wrap items-center gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (ip.trim() && !invalid) check.mutate({ ip: ip.trim(), scope });
          }}
        >
          <Input value={ip} onChange={(e) => setIp(e.target.value)} placeholder="203.0.113.7" className="h-9 w-48 font-mono text-xs" aria-invalid={invalid} aria-label="IP address" />
          <Segmented size="sm" value={scope} onChange={setScope} options={[{ value: 'admin', label: 'Admin' }, { value: 'api_layer', label: 'API layer' }]} aria-label="Scope" />
          <Button type="submit" size="sm" variant="outline" disabled={!ip.trim() || invalid || check.isPending}>
            {check.isPending ? <Loader2 className="size-3.5 animate-spin" /> : 'Check'}
          </Button>
        </form>
        {invalid && <p className="text-[11px] font-medium text-destructive">Not a valid IPv4 or IPv6 address.</p>}
        {r && (
          <div className={cn('flex items-start gap-3 rounded-lg border p-3 text-xs', r.allowed ? 'border-success/30 bg-success/[0.06]' : 'border-error/30 bg-error/[0.06]')}>
            {r.allowed ? <CircleCheck className="mt-px size-4 shrink-0 text-success" /> : <CircleX className="mt-px size-4 shrink-0 text-error" />}
            <div>
              <p className="font-semibold">
                <span className="font-mono">{r.ip}</span> is {r.allowed ? 'allowed' : 'blocked'} on {SCOPES[r.scope].label}
              </p>
              <p className="mt-0.5 text-muted-foreground">{r.reason}</p>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

export function IpRulesPage() {
  const { data, isLoading, isError, refetch } = useIpRules();
  const update = useUpdateIpRule();
  const del = useDeleteIpRule();
  const [dialog, setDialog] = useState<{ open: boolean; rule: IpRule | null; scope: IpRuleScope }>({ open: false, rule: null, scope: 'admin' });
  const [deleting, setDeleting] = useState<IpRule | null>(null);
  const [now] = useState(() => Date.now());

  if (isLoading && !data) return <ListPageSkeleton stats={0} />;
  const rules = data?.items ?? [];

  return (
    <PageContainer>
      <PageHeader
        title="IP rules"
        description="Allow and block lists for the admin console and for the API layer."
        actions={
          <Button size="sm" className="rounded-sm" onClick={() => setDialog({ open: true, rule: null, scope: 'admin' })}>
            <Plus className="size-4" /> Add rule
          </Button>
        }
      />

      <Card className="rounded-xl">
        <CardContent className="grid gap-4 md:grid-cols-3">
          {[
            { icon: Ban, title: 'Block always wins', body: 'An address that matches any enabled block rule is refused, even if an allow rule also matches.' },
            { icon: LockKeyhole, title: 'One allow rule = allow-list mode', body: 'As soon as a scope has an enabled allow rule, only matching addresses get in; everything else is refused.' },
            { icon: Globe, title: 'No allow rules = open', body: 'Without allow rules a scope accepts every address that is not blocked. The API layer also trusts GATEWAY_IP_ALLOWLIST.' },
          ].map((x) => (
            <div key={x.title} className="flex gap-3">
              <span className="flex size-8 shrink-0 items-center justify-center rounded-lg border border-border bg-muted/60">
                <x.icon className="size-4" />
              </span>
              <div>
                <p className="text-xs font-semibold">{x.title}</p>
                <p className="mt-0.5 text-[11px] leading-relaxed text-muted-foreground">{x.body}</p>
              </div>
            </div>
          ))}
        </CardContent>
      </Card>

      {isError ? (
        <ErrorState title="Could not load IP rules" onRetry={() => void refetch()} />
      ) : (
        <div className="grid gap-6 xl:grid-cols-2">
          {(Object.keys(SCOPES) as IpRuleScope[]).map((scope) => {
            const meta = SCOPES[scope];
            const list = rules.filter((r) => r.scope === scope).sort((a, b) => (a.action === b.action ? a.cidr.localeCompare(b.cidr) : a.action === 'block' ? -1 : 1));
            const allows = list.filter((r) => r.action === 'allow' && r.enabled);
            const blocks = list.filter((r) => r.action === 'block' && r.enabled);
            const allowList = allows.length > 0;
            return (
              <Card key={scope} className="gap-0 rounded-xl py-0">
                <CardHeader className="flex flex-row items-start justify-between gap-3 border-b border-border px-5 py-4">
                  <div className="flex items-start gap-3">
                    <span className="flex size-9 shrink-0 items-center justify-center rounded-lg border border-border bg-muted/60">
                      <meta.icon className="size-4" />
                    </span>
                    <div>
                      <CardTitle className="text-sm font-semibold">{meta.label}</CardTitle>
                      <CardDescription>{meta.hint}</CardDescription>
                    </div>
                  </div>
                  <Button variant="outline" size="xs" onClick={() => setDialog({ open: true, rule: null, scope })}>
                    <Plus className="size-3" /> Add
                  </Button>
                </CardHeader>
                <div className={cn('flex items-center gap-2 border-b border-border px-5 py-2.5 text-xs', allowList ? 'bg-warning/[0.06] text-warning-dark dark:text-warning-light' : 'bg-muted/30 text-muted-foreground')}>
                  {allowList ? <LockKeyhole className="size-3.5" /> : <Globe className="size-3.5" />}
                  {allowList ? (
                    <span>
                      <b className="font-semibold">Allow-list mode</b> — only {allows.length} listed range{allows.length === 1 ? '' : 's'} can connect{blocks.length ? `, minus ${blocks.length} blocked` : ''}.
                    </span>
                  ) : (
                    <span>
                      <b className="font-semibold text-foreground">Open</b> — every address except {blocks.length ? `${blocks.length} blocked range${blocks.length === 1 ? '' : 's'}` : 'none'} can connect.
                    </span>
                  )}
                </div>
                <CardContent className="px-5 py-3">
                  {list.length === 0 ? (
                    <p className="py-6 text-center text-xs text-muted-foreground">No rules — this scope is open to every address.</p>
                  ) : (
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <TableHead className="w-20">Action</TableHead>
                          <TableHead>Range</TableHead>
                          <TableHead>Expires</TableHead>
                          <TableHead className="w-12">On</TableHead>
                          <TableHead className="w-10" />
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {list.map((r) => (
                          <TableRow key={r.id} className={cn(!r.enabled && 'opacity-55')}>
                            <TableCell>
                              <ActionBadge action={r.action} />
                            </TableCell>
                            <TableCell>
                              <span className="font-mono text-xs font-medium">{r.cidr}</span>
                              {r.description && <span className="block text-[11px] text-muted-foreground">{r.description}</span>}
                            </TableCell>
                            <TableCell className="text-[11px] text-muted-foreground">{r.expiresAt ? `${Math.max(0, Math.ceil((Date.parse(r.expiresAt) - now) / 86_400_000))} days` : 'never'}</TableCell>
                            <TableCell>
                              <Switch size="sm" checked={r.enabled} onCheckedChange={(v) => update.mutate({ id: r.id, body: { enabled: v } })} aria-label={`${r.enabled ? 'Disable' : 'Enable'} ${r.cidr}`} />
                            </TableCell>
                            <TableCell>
                              <DropdownMenu>
                                <DropdownMenuTrigger render={<Button variant="ghost" size="icon-xs" aria-label={`Actions for ${r.cidr}`} />}>
                                  <MoreVertical className="size-4" />
                                </DropdownMenuTrigger>
                                <DropdownMenuContent align="end" className="w-36">
                                  <DropdownMenuItem onClick={() => setDialog({ open: true, rule: r, scope: r.scope })}>
                                    <PenLine className="size-4" /> Edit
                                  </DropdownMenuItem>
                                  <DropdownMenuSeparator />
                                  <DropdownMenuItem className="text-destructive" onClick={() => setDeleting(r)}>
                                    <Trash2 className="size-4" /> Delete
                                  </DropdownMenuItem>
                                </DropdownMenuContent>
                              </DropdownMenu>
                            </TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  )}
                </CardContent>
              </Card>
            );
          })}
        </div>
      )}

      <Tester />

      {dialog.open && <RuleDialog key={dialog.rule?.id ?? `new-${dialog.scope}`} open={dialog.open} onOpenChange={(o) => setDialog((d) => ({ ...d, open: o }))} rule={dialog.rule} scope={dialog.scope} rules={rules} />}
      <DeleteConfirmationDialog
        open={!!deleting}
        onOpenChange={(o) => !o && setDeleting(null)}
        itemName={deleting?.cidr ?? ''}
        resourceType="IP rule"
        warningMessage={deleting?.action === 'allow' && rules.filter((r) => r.scope === deleting.scope && r.action === 'allow' && r.enabled).length === 1 ? 'This is the last allow rule for its scope — deleting it opens the scope to every address that is not blocked.' : undefined}
        isDeleting={del.isPending}
        onConfirm={() => {
          if (deleting) del.mutate(deleting.id, { onSuccess: () => setDeleting(null) });
        }}
      />
    </PageContainer>
  );
}
