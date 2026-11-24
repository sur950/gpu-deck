'use client';

import { useState } from 'react';
import { Trash2 } from 'lucide-react';
import { Checkbox } from '@/components/ui/checkbox';
import { FormDialog } from '@/components/shared/form-dialog';
import { useDeleteModel } from '@/hooks/api/models';
import type { Model } from '@/types/models';

/** Soft-delete a model, optionally evicting its cached files from every node. */
export function DeleteModelDialog({ model, open, onOpenChange, onDeleted }: { model: Model; open: boolean; onOpenChange: (open: boolean) => void; onDeleted?: () => void }) {
  const remove = useDeleteModel();
  const [evict, setEvict] = useState(false);
  const inUse = (model.deployment_count ?? 0) > 0;
  return (
    <FormDialog
      open={open}
      onOpenChange={onOpenChange}
      variant="destructive"
      icon={<Trash2 className="size-4 text-destructive" />}
      title={`Delete ${model.display_name ?? model.name}?`}
      description={
        inUse
          ? `${model.deployment_count} deployment(s) serve this model. Delete them first — the control plane refuses to remove a model that is in use.`
          : 'The model and its versions leave the catalog. Clients calling it get model_not_found. This cannot be undone.'
      }
      submitLabel="Delete model"
      isPending={remove.isPending}
      submitDisabled={inUse}
      onSubmit={() => remove.mutate({ id: model.id, evictCache: evict }, { onSuccess: () => { onOpenChange(false); onDeleted?.(); } })}
    >
      <label className="flex items-start gap-3 rounded-lg border border-border bg-muted/30 p-3 text-xs">
        <Checkbox checked={evict} onCheckedChange={(v) => setEvict(v === true)} className="mt-0.5" disabled={inUse} />
        <span>
          <span className="block font-medium">Also evict cached files from every node</span>
          <span className="text-muted-foreground">Frees disk now. Files referenced in place (Ollama, LM Studio) are never deleted — only GPU Deck&apos;s cache copies.</span>
        </span>
      </label>
    </FormDialog>
  );
}
