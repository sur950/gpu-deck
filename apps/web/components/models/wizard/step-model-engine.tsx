'use client';

import { useMemo, useState } from 'react';
import Link from 'next/link';
import { CircleCheck, Loader2, Lock, Search, Sparkles, TriangleAlert, WandSparkles, XCircle } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Field } from '@/components/shared/form-dialog';
import { StatusBadge } from '@/components/shared/StatusBadge';
import { COMPAT_BADGE } from '@/components/fleet/status';
import { EngineBadge, FormatBadge, ModelTypeBadge, PriorityBadge } from '@/components/models/model-badges';
import { SERVING_NAME_RE } from '@/components/models/wizard/draft';
import type { StepProps } from '@/components/models/wizard/context';
import { cn } from '@/lib/utils';
import { formatBytes } from '@/utils/format';
import { ENGINE_LABELS } from '@/types/models';

export function modelStepError({ draft, ctx }: Pick<StepProps, 'draft' | 'ctx'>): string | null {
  if (!draft.modelId) return 'Pick a model';
  if (ctx.version?.status === 'blocked') return 'This version is blocked (pickle weights)';
  if (!SERVING_NAME_RE.test(draft.name)) return 'Serving name: lowercase letters, digits, dots, dashes, underscores';
  if (ctx.takenNames.has(draft.name)) return `A deployment is already called "${draft.name}"`;
  return null;
}

