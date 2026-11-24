'use client';

import { Gauge, Plus, Trash2, Zap } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Switch } from '@/components/ui/switch';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Field } from '@/components/shared/form-dialog';
import { KeyValueEditor } from '@/components/shared/key-value-editor';
import { type KvBackend, type PdTransfer, type SpecMethod, type TrafficStrategyName } from '@/components/models/wizard/draft';
import type { StepProps } from '@/components/models/wizard/context';
import { cn } from '@/lib/utils';
import { MODEL_PRIORITY_LABELS, PRESET_DESCRIPTIONS, PRESET_LABELS, toolParserOptions, type DeploymentPreset, type ModelPriority } from '@/types/models';

function OptionCard({ title, body, enabled, onToggle, disabledReason, children }: { title: string; body: string; enabled: boolean; onToggle: (v: boolean) => void; disabledReason?: string | null; children?: React.ReactNode }) {
  const disabled = !!disabledReason;
  return (
    <div className={cn('rounded-lg border p-3 transition-colors', enabled ? 'border-foreground/40 bg-muted/30' : 'border-border', disabled && 'opacity-60')}>
      <label className="flex items-start justify-between gap-4">
        <span>
          <span className="block text-xs font-medium">{title}</span>
          <span className="block text-[11px] leading-snug text-muted-foreground">{disabled ? disabledReason : body}</span>
        </span>
        <Switch checked={enabled} disabled={disabled} onCheckedChange={onToggle} aria-label={title} />
      </label>
      {enabled && children && <div className="mt-3 grid gap-3 border-t border-border pt-3 sm:grid-cols-3">{children}</div>}
    </div>
  );
}

