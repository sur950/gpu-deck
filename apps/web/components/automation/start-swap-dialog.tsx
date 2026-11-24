'use client';

import { useMemo, useState } from 'react';
import { useRouter } from 'next/navigation';
import { Loader2, ShieldAlert } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Switch } from '@/components/ui/switch';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { useCreateSwap, useRefDeployments, useRefDevices, useRefNodes } from '@/hooks/api/automation';
import { Segmented } from '@/components/automation/segmented';
import { KIND_META } from '@/components/automation/swap-meta';
import { deviceShortName } from '@/components/automation/layout-model';

type ManualKind = 'load' | 'unload' | 'restart' | 'move';

interface StartSwapDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/** A manual swap: load, unload, restart or move one deployment through the same safe sequence rules use. */
export function StartSwapDialog({ open, onOpenChange }: StartSwapDialogProps) {
  const router = useRouter();
  const deployments = useRefDeployments(open);
  const nodes = useRefNodes(open);
  const devices = useRefDevices(open);
  const createSwap = useCreateSwap();
  const [deploymentId, setDeploymentId] = useState('');
  const [kind, setKind] = useState<ManualKind>('restart');
  const [instanceId, setInstanceId] = useState('');
  const [nodeId, setNodeId] = useState('');
  const [deviceId, setDeviceId] = useState('');
  const [drain, setDrain] = useState(120);
  const [force, setForce] = useState(false);
  const [dryRun, setDryRun] = useState(false);

  const deployment = (deployments.data ?? []).find((d) => d.id === deploymentId);
  const alwaysOn = deployment?.priority === 'always_on';
  const needsTarget = kind === 'move' || kind === 'load';
  const needsInstance = kind !== 'load' && (deployment?.instances.length ?? 0) > 1;
  const nodeDevices = useMemo(() => (devices.data ?? []).filter((d) => d.nodeId === nodeId), [devices.data, nodeId]);
  const valid = !!deployment && (!needsTarget || !!nodeId) && (!(kind === 'unload' && alwaysOn) || force);

  function submit() {
    if (!deployment) return;
    createSwap.mutate(
      {
        deployment_id: deployment.id,
        kind,
        instance_id: kind === 'load' ? null : instanceId || deployment.instances[0]?.id || null,
        node_id: needsTarget ? nodeId : null,
        device_ids: needsTarget && deviceId ? [deviceId] : [],
        drain_timeout_seconds: drain,
        force,
        dry_run: dryRun,
      },
      {
        onSuccess: (swap) => {
          onOpenChange(false);
          router.push(`/swaps/${swap.id}`);
        },
      },
    );
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Start a swap</DialogTitle>
          <DialogDescription>Runs the same safe sequence as rules: stop routing, drain in-flight requests, unload, load, health-check, route back — with rollback on failure.</DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <label className="block space-y-1.5 text-xs font-medium">
            Deployment
            <Select value={deploymentId} onValueChange={(v) => { setDeploymentId(String(v ?? '')); setInstanceId(''); setForce(false); }}>
              <SelectTrigger className="w-full">
                <SelectValue>{(v: unknown) => (deployments.data ?? []).find((d) => d.id === v)?.name ?? <span className="text-muted-foreground">Choose a deployment</span>}</SelectValue>
              </SelectTrigger>
              <SelectContent>
                {(deployments.data ?? []).map((d) => (
                  <SelectItem key={d.id} value={d.id}>
                    {d.name} <span className="ml-2 text-[11px] text-muted-foreground">{d.instances.length} replica{d.instances.length === 1 ? '' : 's'}</span>
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </label>
          <div className="space-y-1.5 text-xs font-medium">
            Operation
            <Segmented
              value={kind}
              onChange={setKind}
              options={(['restart', 'move', 'load', 'unload'] as ManualKind[]).map((k) => ({ value: k, label: KIND_META[k].label, icon: KIND_META[k].icon }))}
              aria-label="Operation"
              className="flex w-full [&>button]:flex-1 [&>button]:justify-center"
            />
          </div>
          {needsInstance && (
            <label className="block space-y-1.5 text-xs font-medium">
              Replica
              <Select value={instanceId || deployment?.instances[0]?.id || ''} onValueChange={(v) => setInstanceId(String(v ?? ''))}>
                <SelectTrigger className="w-full">
                  <SelectValue>{(v: unknown) => { const i = deployment?.instances.find((x) => x.id === v); return i ? `${i.nodeName ?? 'node'} · ${i.state}` : 'Choose'; }}</SelectValue>
                </SelectTrigger>
                <SelectContent>
                  {deployment?.instances.map((i) => (
                    <SelectItem key={i.id} value={i.id}>
                      {i.nodeName ?? i.nodeId.slice(0, 8)} · {i.state}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </label>
          )}
          {needsTarget && (
            <div className="grid gap-3 sm:grid-cols-2">
              <label className="block space-y-1.5 text-xs font-medium">
                {kind === 'move' ? 'Move to node' : 'Load on node'}
                <Select value={nodeId} onValueChange={(v) => { setNodeId(String(v ?? '')); setDeviceId(''); }}>
                  <SelectTrigger className="w-full">
                    <SelectValue>{(v: unknown) => (nodes.data ?? []).find((n) => n.id === v)?.name ?? <span className="text-muted-foreground">Choose a node</span>}</SelectValue>
                  </SelectTrigger>
                  <SelectContent>
                    {(nodes.data ?? []).map((n) => (
                      <SelectItem key={n.id} value={n.id} disabled={n.status !== 'online'}>
                        {n.name}
                        {n.status !== 'online' ? ` (${n.status})` : ''}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </label>
              <label className="block space-y-1.5 text-xs font-medium">
                GPU
                <Select value={deviceId || 'any'} onValueChange={(v) => setDeviceId(v === 'any' ? '' : String(v ?? ''))} disabled={!nodeId}>
                  <SelectTrigger className="w-full">
                    <SelectValue>{(v: unknown) => (v === 'any' ? 'Scheduler picks' : (nodeDevices.find((d) => d.id === v) ? deviceShortName(nodeDevices.find((d) => d.id === v) as (typeof nodeDevices)[number]) : 'Scheduler picks'))}</SelectValue>
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="any">Scheduler picks</SelectItem>
                    {nodeDevices.map((d) => (
                      <SelectItem key={d.id} value={d.id}>
                        {deviceShortName(d)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </label>
            </div>
          )}
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="block space-y-1.5 text-xs font-medium">
              Drain timeout (s)
              <Input type="number" min={0} max={3600} value={drain} onChange={(e) => setDrain(Math.max(0, Math.min(3600, Number(e.target.value) || 0)))} className="h-9 font-mono text-xs" />
            </label>
            <div className="space-y-2 pt-5 text-xs font-medium">
              <span className="flex items-center justify-between">
                Dry run (plan only)
                <Switch checked={dryRun} onCheckedChange={setDryRun} aria-label="Dry run" />
              </span>
            </div>
          </div>
          {kind === 'unload' && alwaysOn && (
            <div className="flex items-start justify-between gap-3 rounded-md border border-warning/30 bg-warning/10 p-3 text-xs text-warning-dark dark:text-warning-light">
              <span className="flex items-start gap-2">
                <ShieldAlert className="mt-px size-3.5 shrink-0" />
                {deployment?.name} is always-on. Unloading it needs force and takes it offline for every client.
              </span>
              <Switch checked={force} onCheckedChange={setForce} aria-label="Force unload" />
            </div>
          )}
        </div>
        <DialogFooter>
          <Button variant="outline" size="sm" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button size="sm" disabled={!valid || createSwap.isPending} onClick={submit}>
            {createSwap.isPending && <Loader2 className="size-4 animate-spin" />}
            {dryRun ? 'Plan swap' : `Start ${KIND_META[kind].label.toLowerCase()}`}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
