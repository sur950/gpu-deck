'use client';

import { useMemo, useState } from 'react';
import { Download } from 'lucide-react';
import { Checkbox } from '@/components/ui/checkbox';
import { Input } from '@/components/ui/input';
import { Switch } from '@/components/ui/switch';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { FormDialog, Field } from '@/components/shared/form-dialog';
import { FormatBadge } from '@/components/models/model-badges';
import { useNodes } from '@/hooks/api/fleet';
import { useModelVersionCache, useModelVersions, useModels } from '@/hooks/api/models';
import { usePrefetchModel } from '@/hooks/api/models-cache';
import { cn } from '@/lib/utils';
import { formatBytes } from '@/utils/format';

interface PrefetchDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  preset?: { modelId?: string; versionId?: string; nodeIds?: string[] };
}

/**
 * Download a model version into the cache of one or more nodes ahead of
 * time, so a later load or swap only pays load time. Pinned files are never
 * auto-evicted.
 */
export function PrefetchDialog({ open, onOpenChange, preset }: PrefetchDialogProps) {
  const models = useModels({ page_size: 500 });
  const nodes = useNodes({ page_size: 500 }, { live: false });
  const prefetch = usePrefetchModel();
  const [modelId, setModelId] = useState(preset?.modelId ?? '');
  const [versionId, setVersionId] = useState(preset?.versionId ?? '');
  const [nodeIds, setNodeIds] = useState<string[]>(preset?.nodeIds ?? []);
  const [pin, setPin] = useState(false);
  const [reason, setReason] = useState('');
  const versions = useModelVersions(modelId);
  const cached = useModelVersionCache(modelId, versionId);

  const model = models.data?.items.find((m) => m.id === modelId);
  const versionList = versions.data?.items ?? [];
  const version = versionList.find((v) => v.id === versionId) ?? (versionId ? undefined : versionList.find((v) => v.is_default));
  const effectiveVersion = version?.id ?? '';
  const cachedOn = useMemo(() => {
    const out = new Map<string, number>();
    for (const item of cached.data?.items ?? []) if (item.state === 'ready' || item.state === 'downloading') out.set(item.node_id, (out.get(item.node_id) ?? 0) + 1);
    return out;
  }, [cached.data]);
  const eligibleNodes = (nodes.data?.items ?? []).filter((n) => n.status !== 'removed');
  const size = version?.total_size_bytes ?? 0;

  return (
    <FormDialog
      open={open}
      onOpenChange={onOpenChange}
      icon={<Download className="size-4 text-muted-foreground" />}
      title="Prefetch to nodes"
      description="The agents download the files (resumable, hash-checked) in the background. Nothing is loaded onto a GPU."
      submitLabel={nodeIds.length > 1 ? `Prefetch to ${nodeIds.length} nodes` : 'Prefetch'}
      widthClass="sm:max-w-xl"
      isPending={prefetch.isPending}
      submitDisabled={!effectiveVersion || nodeIds.length === 0 || version?.status === 'blocked'}
      footerNote={size ? `${formatBytes(size)} per node` : undefined}
      onSubmit={() =>
        prefetch.mutate(
          { model_version_id: effectiveVersion, node_ids: nodeIds, pin, pin_reason: pin ? reason.trim() || null : null },
          { onSuccess: () => onOpenChange(false) },
        )
      }
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Model">
          <Select value={modelId || null} onValueChange={(v) => { setModelId(String(v ?? '')); setVersionId(''); }}>
            <SelectTrigger className="w-full">
              <SelectValue placeholder="Pick a model">{(v: unknown) => { const m = models.data?.items.find((x) => x.id === v); return m ? m.display_name ?? m.name : 'Pick a model'; }}</SelectValue>
            </SelectTrigger>
            <SelectContent>
              {(models.data?.items ?? []).map((m) => (
                <SelectItem key={m.id} value={m.id}>
                  {m.display_name ?? m.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </Field>
        <Field label="Version">
          <Select value={effectiveVersion || null} onValueChange={(v) => setVersionId(String(v ?? ''))} disabled={!model}>
            <SelectTrigger className="w-full">
              <SelectValue placeholder="Default version">{(v: unknown) => { const x = versionList.find((y) => y.id === v); return x ? `${x.version}${x.is_default ? ' (default)' : ''}` : 'Default version'; }}</SelectValue>
            </SelectTrigger>
            <SelectContent>
              {versionList.map((v) => (
                <SelectItem key={v.id} value={v.id} disabled={v.status === 'blocked'}>
                  {v.version} · {formatBytes(v.total_size_bytes)}
                  {v.is_default ? ' · default' : ''}
                  {v.status === 'blocked' ? ' · blocked' : ''}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </Field>
      </div>
      {version && (
        <div className="flex items-center gap-2 text-[11px] text-muted-foreground">
          <FormatBadge format={version.format} />
          {version.file_count ?? 0} files · {formatBytes(version.total_size_bytes)}
          {version.quantization ? ` · ${version.quantization}` : ''}
        </div>
      )}
      <Field label="Nodes" hint="Free space is the node's cache limit minus what is already cached.">
        <div className="max-h-56 divide-y divide-border overflow-y-auto rounded-lg border border-border">
          {eligibleNodes.map((n) => {
            const already = cachedOn.get(n.id);
            const free = n.cache_limit_bytes ? Math.max(0, n.cache_limit_bytes - (n.cache_used_bytes ?? 0)) : null;
            const tooBig = free !== null && size > free;
            const checked = nodeIds.includes(n.id);
            return (
              <label key={n.id} className={cn('flex cursor-pointer items-center gap-3 px-3 py-2 text-xs hover:bg-muted/40', checked && 'bg-muted/40')}>
                <Checkbox checked={checked} onCheckedChange={(v) => setNodeIds((ids) => (v === true ? [...ids, n.id] : ids.filter((x) => x !== n.id)))} />
                <span className="min-w-0 flex-1">
                  <span className="font-medium">{n.name}</span>
                  <span className="ml-2 text-muted-foreground">{n.os === 'macos' ? 'macOS' : 'Linux'} · {n.status}</span>
                </span>
                {already ? <span className="text-[11px] text-success-dark dark:text-success-light">already cached</span> : null}
                <span className={cn('font-mono text-[11px] tabular-nums', tooBig ? 'text-error' : 'text-muted-foreground')}>{free === null ? 'no limit' : `${formatBytes(free)} free`}</span>
              </label>
            );
          })}
        </div>
      </Field>
      <label className="flex items-start justify-between gap-4 rounded-lg border border-border bg-muted/30 p-3">
        <span className="space-y-0.5">
          <span className="block text-xs font-medium">Pin after download</span>
          <span className="block text-[11px] text-muted-foreground">Eviction (rules or by last use) skips pinned files — use it for layouts you will switch to later.</span>
        </span>
        <Switch checked={pin} onCheckedChange={setPin} aria-label="Pin after download" />
      </label>
      {pin && (
        <Field label="Pin reason" htmlFor="pin-reason">
          <Input id="pin-reason" value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Night layout" />
        </Field>
      )}
    </FormDialog>
  );
}