export function StepPerformance({ draft, update, ctx }: StepProps) {
  const engineName = ctx.engine?.display_name ?? 'This engine';
  const draftCandidates = ctx.models.filter((m) => m.id !== draft.modelId && m.family && m.family === ctx.model?.family && m.default_version);
  return (
    <div className="space-y-5">
      <Field label="Preset" hint="Translated into engine flags at launch (batch size, scheduling, prefill chunking). Flags you set in the next step win.">
        <div className="grid gap-2 sm:grid-cols-3" role="radiogroup" aria-label="Preset">
          {([...Object.keys(PRESET_LABELS), 'none'] as (DeploymentPreset | 'none')[]).map((p) => {
            const selected = (draft.preset ?? 'none') === p;
            const Icon = p === 'low_latency' ? Zap : p === 'high_throughput' ? Gauge : Plus;
            return (
              <button
                key={p}
                type="button"
                role="radio"
                aria-checked={selected}
                onClick={() => update({ preset: p === 'none' ? null : p })}
                className={cn('flex gap-3 rounded-lg border p-3 text-left transition-colors', selected ? 'border-foreground/60 bg-muted/40 ring-1 ring-foreground/50' : 'border-border hover:border-foreground/25')}
              >
                <span className="flex size-8 shrink-0 items-center justify-center rounded-md border border-border bg-background">
                  <Icon className="size-4 text-muted-foreground" />
                </span>
                <span>
                  <span className="block text-xs font-medium">{p === 'none' ? 'Custom' : PRESET_LABELS[p]}</span>
                  <span className="block text-[11px] leading-snug text-muted-foreground">{p === 'none' ? 'No preset; only the flags you set.' : PRESET_DESCRIPTIONS[p]}</span>
                </span>
              </button>
            );
          })}
        </div>
      </Field>

      <Field label="Context size" hint={`Tokens per request. KV cache is sized for it — larger contexts need more VRAM. Model default: ${ctx.model?.default_context_size ?? 'n/a'}.`} htmlFor="wz-ctx">
        <Input id="wz-ctx" type="number" min={256} step={256} value={draft.contextSize} onChange={(e) => update({ contextSize: e.target.value })} placeholder={String(ctx.model?.default_context_size ?? 8192)} className="w-40 font-mono text-sm" />
      </Field>

      <OptionCard
        title="Prefill / decode split"
        body="Run prompt processing and token generation on separate instances, connected by a KV transfer. Better tail latency under mixed load."
        enabled={draft.pdSplit}
        onToggle={(v) => update({ pdSplit: v })}
        disabledReason={ctx.caps.pdSplit ? null : `${engineName} does not support PD disaggregation (vLLM, SGLang and TensorRT-LLM do).`}
      >
        <Field label="Prefill replicas">
          <Input type="number" min={1} value={draft.prefillReplicas} onChange={(e) => update({ prefillReplicas: Math.max(1, Number(e.target.value)) })} className="h-9 font-mono text-sm" />
        </Field>
        <Field label="Decode replicas">
          <Input type="number" min={1} value={draft.decodeReplicas} onChange={(e) => update({ decodeReplicas: Math.max(1, Number(e.target.value)) })} className="h-9 font-mono text-sm" />
        </Field>
        <Field label="KV transfer">
          <Select value={draft.pdTransfer} onValueChange={(v) => update({ pdTransfer: v as PdTransfer })}>
            <SelectTrigger className="w-full" size="sm">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="nixl">NIXL</SelectItem>
              <SelectItem value="mooncake">Mooncake</SelectItem>
            </SelectContent>
          </Select>
        </Field>
      </OptionCard>

      <OptionCard
        title="Speculative decoding"
        body="A cheap drafter proposes tokens that the model verifies in one pass — faster generation at the same output."
        enabled={draft.specEnabled}
        onToggle={(v) => update({ specEnabled: v })}
        disabledReason={ctx.caps.specDecoding ? null : `${engineName} has no speculative decoding.`}
      >
        <Field label="Method">
          <Select value={draft.specMethod} onValueChange={(v) => update({ specMethod: v as SpecMethod })}>
            <SelectTrigger className="w-full" size="sm">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="ngram">n-gram (prompt lookup)</SelectItem>
              <SelectItem value="draft_model">Draft model</SelectItem>
              <SelectItem value="eagle">EAGLE head</SelectItem>
              <SelectItem value="mtp">MTP (built-in)</SelectItem>
            </SelectContent>
          </Select>
        </Field>
        <Field label="Draft tokens">
          <Input type="number" min={1} max={16} value={draft.numDraftTokens} onChange={(e) => update({ numDraftTokens: Math.min(16, Math.max(1, Number(e.target.value))) })} className="h-9 font-mono text-sm" />
        </Field>
        {draft.specMethod === 'draft_model' && (
          <Field label="Draft model" hint={draftCandidates.length ? 'Same family, so the tokenizer matches.' : 'Register a smaller model of the same family first.'}>
            <Select value={draft.draftVersionId || null} onValueChange={(v) => update({ draftVersionId: String(v ?? '') })}>
              <SelectTrigger className="w-full" size="sm">
                <SelectValue placeholder="Pick a drafter">{(v: unknown) => draftCandidates.find((m) => m.default_version?.id === v)?.name ?? 'Pick a drafter'}</SelectValue>
              </SelectTrigger>
              <SelectContent>
                {draftCandidates.map((m) => (
                  <SelectItem key={m.id} value={m.default_version?.id ?? m.id}>
                    {m.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>
        )}
      </OptionCard>

      <OptionCard
        title="Shared KV cache across replicas"
        body="Replicas reuse each other's prefix cache through an external store, so repeated system prompts are computed once."
        enabled={draft.kvSharing}
        onToggle={(v) => update({ kvSharing: v })}
        disabledReason={ctx.caps.kvSharing ? null : `${engineName} cannot share KV cache (vLLM and SGLang can).`}
      >
        <Field label="Backend">
          <Select value={draft.kvBackend} onValueChange={(v) => update({ kvBackend: v as KvBackend })}>
            <SelectTrigger className="w-full" size="sm">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="lmcache">LMCache</SelectItem>
              <SelectItem value="mooncake">Mooncake</SelectItem>
              <SelectItem value="native">Engine native</SelectItem>
            </SelectContent>
          </Select>
        </Field>
      </OptionCard>
    </div>
  );
}

export function StepRuntime({ draft, update, ctx }: StepProps) {
  const parsers = toolParserOptions(ctx.engine?.name);
  const isCustomParser = !!draft.toolParser && !parsers.some((p) => p.value === draft.toolParser);
  return (
    <div className="grid items-start gap-6 lg:grid-cols-2">
      <div className="space-y-5">
        <Field label="Launch flags" hint={`Passed to ${ctx.engine?.display_name ?? 'the engine'} as-is (blank value = bare flag). Prefilled from auto-pick and the model defaults.`}>
          <KeyValueEditor rows={draft.flags} onChange={(rows) => update({ flags: rows })} keyPlaceholder="--flash-attn" valuePlaceholder="" addLabel="Add flag" />
        </Field>
        <Field label="Environment" hint="Extra environment for the engine process (no secrets — use the vault).">
          <KeyValueEditor rows={draft.envVars} onChange={(rows) => update({ envVars: rows })} keyPlaceholder="GGML_CUDA_GRAPHS" valuePlaceholder="1" addLabel="Add variable" />
        </Field>
      </div>
      <div className="space-y-5">
        <Field label="Tool parser" hint="How tool calls are parsed out of the model's output for OpenAI / Anthropic responses.">
          <div className="grid gap-2 sm:grid-cols-2">
            <Select value={isCustomParser ? 'custom' : draft.toolParser || 'unset'} onValueChange={(v) => update({ toolParser: v === 'unset' ? '' : v === 'custom' ? draft.toolParser || ' ' : String(v) })}>
              <SelectTrigger className="w-full">
                <SelectValue>{(v: unknown) => (v === 'unset' ? `Model default (${ctx.model?.tool_parser_default ?? 'none'})` : v === 'custom' ? 'Custom…' : parsers.find((p) => p.value === v)?.label ?? String(v))}</SelectValue>
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="unset">Model default ({ctx.model?.tool_parser_default ?? 'none'})</SelectItem>
                {parsers.map((p) => (
                  <SelectItem key={p.value} value={p.value}>
                    {p.label}
                  </SelectItem>
                ))}
                <SelectItem value="custom">Custom…</SelectItem>
              </SelectContent>
            </Select>
            {isCustomParser && <Input value={draft.toolParser.trim()} onChange={(e) => update({ toolParser: e.target.value })} placeholder="parser name" className="font-mono text-sm" aria-label="Custom tool parser" />}
          </div>
        </Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Priority" hint="Overrides the model's priority for this deployment.">
            <Select value={draft.priorityOverride || 'inherit'} onValueChange={(v) => update({ priorityOverride: v === 'inherit' ? '' : (v as ModelPriority) })}>
              <SelectTrigger className="w-full">
                <SelectValue>{(v: unknown) => (v === 'inherit' ? `Inherit (${ctx.model ? MODEL_PRIORITY_LABELS[ctx.model.priority] : '—'})` : MODEL_PRIORITY_LABELS[v as ModelPriority])}</SelectValue>
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="inherit">Inherit ({ctx.model ? MODEL_PRIORITY_LABELS[ctx.model.priority] : '—'})</SelectItem>
                {(Object.keys(MODEL_PRIORITY_LABELS) as ModelPriority[]).map((p) => (
                  <SelectItem key={p} value={p}>
                    {MODEL_PRIORITY_LABELS[p]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>
          <Field label="Load balancing" hint="How the API layer picks a replica.">
            <Select value={draft.trafficStrategy} onValueChange={(v) => update({ trafficStrategy: v as TrafficStrategyName })}>
              <SelectTrigger className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="least_sessions">least_sessions</SelectItem>
                <SelectItem value="weighted">weighted</SelectItem>
                <SelectItem value="round_robin">round_robin</SelectItem>
              </SelectContent>
            </Select>
          </Field>
        </div>
        <div className={cn('space-y-2 rounded-lg border border-border p-3', !ctx.caps.controlVectors && 'opacity-60')}>
          <div className="flex items-center justify-between gap-3">
            <span>
              <span className="block text-xs font-medium">Control vectors</span>
              <span className="block text-[11px] text-muted-foreground">{ctx.caps.controlVectors ? 'Steer the model with GGUF control vectors (llama.cpp --control-vector-scaled).' : `Only llama.cpp supports control vectors.`}</span>
            </span>
            <Button type="button" variant="outline" size="xs" disabled={!ctx.caps.controlVectors} onClick={() => update({ controlVectors: [...draft.controlVectors, { path: '', scale: '0.8', from: '', to: '' }] })}>
              <Plus className="size-3" />
              Add
            </Button>
          </div>
          {draft.controlVectors.map((cv, i) => (
            <div key={i} className="grid grid-cols-[1fr_64px_52px_52px_32px] items-center gap-1.5">
              <Input value={cv.path} onChange={(e) => update({ controlVectors: draft.controlVectors.map((x, j) => (j === i ? { ...x, path: e.target.value } : x)) })} placeholder="/models/cv/formal.gguf" className="h-8 font-mono text-[11px]" aria-label="Control vector path" />
              <Input value={cv.scale} onChange={(e) => update({ controlVectors: draft.controlVectors.map((x, j) => (j === i ? { ...x, scale: e.target.value } : x)) })} placeholder="scale" className="h-8 font-mono text-[11px]" aria-label="Scale" />
              <Input value={cv.from} onChange={(e) => update({ controlVectors: draft.controlVectors.map((x, j) => (j === i ? { ...x, from: e.target.value } : x)) })} placeholder="from" className="h-8 font-mono text-[11px]" aria-label="Layer from" />
              <Input value={cv.to} onChange={(e) => update({ controlVectors: draft.controlVectors.map((x, j) => (j === i ? { ...x, to: e.target.value } : x)) })} placeholder="to" className="h-8 font-mono text-[11px]" aria-label="Layer to" />
              <Button type="button" variant="ghost" size="icon-sm" onClick={() => update({ controlVectors: draft.controlVectors.filter((_, j) => j !== i) })} aria-label="Remove control vector" className="text-muted-foreground hover:text-destructive">
                <Trash2 className="size-3.5" />
              </Button>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
