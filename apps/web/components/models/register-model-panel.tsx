'use client';

import { useMemo, useState } from 'react';
import { useRouter } from 'next/navigation';
import { FileWarning, Loader2, PackagePlus, ShieldAlert, ShieldCheck } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Checkbox } from '@/components/ui/checkbox';
import { Input } from '@/components/ui/input';
import { Switch } from '@/components/ui/switch';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Field } from '@/components/shared/form-dialog';
import { FormatBadge, MODEL_TYPE_ICONS, SourceBadge } from '@/components/models/model-badges';
import { useRegisterModel } from '@/hooks/api/models';
import { cn } from '@/lib/utils';
import { formatBytes } from '@/utils/format';
import {
  MODEL_PRIORITY_DESCRIPTIONS,
  MODEL_PRIORITY_LABELS,
  MODEL_PRIORITY_OPTIONS,
  MODEL_TYPE_OPTIONS,
  type CatalogRemoteFile,
  type LocalScanCandidate,
  type ModelPriority,
  type ModelSource,
  type ModelType,
} from '@/types/models';

export interface RegisterDraft {
  source: ModelSource;
  /** org/name for remote sources, absolute path for local ones. */
  repoId: string;
  revision?: string | null;
  files: CatalogRemoteFile[];
  totalSize: number;
  hasPickleOnly: boolean;
  suggested: {
    name?: string;
    type?: ModelType | null;
    family?: string | null;
    context_size?: number | null;
    tool_parser?: string | null;
    quantization?: string | null;
  };
  localKind?: LocalScanCandidate['kind'];
  nodeId?: string | null;
  /** Vault token credential used to list the repo; stored on the model for node downloads. */
  credentialId?: string | null;
}

const KIND_LABEL: Record<LocalScanCandidate['kind'], string> = {
  plain: 'Plain files',
  ollama_blob: 'Ollama blob (referenced in place)',
  lmstudio: 'LM Studio (referenced in place)',
  hf_cache: 'Hugging Face cache',
  mlx_dir: 'MLX directory',
};

const PARSER_HINTS = ['jinja', 'hermes', 'llama3_json', 'mistral', 'qwen25', 'deepseek_v3'];

function quantOf(path: string): string | null {
  return /(IQ\d_[A-Z]+|Q\d_K_[SML]|Q\d_K|Q\d_\d|F16|BF16|F32)/i.exec(path)?.[1]?.toUpperCase() ?? null;
}

/**
 * Register panel shared by the Hugging Face / ModelScope search and the local
 * path scan: pick the files (one quantisation for GGUF repos), name the model
 * for the serving API, set its priority and security flags.
 */
