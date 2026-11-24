'use client';

import { useMemo, useState } from 'react';
import { GitBranchPlus } from 'lucide-react';
import { Checkbox } from '@/components/ui/checkbox';
import { Input } from '@/components/ui/input';
import { Switch } from '@/components/ui/switch';
import { FormDialog, Field } from '@/components/shared/form-dialog';
import { FormatBadge } from '@/components/models/model-badges';
import { useAddModelVersion, useRemoteFiles } from '@/hooks/api/models';
import { cn } from '@/lib/utils';
import { formatBytes } from '@/utils/format';
import type { Model, ModelVersion } from '@/types/models';

function quantOf(path: string): string | null {
  return /(IQ\d_[A-Z]+|Q\d_K_[SML]|Q\d_K|Q\d_\d|F16|BF16|F32)/i.exec(path)?.[1]?.toUpperCase() ?? null;
}

/** Add a version: another quantisation of a remote repo, a pinned revision, or another local path. */
export function AddVersionDialog({ model, existing, open, onOpenChange }: { model: Model; existing: ModelVersion[]; open: boolean; onOpenChange: (open: boolean) => void }) {
  const remote = model.source !== 'local_path';
  const listing = useRemoteFiles(remote ? (model.source as 'huggingface' | 'modelscope') : 'huggingface', remote ? model.repo_id : '');
  const add = useAddModelVersion();
  const [version, setVersion] = useState('');
  const [revision, setRevision] = useState('');
  const [localPath, setLocalPath] = useState(remote ? '' : model.repo_id);
  const [files, setFiles] = useState<string[]>([]);
  const [isDefault, setIsDefault] = useState(false);
  const weightFiles = useMemo(() => (listing.data?.files ?? []).filter((f) => f.format !== 'other'), [listing.data]);
  const gguf = weightFiles.filter((f) => f.format === 'gguf');
  const taken = new Set(existing.map((v) => v.version));
  const label = version || (files.length === 1 ? quantOf(files[0]) ?? '' : '');
  const chosenSize = weightFiles.filter((f) => files.includes(f.path)).reduce((s, f) => s + f.size_bytes, 0);
  const valid = !!label && !taken.has(label) && (remote ? true : !!localPath.trim());

  return (
    <FormDialog
      open={open}
      onOpenChange={onOpenChange}
      icon={<GitBranchPlus className="size-4 text-muted-foreground" />}
      title={`Add a version of ${model.display_name ?? model.name}`}
      description={remote ? 'Pick another quantisation or pin a revision. Files are hashed now and downloaded to nodes on demand.' : 'Point at another directory or file on the same host.'}
      submitLabel="Add version"
      widthClass="sm:max-w-xl"
      isPending={add.isPending}
      submitDisabled={!valid}
      footerNote={files.length ? `${files.length} file(s) · ${formatBytes(chosenSize)}` : remote ? 'All weight files' : undefined}
      onSubmit={() =>
        add.mutate(
          {
            modelId: model.id,
            body: {
              version: label,
              revision: revision.trim() || null,
              files,
              is_default: isDefault,
              quantization: files.length === 1 ? quantOf(files[0]) : null,
              local_path: remote ? null : localPath.trim(),
            },
          },
          { onSuccess: () => onOpenChange(false) },
        )
      }
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Version label" hint={taken.has(label) ? 'A version with this label exists.' : 'e.g. Q5_K_M, main, 2025-06'} htmlFor="ver-label">
          <Input id="ver-label" value={version} onChange={(e) => setVersion(e.target.value)} placeholder={label || 'Q5_K_M'} className="font-mono text-sm" aria-invalid={taken.has(label)} />
        </Field>
        {remote ? (
          <Field label="Revision" hint="Commit sha; empty = latest." htmlFor="ver-rev">
            <Input id="ver-rev" value={revision} onChange={(e) => setRevision(e.target.value)} placeholder={listing.data?.revision ?? 'main'} className="font-mono text-sm" />
          </Field>
        ) : (
          <Field label="Local path" htmlFor="ver-path">
            <Input id="ver-path" value={localPath} onChange={(e) => setLocalPath(e.target.value)} className="font-mono text-sm" />
          </Field>
        )}
      </div>
      {remote && (
        <Field label={gguf.length > 1 ? 'Quantisation file' : 'Weight files'} hint={gguf.length > 1 ? 'Pick one GGUF file for this version.' : 'Empty selection = every weight file.'}>
          <div className="max-h-48 divide-y divide-border overflow-y-auto rounded-lg border border-border">
            {listing.isLoading && <p className="p-3 text-xs text-muted-foreground">Listing repository files…</p>}
            {weightFiles.map((f) => {
              const checked = files.includes(f.path);
              const exists = existing.some((v) => v.quantization && quantOf(f.path) === v.quantization?.toUpperCase());
              return (
                <label key={f.path} className={cn('flex cursor-pointer items-center gap-3 px-3 py-2 text-xs hover:bg-muted/40', checked && 'bg-muted/40')}>
                  <Checkbox
                    checked={checked}
                    onCheckedChange={(v) => setFiles((list) => (gguf.length > 1 ? (v === true ? [f.path] : []) : v === true ? [...list, f.path] : list.filter((p) => p !== f.path)))}
                  />
                  <span className="min-w-0 flex-1 truncate font-mono text-[11px]">{f.path}</span>
                  {exists && <span className="text-[10.5px] text-muted-foreground">registered</span>}
                  <FormatBadge format={f.format} />
                  <span className="w-16 text-right font-mono text-[11px] text-muted-foreground">{formatBytes(f.size_bytes)}</span>
                </label>
              );
            })}
          </div>
        </Field>
      )}
      <label className="flex items-center justify-between gap-4 rounded-lg border border-border bg-muted/30 p-3 text-xs">
        <span>
          <span className="block font-medium">Make it the default version</span>
          <span className="text-muted-foreground">Deployments without a pinned version switch to it on their next rolling restart.</span>
        </span>
        <Switch checked={isDefault} onCheckedChange={setIsDefault} aria-label="Default version" />
      </label>
    </FormDialog>
  );
}
