'use client';

import { useState } from 'react';
import { Boxes, Loader2, MoreVertical, PenLine, Plus, Receipt, Server, Star, Trash2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from '@/components/ui/dropdown-menu';
import { Input } from '@/components/ui/input';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Skeleton } from '@/components/ui/skeleton';
import { EmptyState } from '@/components/shared/EmptyState';
import { ErrorState } from '@/components/shared/ErrorState';
import { DeleteConfirmationDialog } from '@/components/shared/DeleteConfirmationDialog';
import { useAttachPricePlan, useCreatePricePlan, useDeletePricePlan, usePricePlans, useUpdatePricePlan } from '@/hooks/api/usage';
import { useRefClusters, useRefNodes } from '@/hooks/api/automation';
import type { RefCluster, RefNode } from '@/hooks/api/automation-api';
import type { PricePlan } from '@/hooks/api/usage-api';

/** Node plan overrides the cluster plan; the default plan covers the rest. */
export function effectivePlanId(node: RefNode | undefined, clusters: RefCluster[], plans: PricePlan[]): string | null {
  if (!node) return null;
  return node.priceplanId ?? clusters.find((c) => c.id === node.clusterId)?.pricePlanId ?? plans.find((p) => p.isDefault)?.id ?? null;
}

function rate(v: number, digits = 4): string {
  return v === 0 ? '—' : `$${v.toFixed(v < 0.001 ? 5 : digits).replace(/0+$/, '').replace(/\.$/, '.0')}`;
}