export function RegisterModelPanel({ draft }: { draft: RegisterDraft }) {
  const router = useRouter();
  const register = useRegisterModel();
  const ggufFiles = draft.files.filter((f) => f.format === 'gguf');
  const multiQuant = draft.source !== 'local_path' && ggufFiles.length > 1;
  const preferredQuant = ggufFiles.find((f) => /Q4_K_M/i.test(f.path)) ?? ggufFiles.find((f) => /Q8_0/i.test(f.path)) ?? ggufFiles[0];

  const [quantFile, setQuantFile] = useState(preferredQuant?.path ?? '');
  const [selected, setSelected] = useState<string[]>(() => draft.files.filter((f) => f.format !== 'pickle').map((f) => f.path));
  const [name, setName] = useState((draft.suggested.name ?? draft.repoId.split('/').pop() ?? 'model').toLowerCase().replace(/[^a-z0-9._-]+/g, '-'));
  const [displayName, setDisplayName] = useState('');
  const [type, setType] = useState<ModelType>(draft.suggested.type ?? 'chat');
  const [family, setFamily] = useState(draft.suggested.family ?? '');
  const [priority, setPriority] = useState<ModelPriority>('normal');
  const [parser, setParser] = useState(draft.suggested.tool_parser ?? '');
  const [context, setContext] = useState(draft.suggested.context_size ? String(draft.suggested.context_size) : '');
  const [trustRemoteCode, setTrustRemoteCode] = useState(false);
  const [allowPickle, setAllowPickle] = useState(false);
  const [versionLabel, setVersionLabel] = useState(draft.suggested.quantization ?? (multiQuant ? quantOf(preferredQuant?.path ?? '') ?? 'main' : draft.source === 'local_path' ? 'local' : 'main'));

  const chosenPaths = useMemo(() => {
    if (multiQuant) return [quantFile, ...draft.files.filter((f) => f.format === 'other' && f.is_required).map((f) => f.path)].filter(Boolean);
    return selected.filter((p) => allowPickle || draft.files.find((f) => f.path === p)?.format !== 'pickle');
  }, [multiQuant, quantFile, selected, allowPickle, draft.files]);
  const chosenSize = draft.files.filter((f) => chosenPaths.includes(f.path)).reduce((s, f) => s + f.size_bytes, 0);
  const nameValid = /^[a-z0-9][a-z0-9._-]{0,127}$/.test(name);
  const pickleSelected = draft.files.some((f) => f.format === 'pickle' && chosenPaths.includes(f.path));
  const TypeIcon = MODEL_TYPE_ICONS[type];

  function submit() {
    const allSelected = !multiQuant && chosenPaths.length === draft.files.length;
    register.mutate(
      {
        name,
        display_name: displayName.trim() || null,
        source: draft.source,
        repo_id: draft.repoId,
        type,
        family: family.trim() || null,
        priority,
        tool_parser_default: parser.trim() || null,
        default_context_size: context ? Number(context) : null,
        trust_remote_code: trustRemoteCode,
        allow_pickle: allowPickle,
        tags: draft.localKind ? [draft.localKind] : [],
        aliases: [],
        // A path scanned on a node is resolved, scanned and hashed there (and deployments pinned to it).
        metadata: {
          ...(draft.credentialId ? { credential_id: draft.credentialId } : {}),
          ...(draft.nodeId ? { node_id: draft.nodeId } : {}),
        },
        initial_version: {
          version: versionLabel.trim() || 'main',
          revision: draft.revision ?? null,
          is_default: true,
          quantization: multiQuant ? quantOf(quantFile) : (draft.suggested.quantization ?? null),
          files: allSelected ? [] : chosenPaths,
          local_path: draft.source === 'local_path' ? draft.repoId : null,
        },
      },
      { onSuccess: (model) => router.push(`/catalog/${model.id}`) },
    );
  }

  return (
    <Card className="rounded-md shadow-premium">
      <CardHeader className="gap-2">
        <div className="flex items-center gap-2">
          <SourceBadge source={draft.source} />
          {draft.localKind && <span className="text-[11px] text-muted-foreground">· {KIND_LABEL[draft.localKind]}</span>}
        </div>
        <CardTitle className="font-mono text-[13px] break-all">{draft.repoId}</CardTitle>
        <CardDescription>
          {draft.files.length} file{draft.files.length === 1 ? '' : 's'} · {formatBytes(draft.totalSize)}
          {draft.revision ? <> · revision <span className="font-mono">{draft.revision}</span></> : null}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        {draft.hasPickleOnly && (
          <div className="flex gap-2.5 rounded-lg border border-error/30 bg-error/10 p-3 text-xs">
            <FileWarning className="mt-0.5 size-4 shrink-0 text-error" />
            <span>
              <span className="block font-medium text-error-dark dark:text-error-light">Only pickle weights</span>
              <span className="text-muted-foreground">Pickle files can run arbitrary code when loaded. The version stays blocked unless you allow pickle below.</span>
            </span>
          </div>
        )}

        <Field label={multiQuant ? 'Quantisation' : 'Files'} hint={multiQuant ? 'One GGUF file per version — add other quantisations later as extra versions.' : 'Unselected files are not downloaded to nodes.'}>
          <div className="max-h-52 divide-y divide-border overflow-y-auto rounded-lg border border-border">
            {(multiQuant ? ggufFiles : draft.files).map((f) => {
              const checked = multiQuant ? quantFile === f.path : chosenPaths.includes(f.path);
              const blocked = f.format === 'pickle' && !allowPickle;
              return (
                <label key={f.path} className={cn('flex cursor-pointer items-center gap-3 px-3 py-2 text-xs hover:bg-muted/40', checked && 'bg-muted/40', blocked && 'cursor-not-allowed opacity-60')}>
                  {multiQuant ? (
                    <input type="radio" name="quant" checked={checked} onChange={() => setQuantFile(f.path)} className="accent-foreground" />
                  ) : (
                    <Checkbox
                      checked={checked}
                      disabled={blocked || (f.is_required && f.format === 'other')}
                      onCheckedChange={(v) => setSelected((list) => (v === true ? [...list, f.path] : list.filter((p) => p !== f.path)))}
                    />
                  )}
                  <span className="min-w-0 flex-1 truncate font-mono text-[11px]" title={f.path}>
                    {f.path}
                  </span>
                  <FormatBadge format={f.format} />
                  <span className="w-16 text-right font-mono text-[11px] text-muted-foreground tabular-nums">{formatBytes(f.size_bytes)}</span>
                </label>
              );
            })}
          </div>
        </Field>

        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Serving name" hint={nameValid ? 'Clients send this as "model".' : 'Lowercase letters, digits, dots, dashes, underscores.'} htmlFor="reg-name">
            <Input id="reg-name" value={name} onChange={(e) => setName(e.target.value.toLowerCase())} className="font-mono text-sm" aria-invalid={!nameValid} />
          </Field>
          <Field label="Display name" htmlFor="reg-display">
            <Input id="reg-display" value={displayName} onChange={(e) => setDisplayName(e.target.value)} placeholder="Shown in the UI" />
          </Field>
          <Field label="Type">
            <Select value={type} onValueChange={(v) => setType(v as ModelType)}>
              <SelectTrigger className="w-full">
                <SelectValue>
                  {(v: unknown) => (
                    <span className="flex items-center gap-1.5">
                      <TypeIcon className="size-3.5 text-muted-foreground" />
                      {MODEL_TYPE_OPTIONS.find((o) => o.value === v)?.label}
                    </span>
                  )}
                </SelectValue>
              </SelectTrigger>
              <SelectContent>
                {MODEL_TYPE_OPTIONS.map((o) => (
                  <SelectItem key={o.value} value={o.value}>
                    {o.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>
          <Field label="Family" hint="Drives default tool parsers per engine." htmlFor="reg-family">
            <Input id="reg-family" value={family} onChange={(e) => setFamily(e.target.value)} placeholder="qwen3" className="font-mono text-sm" />
          </Field>
          <Field label="Version label" htmlFor="reg-version">
            <Input id="reg-version" value={versionLabel} onChange={(e) => setVersionLabel(e.target.value)} className="font-mono text-sm" />
          </Field>
          <Field label="Default context" hint="Tokens; deployments may override." htmlFor="reg-ctx">
            <Input id="reg-ctx" type="number" min={256} step={256} value={context} onChange={(e) => setContext(e.target.value)} placeholder="8192" className="font-mono text-sm" />
          </Field>
        </div>

        <Field label="Default tool parser" hint="Per-engine values: --jinja for llama.cpp, the tool-call parser for vLLM / SGLang." htmlFor="reg-parser">
          <Input id="reg-parser" value={parser} onChange={(e) => setParser(e.target.value)} placeholder="hermes" className="font-mono text-sm" />
          <div className="flex flex-wrap gap-1 pt-1">
            {PARSER_HINTS.map((p) => (
              <button key={p} type="button" onClick={() => setParser(p)} className={cn('rounded-sm border px-1.5 py-0.5 font-mono text-[10.5px] transition-colors', parser === p ? 'border-foreground/50 bg-muted text-foreground' : 'border-border text-muted-foreground hover:text-foreground')}>
                {p}
              </button>
            ))}
          </div>
        </Field>

        <Field label="Priority">
          <div className="grid gap-2 sm:grid-cols-3" role="radiogroup" aria-label="Priority">
            {MODEL_PRIORITY_OPTIONS.map((o) => (
              <button
                key={o.value}
                type="button"
                role="radio"
                aria-checked={priority === o.value}
                onClick={() => setPriority(o.value)}
                className={cn('rounded-lg border p-2.5 text-left transition-colors', priority === o.value ? 'border-foreground/60 bg-muted/40 ring-1 ring-foreground/50' : 'border-border hover:border-foreground/25')}
              >
                <span className="block text-xs font-medium">{MODEL_PRIORITY_LABELS[o.value]}</span>
                <span className="block text-[10.5px] leading-snug text-muted-foreground">{MODEL_PRIORITY_DESCRIPTIONS[o.value]}</span>
              </button>
            ))}
          </div>
        </Field>

        <div className="space-y-2 rounded-lg border border-border p-3">
          <div className="flex items-center gap-1.5 text-xs font-medium">
            {trustRemoteCode || allowPickle ? <ShieldAlert className="size-3.5 text-warning" /> : <ShieldCheck className="size-3.5 text-success" />}
            Security
          </div>
          <label className="flex items-start justify-between gap-4">
            <span>
              <span className="block text-xs">Trust remote code</span>
              <span className="block text-[11px] text-muted-foreground">Runs Python shipped in the repo (custom architectures). Off by default.</span>
            </span>
            <Switch checked={trustRemoteCode} onCheckedChange={setTrustRemoteCode} aria-label="Trust remote code" />
          </label>
          <label className="flex items-start justify-between gap-4">
            <span>
              <span className="block text-xs">Allow pickle weights</span>
              <span className="block text-[11px] text-muted-foreground">.bin / .pt files execute code on load. Prefer safetensors or GGUF.</span>
            </span>
            <Switch checked={allowPickle} onCheckedChange={setAllowPickle} aria-label="Allow pickle" />
          </label>
        </div>

        <div className="flex items-center justify-between gap-3 border-t border-border pt-4">
          <span className="text-[11px] text-muted-foreground tabular-nums">
            {chosenPaths.length} file{chosenPaths.length === 1 ? '' : 's'} · {formatBytes(chosenSize)}
            {pickleSelected ? ' · includes pickle' : ''}
          </span>
          <Button size="sm" className="rounded-sm" disabled={!nameValid || chosenPaths.length === 0 || register.isPending} onClick={submit}>
            {register.isPending ? <Loader2 className="size-4 animate-spin" /> : <PackagePlus className="size-4" />}
            Register model
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
