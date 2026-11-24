'use client';

import { useState } from 'react';
import { Power, Square } from 'lucide-react';
import { Checkbox } from '@/components/ui/checkbox';
import { Input } from '@/components/ui/input';
import { FormDialog, Field } from '@/components/shared/form-dialog';
import { useCloudNodeAction } from '@/hooks/use-cloud';
import type { CloudNode } from '@/types/cloud';

export type CloudDialogAction = 'stop' | 'terminate';

/**
 * Stop or terminate a cloud VM. By default the node is drained first (its
 * model instances move to other GPUs); `force` skips the drain.
 */
export function CloudActionDialog({ vm, action, open, onOpenChange }: { vm: CloudNode; action: CloudDialogAction; open: boolean; onOpenChange: (open: boolean) => void }) {
  const mutation = useCloudNodeAction();
  const [force, setForce] = useState(false);
  const [timeout, setTimeoutValue] = useState('');
  const online = vm.node_status === 'online';
  const terminate = action === 'terminate';
  return (
    <FormDialog
      open={open}
      onOpenChange={onOpenChange}
      icon={terminate ? <Power className="size-4 text-destructive" /> : <Square className="size-4 text-muted-foreground" />}
      title={`${terminate ? 'Terminate' : 'Stop'} ${vm.name}?`}
      description={
        terminate
          ? 'The VM and everything tagged gpu-deck:* that it owns are deleted, the node is removed (certificates revoked) and an unused join token is revoked. This cannot be undone.'
          : 'The VM is stopped (disks kept, compute billing stops on most providers). Start it again later and the agent reconnects.'
      }
      submitLabel={terminate ? 'Terminate VM' : 'Stop VM'}
      variant={terminate ? 'destructive' : 'default'}
      isPending={mutation.isPending}
      onSubmit={() =>
        mutation.mutate(
          { id: vm.id, action, body: { force, reason: 'admin', drain_timeout_seconds: timeout ? Number(timeout) : null } },
          { onSuccess: () => onOpenChange(false) },
        )
      }
    >
      {online ? (
        <>
          <p className="rounded-md border border-border bg-muted/40 p-3 text-xs text-muted-foreground">
            <span className="font-medium text-foreground">{vm.node_name ?? 'The node'}</span> is online. It is drained first: new requests stop, model instances move to other GPUs, then the VM is {terminate ? 'terminated' : 'stopped'}.
          </p>
          <label className="flex items-start gap-2.5 text-xs">
            <Checkbox checked={force} onCheckedChange={(v) => setForce(v === true)} aria-label="Skip the drain" />
            <span>
              <span className="font-medium">Skip the drain</span>
              <span className="block text-[11px] text-muted-foreground">In-flight requests on this node fail.</span>
            </span>
          </label>
          {!force && (
            <Field label="Drain timeout (seconds)" hint="Default CLOUD_DRAIN_TIMEOUT_SECONDS; the VM is stopped when it runs out." htmlFor="drain-timeout">
              <Input id="drain-timeout" type="number" min={0} value={timeout} onChange={(e) => setTimeoutValue(e.target.value)} placeholder="server default" className="font-mono" />
            </Field>
          )}
        </>
      ) : (
        <p className="text-xs text-muted-foreground">The node is not online, so there is nothing to drain.</p>
      )}
    </FormDialog>
  );
}