function PlanDialog({ open, onOpenChange, plan }: { open: boolean; onOpenChange: (o: boolean) => void; plan: PricePlan | null }) {
  const create = useCreatePricePlan();
  const update = useUpdatePricePlan();
  const [name, setName] = useState(plan?.name ?? '');
  const [description, setDescription] = useState(plan?.description ?? '');
  const [gpu, setGpu] = useState(String(plan?.usdPerGpuMinute ?? 0));
  const [input, setInput] = useState(String(plan?.usdPer1kInputTokens ?? 0));
  const [output, setOutput] = useState(String(plan?.usdPer1kOutputTokens ?? 0));
  const [cached, setCached] = useState(String(plan?.usdPer1kCachedTokens ?? 0));
  const [isDefault, setIsDefault] = useState(plan?.isDefault ?? false);
  const nums = [gpu, input, output, cached].map(Number);
  const invalid = !name.trim() || nums.some((n) => !Number.isFinite(n) || n < 0);
  const busy = create.isPending || update.isPending;
  const perHour = Number(gpu) * 60;

  function submit() {
    if (invalid) return;
    const body = { name: name.trim(), description: description.trim() || null, usd_per_gpu_minute: Number(gpu), usd_per_1k_input_tokens: Number(input), usd_per_1k_output_tokens: Number(output), usd_per_1k_cached_tokens: Number(cached), is_default: isDefault };
    const done = { onSuccess: () => onOpenChange(false) };
    if (plan) update.mutate({ id: plan.id, body }, done);
    else create.mutate(body, done);
  }

  const field = (label: string, value: string, set: (v: string) => void, hint: string) => (
    <label className="space-y-1.5 text-xs font-medium">
      {label}
      <div className="relative">
        <span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-xs text-muted-foreground">$</span>
        <Input type="number" min={0} step="any" value={value} onChange={(e) => set(e.target.value)} className="h-9 pl-6 font-mono text-xs" />
      </div>
      <span className="block text-[11px] font-normal text-muted-foreground">{hint}</span>
    </label>
  );

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{plan ? `Edit “${plan.name}”` : 'New price plan'}</DialogTitle>
          <DialogDescription>Charge per GPU minute, per token, or both. Usage keeps the plan that applied when it happened.</DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <label className="block space-y-1.5 text-xs font-medium">
            Name
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="L40S on-prem" />
          </label>
          <label className="block space-y-1.5 text-xs font-medium">
            Description <span className="font-normal text-muted-foreground">optional</span>
            <Textarea value={description} onChange={(e) => setDescription(e.target.value)} rows={2} className="text-xs" placeholder="How the rate was derived (amortisation, power, list price)." />
          </label>
          <div className="grid gap-3 sm:grid-cols-2">
            {field('Per GPU minute', gpu, setGpu, Number(gpu) > 0 ? `≈ $${perHour.toFixed(2)} per GPU hour` : '0 = tokens only')}
            {field('Per 1k input tokens', input, setInput, 'Uncached prompt tokens')}
            {field('Per 1k output tokens', output, setOutput, 'Generated, reasoning included')}
            {field('Per 1k cached tokens', cached, setCached, 'Prompt tokens served from cache')}
          </div>
          <label className="flex items-center justify-between gap-3 rounded-lg border border-border p-3 text-xs">
            <span>
              <span className="font-medium">Default plan</span>
              <span className="block text-[11px] text-muted-foreground">Used for nodes whose node and cluster have no plan.</span>
            </span>
            <Switch checked={isDefault} onCheckedChange={setIsDefault} />
          </label>
        </div>
        <DialogFooter>
          <Button variant="outline" size="sm" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button size="sm" disabled={invalid || busy} onClick={submit}>
            {busy && <Loader2 className="size-4 animate-spin" />}
            {plan ? 'Save plan' : 'Create plan'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function PlansTab() {
  const plansQuery = usePricePlans({ pageSize: 100 });
  const clusters = useRefClusters();
  const nodes = useRefNodes();
  const attach = useAttachPricePlan();
  const del = useDeletePricePlan();
  const [dialog, setDialog] = useState<{ open: boolean; plan: PricePlan | null }>({ open: false, plan: null });
  const [deleting, setDeleting] = useState<PricePlan | null>(null);
  const plans = plansQuery.data?.items ?? [];
  const clusterList = clusters.data ?? [];
  const nodeList = nodes.data ?? [];
  const planName = (id: string | null | undefined) => plans.find((p) => p.id === id)?.name ?? null;

  if (plansQuery.isError) return <ErrorState title="Could not load price plans" onRetry={() => void plansQuery.refetch()} />;

  return (
    <div className="space-y-6">
      <Card className="rounded-md shadow-premium">
        <CardHeader className="flex flex-row items-center justify-between">
          <div className="space-y-0.5">
            <CardTitle className="text-sm font-semibold">Price plans</CardTitle>
            <CardDescription>USD rates per GPU minute and per 1k tokens.</CardDescription>
          </div>
          <Button size="sm" className="rounded-sm" onClick={() => setDialog({ open: true, plan: null })}>
            <Plus className="size-4" /> New plan
          </Button>
        </CardHeader>
        <CardContent>
          {plansQuery.isLoading ? (
            <Skeleton className="h-40" />
          ) : plans.length === 0 ? (
            <EmptyState icon={Receipt} title="No price plans" description="Create a plan and attach it to a cluster or a node to see costs." />
          ) : (
            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Plan</TableHead>
                    <TableHead className="text-right">GPU minute</TableHead>
                    <TableHead className="text-right">1k input</TableHead>
                    <TableHead className="text-right">1k output</TableHead>
                    <TableHead className="text-right">1k cached</TableHead>
                    <TableHead>Attached to</TableHead>
                    <TableHead className="w-10" />
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {plans.map((p) => {
                    const onClusters = clusterList.filter((c) => c.pricePlanId === p.id);
                    const onNodes = nodeList.filter((n) => n.priceplanId === p.id);
                    return (
                      <TableRow key={p.id}>
                        <TableCell className="max-w-[320px]">
                          <p className="flex items-center gap-2 text-[13px] font-semibold">
                            {p.name}
                            {p.isDefault && (
                              <span className="inline-flex items-center gap-0.5 rounded-full border border-border px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground">
                                <Star className="size-2.5" /> default
                              </span>
                            )}
                          </p>
                          {p.description && <p className="truncate text-[11px] text-muted-foreground">{p.description}</p>}
                        </TableCell>
                        <TableCell className="text-right font-mono text-xs tabular-nums">{rate(p.usdPerGpuMinute)}</TableCell>
                        <TableCell className="text-right font-mono text-xs tabular-nums">{rate(p.usdPer1kInputTokens, 5)}</TableCell>
                        <TableCell className="text-right font-mono text-xs tabular-nums">{rate(p.usdPer1kOutputTokens, 5)}</TableCell>
                        <TableCell className="text-right font-mono text-xs tabular-nums">{rate(p.usdPer1kCachedTokens, 5)}</TableCell>
                        <TableCell>
                          <div className="flex flex-wrap gap-1">
                            {onClusters.map((c) => (
                              <span key={c.id} className="inline-flex h-6 items-center gap-1 rounded-[5px] bg-muted px-2 text-[11px]">
                                <Boxes className="size-3 text-muted-foreground" /> {c.name}
                              </span>
                            ))}
                            {onNodes.map((n) => (
                              <span key={n.id} className="inline-flex h-6 items-center gap-1 rounded-[5px] bg-muted px-2 text-[11px]">
                                <Server className="size-3 text-muted-foreground" /> {n.name}
                              </span>
                            ))}
                            {!onClusters.length && !onNodes.length && <span className="text-[11px] text-muted-foreground">{p.isDefault ? 'fallback for everything else' : 'not attached'}</span>}
                          </div>
                        </TableCell>
                        <TableCell>
                          <DropdownMenu>
                            <DropdownMenuTrigger render={<Button variant="ghost" size="icon-sm" aria-label={`Actions for ${p.name}`} />}>
                              <MoreVertical className="size-4" />
                            </DropdownMenuTrigger>
                            <DropdownMenuContent align="end" className="w-40">
                              <DropdownMenuItem onClick={() => setDialog({ open: true, plan: p })}>
                                <PenLine className="size-4" /> Edit
                              </DropdownMenuItem>
                              <DropdownMenuSeparator />
                              <DropdownMenuItem className="text-destructive" onClick={() => setDeleting(p)}>
                                <Trash2 className="size-4" /> Delete
                              </DropdownMenuItem>
                            </DropdownMenuContent>
                          </DropdownMenu>
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

      <Card className="gap-0 rounded-xl py-0">
        <CardHeader className="border-b border-border px-5 py-4">
          <CardTitle className="text-sm font-semibold">Where each plan applies</CardTitle>
          <CardDescription>A node’s own plan wins over its cluster’s; anything left uses the default plan.</CardDescription>
        </CardHeader>
        <CardContent className="px-5 py-4">
          {clusters.isLoading || nodes.isLoading ? (
            <Skeleton className="h-32" />
          ) : (
            <ul className="space-y-4">
              {clusterList.map((c) => (
                <li key={c.id} className="rounded-lg border border-border">
                  <div className="flex flex-wrap items-center gap-3 border-b border-border px-4 py-2.5">
                    <Boxes className="size-4 text-muted-foreground" />
                    <span className="text-[13px] font-semibold">{c.name}</span>
                    <span className="text-[11px] text-muted-foreground">cluster · {c.type.replace('_', ' ')}</span>
                    <Select value={c.pricePlanId ?? 'none'} onValueChange={(v) => attach.mutate({ kind: 'cluster', id: c.id, pricePlanId: v === 'none' ? null : String(v) })}>
                      <SelectTrigger className="ml-auto w-56" size="sm" aria-label={`Price plan for ${c.name}`}>
                        <SelectValue>{(v: unknown) => (v === 'none' ? 'Default plan' : (planName(String(v)) ?? 'Unknown plan'))}</SelectValue>
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="none">Default plan</SelectItem>
                        {plans.map((p) => (
                          <SelectItem key={p.id} value={p.id}>
                            {p.name}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                  <ul className="divide-y divide-border">
                    {nodeList
                      .filter((n) => n.clusterId === c.id)
                      .map((n) => (
                        <li key={n.id} className="flex flex-wrap items-center gap-3 px-4 py-2 pl-10">
                          <Server className="size-3.5 text-muted-foreground" />
                          <span className="text-xs font-medium">{n.name}</span>
                          <span className="text-[11px] text-muted-foreground">effective: {planName(effectivePlanId(n, clusterList, plans)) ?? 'none'}</span>
                          <Select value={n.priceplanId ?? 'inherit'} onValueChange={(v) => attach.mutate({ kind: 'node', id: n.id, pricePlanId: v === 'inherit' ? null : String(v) })}>
                            <SelectTrigger className="ml-auto w-56" size="sm" aria-label={`Price plan for ${n.name}`}>
                              <SelectValue>{(v: unknown) => (v === 'inherit' ? 'Inherit from cluster' : (planName(String(v)) ?? 'Unknown plan'))}</SelectValue>
                            </SelectTrigger>
                            <SelectContent>
                              <SelectItem value="inherit">Inherit from cluster</SelectItem>
                              {plans.map((p) => (
                                <SelectItem key={p.id} value={p.id}>
                                  {p.name}
                                </SelectItem>
                              ))}
                            </SelectContent>
                          </Select>
                        </li>
                      ))}
                  </ul>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>

      {dialog.open && <PlanDialog key={dialog.plan?.id ?? 'new'} open={dialog.open} onOpenChange={(o) => setDialog((d) => ({ ...d, open: o }))} plan={dialog.plan} />}
      <DeleteConfirmationDialog
        open={!!deleting}
        onOpenChange={(o) => !o && setDeleting(null)}
        itemName={deleting?.name ?? ''}
        resourceType="price plan"
        warningMessage="Past usage keeps the cost it was billed at. A plan still attached to a cluster or node cannot be deleted."
        isDeleting={del.isPending}
        onConfirm={() => {
          if (deleting) del.mutate(deleting.id, { onSuccess: () => setDeleting(null) });
        }}
      />
    </div>
  );
}