export function StepModel({ draft, update, ctx }: StepProps) {
  const [filter, setFilter] = useState('');
  const list = useMemo(
    () =>
      ctx.models.filter((m) => {
        const q = filter.trim().toLowerCase();
        return !q || [m.name, m.display_name ?? '', m.family ?? '', m.repo_id].some((s) => s.toLowerCase().includes(q));
      }),
    [ctx.models, filter],
  );
  const locked = ctx.mode === 'edit';
  const nameError = draft.name && !SERVING_NAME_RE.test(draft.name) ? 'Lowercase letters, digits, dots, dashes and underscores.' : ctx.takenNames.has(draft.name) ? 'Another deployment uses this name.' : null;

  return (
    <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,1fr)_360px]">
      <div className="overflow-hidden rounded-lg border border-border">
        <div className="relative border-b border-border">
          <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="Filter the catalog…" className="h-10 rounded-none border-0 pl-9 shadow-none focus-visible:ring-0" disabled={locked} aria-label="Filter models" />
        </div>
        <ul className="max-h-[440px] divide-y divide-border overflow-y-auto" role="radiogroup" aria-label="Model">
          {list.map((m) => {
            const v = m.default_version;
            const blocked = v?.status === 'blocked';
            const selected = draft.modelId === m.id;
            return (
              <li key={m.id}>
                <button
                  type="button"
                  role="radio"
                  aria-checked={selected}
                  disabled={(locked && !selected) || blocked}
                  onClick={() => update({ modelId: m.id, versionId: '', engineVersionId: '', name: draft.modelId === m.id ? draft.name : '', contextSize: m.default_context_size ? String(m.default_context_size) : '', toolParser: '', flags: [] })}
                  className={cn(
                    'flex w-full items-center gap-3 px-3.5 py-2.5 text-left transition-colors disabled:cursor-not-allowed disabled:opacity-50',
                    selected ? 'bg-muted/60 shadow-[inset_3px_0_0_rgb(var(--brand))]' : 'hover:bg-muted/30',
                  )}
                >
                  <span className={cn('flex size-4 shrink-0 items-center justify-center rounded-full border', selected ? 'border-foreground' : 'border-input')}>
                    {selected && <span className="size-2 rounded-full bg-foreground" />}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="flex flex-wrap items-center gap-2">
                      <span className="text-[13px] font-medium">{m.display_name ?? m.name}</span>
                      <ModelTypeBadge type={m.type} />
                      {blocked && (
                        <span className="inline-flex items-center gap-1 text-[10.5px] text-error">
                          <Lock className="size-3" />
                          blocked
                        </span>
                      )}
                    </span>
                    <span className="mt-0.5 block truncate font-mono text-[10.5px] text-muted-foreground">{m.repo_id}</span>
                  </span>
                  <span className="flex shrink-0 items-center gap-2">
                    {v && <FormatBadge format={v.format} />}
                    <span className="w-16 text-right font-mono text-[11px] text-muted-foreground tabular-nums">{v ? formatBytes(v.total_size_bytes) : '—'}</span>
                    <PriorityBadge priority={m.priority} className="hidden sm:inline-flex" />
                  </span>
                </button>
              </li>
            );
          })}
          {list.length === 0 && (
            <li className="p-6 text-center text-sm text-muted-foreground">
              No models match. <Link href="/catalog/new" className="underline underline-offset-2">Add one to the catalog</Link>.
            </li>
          )}
        </ul>
      </div>

      <div className="space-y-4 rounded-lg border border-border bg-muted/20 p-4">
        <Field label="Version" hint={ctx.version ? `${ctx.version.format.toUpperCase()} · ${formatBytes(ctx.version.total_size_bytes)}${ctx.version.quantization ? ` · ${ctx.version.quantization}` : ''}` : 'Pick a model first.'}>
          <Select value={draft.versionId || 'default'} onValueChange={(v) => update({ versionId: v === 'default' ? '' : String(v), engineVersionId: '' })} disabled={!ctx.model}>
            <SelectTrigger className="w-full">
              <SelectValue>
                {(v: unknown) => {
                  if (v === 'default') return `Default${ctx.model?.default_version ? ` (${ctx.model.default_version.version})` : ''}`;
                  return ctx.versions.find((x) => x.id === v)?.version ?? 'Version';
                }}
              </SelectValue>
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="default">Default{ctx.model?.default_version ? ` (${ctx.model.default_version.version})` : ''} — follows the catalog</SelectItem>
              {ctx.versions.map((v) => (
                <SelectItem key={v.id} value={v.id} disabled={v.status === 'blocked'}>
                  Pin {v.version} · {v.format} · {formatBytes(v.total_size_bytes)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </Field>
        <Field label="Serving name" hint={nameError ?? 'What clients put in "model". Must be unique.'} htmlFor="wz-name">
          <Input id="wz-name" value={draft.name} onChange={(e) => update({ name: e.target.value.toLowerCase() })} placeholder={ctx.model?.name ?? 'qwen3-8b'} className="font-mono text-sm" aria-invalid={!!nameError} />
        </Field>
        <Field label="Description" htmlFor="wz-desc">
          <Textarea id="wz-desc" rows={2} value={draft.description} onChange={(e) => update({ description: e.target.value })} placeholder="Support bot, low latency" />
        </Field>
      </div>
    </div>
  );
}

export function engineStepError({ draft, ctx }: Pick<StepProps, 'draft' | 'ctx'>): string | null {
  if (ctx.autoPickLoading || ctx.compatLoading) return 'Checking compatibility…';
  if (draft.engineVersionId) {
    const c = ctx.compat.find((x) => x.engine_version_id === draft.engineVersionId);
    if (c?.result === 'incompatible') return 'The chosen engine version is incompatible';
    return null;
  }
  if (!ctx.autoPick?.engine_version) return 'No compatible engine — pick a different model or version';
  return null;
}

const SEVERITY = {
  info: { icon: CircleCheck, tone: 'text-success' },
  warning: { icon: TriangleAlert, tone: 'text-warning' },
  error: { icon: XCircle, tone: 'text-error' },
} as const;

export function StepEngine({ draft, update, ctx }: StepProps) {
  const pick = ctx.autoPick;
  const picked = pick?.engine_version;
  const [expanded, setExpanded] = useState<string | null>(null);
  const autoSelected = !draft.engineVersionId;

  return (
    <div className="space-y-4">
      <button
        type="button"
        onClick={() => update({ engineVersionId: '' })}
        className={cn(
          'relative flex w-full flex-col gap-3 overflow-hidden rounded-lg border p-4 text-left transition-colors sm:flex-row sm:items-center',
          autoSelected ? 'border-foreground/60 bg-muted/40 ring-1 ring-foreground/50' : 'border-border hover:border-foreground/25',
        )}
      >
        <span aria-hidden className="pointer-events-none absolute -top-16 -right-10 size-40 rounded-full bg-brand/10 blur-2xl" />
        <span className="flex size-9 shrink-0 items-center justify-center rounded-lg border border-border bg-background">
          {ctx.autoPickLoading ? <Loader2 className="size-4 animate-spin text-muted-foreground" /> : <WandSparkles className="size-4 text-muted-foreground" />}
        </span>
        <span className="min-w-0 flex-1">
          <span className="block text-[13px] font-semibold">Auto-pick</span>
          <span className="block text-xs text-muted-foreground">
            {ctx.autoPickLoading
              ? 'Scoring every enabled engine version against this model and the fleet…'
              : picked
                ? <>Recommends <span className="font-medium text-foreground">{ENGINE_LABELS[picked.engine_name]} {picked.version}</span> (score {pick.check?.score ?? '—'}{pick.check?.estimated_vram_bytes ? `, ~${formatBytes(pick.check.estimated_vram_bytes)} per GPU` : ''}). The scheduler re-checks at placement time.</>
                : 'No engine version in the registry can serve this model on the current fleet.'}
          </span>
          {picked && (pick?.suggested_tool_parser || Object.keys(pick?.suggested_launch_flags ?? {}).length > 0) && (
            <span className="mt-1.5 flex flex-wrap items-center gap-1 text-[11px] text-muted-foreground">
              <Sparkles className="size-3" />
              Suggests
              {pick?.suggested_tool_parser && <code className="rounded-sm bg-muted px-1 font-mono text-[10.5px]">tool parser {pick.suggested_tool_parser}</code>}
              {Object.entries(pick?.suggested_launch_flags ?? {}).map(([k, v]) => (
                <code key={k} className="rounded-sm bg-muted px-1 font-mono text-[10.5px]">
                  {v === true ? k : `${k} ${String(v)}`}
                </code>
              ))}
              <span>— prefilled in the Runtime step.</span>
            </span>
          )}
        </span>
        <span className={cn('flex size-4 shrink-0 items-center justify-center rounded-full border', autoSelected ? 'border-foreground' : 'border-input')}>{autoSelected && <span className="size-2 rounded-full bg-foreground" />}</span>
      </button>

      <div className="overflow-hidden rounded-lg border border-border">
        <div className="flex items-center justify-between border-b border-border bg-muted/30 px-4 py-2">
          <span className="text-xs font-medium">Or pin an engine version</span>
          <span className="text-[11px] text-muted-foreground">{ctx.compatLoading
            ? 'checking…'
            : (() => {
                const ok = ctx.compat.filter((c) => c.result === 'compatible').length;
                const unknown = ctx.compat.filter((c) => c.result === 'unknown').length;
                return `${ok} of ${ctx.compat.length} compatible${unknown ? ` · ${unknown} not fully checked` : ''}`;
              })()}</span>
        </div>
        <ul className="divide-y divide-border">
          {ctx.compat.map((c) => {
            const selected = draft.engineVersionId === c.engine_version_id;
            const disabled = c.result === 'incompatible';
            const open = expanded === c.id;
            const worst = c.reasons.find((r) => r.severity === 'error') ?? c.reasons.find((r) => r.severity === 'warning');
            return (
              <li key={c.id}>
                <div className={cn('flex items-center gap-3 px-4 py-2.5', selected && 'bg-muted/50')}>
                  <button
                    type="button"
                    disabled={disabled}
                    onClick={() => update({ engineVersionId: c.engine_version_id })}
                    className="flex min-w-0 flex-1 items-center gap-3 text-left disabled:cursor-not-allowed"
                    aria-pressed={selected}
                  >
                    <span className={cn('flex size-4 shrink-0 items-center justify-center rounded-full border', selected ? 'border-foreground' : 'border-input', disabled && 'opacity-40')}>
                      {selected && <span className="size-2 rounded-full bg-foreground" />}
                    </span>
                    <span className={cn('w-44 shrink-0', disabled && 'opacity-60')}>
                      <EngineBadge engine={c.engine_name} version={c.engine_version} />
                    </span>
                    <StatusBadge variant={COMPAT_BADGE[c.result]} className="shrink-0 py-0">{c.result}</StatusBadge>
                    <span className={cn('min-w-0 flex-1 truncate text-[11.5px]', worst?.severity === 'error' ? 'text-error-dark dark:text-error-light' : 'text-muted-foreground')}>
                      {worst?.message ?? c.reasons[c.reasons.length - 1]?.message}
                    </span>
                  </button>
                  <span className="hidden w-10 text-right font-mono text-[11px] text-muted-foreground tabular-nums sm:block">{c.score}</span>
                  <div className="hidden h-1.5 w-20 overflow-hidden rounded-full bg-muted sm:block" aria-hidden>
                    <div className={cn('h-full rounded-full', c.result === 'compatible' ? 'bg-success' : c.result === 'unknown' ? 'bg-muted-foreground/50' : 'bg-error/60')} style={{ width: `${c.score}%` }} />
                  </div>
                  <Button variant="ghost" size="xs" onClick={() => setExpanded(open ? null : c.id)} aria-expanded={open}>
                    {open ? 'Hide' : 'Why'}
                  </Button>
                </div>
                {open && (
                  <ul className="space-y-1 border-t border-border bg-muted/20 px-4 py-2.5 pl-12">
                    {c.reasons.map((r, i) => {
                      const s = SEVERITY[r.severity ?? 'info'];
                      return (
                        <li key={i} className="flex gap-2 text-[11.5px]">
                          <s.icon className={cn('mt-0.5 size-3.5 shrink-0', s.tone)} />
                          <span>
                            <span className="font-mono text-[10.5px] text-muted-foreground">{r.code}</span> {r.message}
                          </span>
                        </li>
                      );
                    })}
                  </ul>
                )}
              </li>
            );
          })}
          {!ctx.compatLoading && ctx.compat.length === 0 && <li className="p-6 text-center text-sm text-muted-foreground">Pick a model to check engines.</li>}
        </ul>
      </div>
    </div>
  );
}
