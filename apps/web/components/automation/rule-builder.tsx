'use client';

import { useMemo, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { ChevronRight, CircleAlert, FlaskConical, Loader2, Radio, Save, Sparkles } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import { Skeleton } from '@/components/ui/skeleton';
import { cn } from '@/lib/utils';
import type { JsonSchema, Rule, RuleInput, RuleType, TargetSelector } from '@/hooks/api/automation-api';
import { useCreateRule, useLayouts, useRefClusters, useRefDeployments, useRefModels, useRefNodes, useRuleSchemas, useUpdateRule } from '@/hooks/api/automation';
import { RuleTypePicker } from '@/components/automation/rule-type-picker';
import { RuleSentence } from '@/components/automation/rule-sentence';
import { SchemaField } from '@/components/automation/schema-field';
import { TargetSelectorEditor, toMatchable } from '@/components/automation/target-selector';
import { Segmented } from '@/components/automation/segmented';
import { behaviourSentence, describeTarget, formatSeconds, RULE_TYPE_META, ruleSentence, type RefNames } from '@/components/automation/rule-meta';
import { defaultsFor, FIELD_LAYOUT, pickKnown, validateParams } from '@/components/automation/schema-utils';
import { compactTarget, matchesTarget } from '@/components/automation/target-match';
import { browserTimeZone, formatInZone, nextOccurrences, zoneAbbreviation } from '@/components/automation/schedule';
import { formatCountdown } from '@/components/automation/run-meta';
import { useNow } from '@/components/automation/use-now';

interface RuleBuilderProps {
  /** Existing rule → edit mode (type locked). */
  rule?: Rule;
  /** Start a new rule from a copy of this one ("Duplicate"). */
  template?: Rule;
  /** Pre-selected type for a new rule (from ?type=). */
  initialType?: RuleType | null;
}

const COOLDOWN_PRESETS = [0, 300, 1800, 3600];
const INTERVAL_PRESETS = [15, 30, 60, 300];

function Section({ step, title, description, id, children, aside }: { step: number; title: string; description?: string; id?: string; children: React.ReactNode; aside?: React.ReactNode }) {
  return (
    <Card id={id} className="scroll-mt-20 gap-0 rounded-xl py-0">
      <div className="flex items-start justify-between gap-4 border-b border-border px-5 py-4">
        <div className="flex items-start gap-3">
          <span className="mt-px flex size-6 shrink-0 items-center justify-center rounded-full border border-border bg-muted/60 font-mono text-[11px] font-semibold text-foreground/80">{step}</span>
          <div>
            <h2 className="text-sm font-semibold tracking-tight">{title}</h2>
            {description && <p className="mt-0.5 text-xs text-muted-foreground">{description}</p>}
          </div>
        </div>
        {aside}
      </div>
      <CardContent className="px-5 py-5">{children}</CardContent>
    </Card>
  );
}

function PresetRow({ values, current, format, onPick }: { values: number[]; current: number; format: (v: number) => string; onPick: (v: number) => void }) {
  return (
    <div className="mt-2 flex flex-wrap gap-1">
      {values.map((v) => (
        <button
          key={v}
          type="button"
          onClick={() => onPick(v)}
          className={cn('h-6 rounded-[5px] px-2 font-mono text-[11px] tabular-nums outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring/30', current === v ? 'bg-foreground text-background' : 'bg-muted text-muted-foreground hover:text-foreground')}
        >
          {format(v)}
        </button>
      ))}
    </div>
  );
}

/**
 * The rule builder: pick one of six types, fill a short form rendered from
 * the contract schema, choose targets, set behaviour — while a live sentence
 * reads the rule back in plain language. No YAML anywhere.
 */
export function RuleBuilder({ rule, template, initialType = null }: RuleBuilderProps) {
  const router = useRouter();
  const editing = !!rule;
  const seed = rule ?? template;
  const { schemas, isLoading: schemasLoading } = useRuleSchemas();
  const layoutsQuery = useLayouts({ pageSize: 100 });
  const deploymentsQuery = useRefDeployments();
  const modelsQuery = useRefModels();
  const nodesQuery = useRefNodes();
  const clustersQuery = useRefClusters();
  const createRule = useCreateRule();
  const updateRule = useUpdateRule();

  const [type, setType] = useState<RuleType | null>(seed?.type ?? initialType);
  const [paramsByType, setParamsByType] = useState<Partial<Record<RuleType, Record<string, unknown>>>>(() => (seed ? { [seed.type]: { ...seed.params } } : {}));
  const [target, setTarget] = useState<TargetSelector>(seed?.target ?? {});
  const [name, setName] = useState(rule?.name ?? (template ? `${template.name} (copy)` : ''));
  const [nameTouched, setNameTouched] = useState(!!seed);
  const [description, setDescription] = useState(seed?.description ?? '');
  const [enabled, setEnabled] = useState(seed?.enabled ?? true);
  const [dryRun, setDryRun] = useState(template ? true : (rule?.dryRun ?? true));
  const [cooldown, setCooldown] = useState<number>(seed?.cooldownSeconds ?? 300);
  const [interval, setIntervalSeconds] = useState<number>(seed?.evaluationIntervalSeconds ?? 30);
  const [priority, setPriority] = useState<number>(seed?.priority ?? 100);
  const [activeField, setActiveField] = useState<string | null>(null);
  const [showMore, setShowMore] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const now = useNow(15_000);

  const paramsSchema: JsonSchema | undefined = type ? schemas.params[type] : undefined;
  const params = useMemo(() => {
    if (!type) return {};
    return { ...defaultsFor(paramsSchema), ...(paramsByType[type] ?? {}) };
  }, [type, paramsSchema, paramsByType]);

  const layouts = useMemo(() => (layoutsQuery.data?.items ?? []).map((l) => ({ id: l.id, name: l.name, isActive: l.isActive })), [layoutsQuery.data]);
  const deployments = useMemo(() => deploymentsQuery.data ?? [], [deploymentsQuery.data]);
  const nodes = useMemo(() => nodesQuery.data ?? [], [nodesQuery.data]);
  const refs: RefNames = useMemo(
    () => ({
      deployments: new Map(deployments.map((d) => [d.id, d.name])),
      models: new Map([...(modelsQuery.data ?? []).map((m) => [m.id, m.displayName ?? m.name] as const), ...deployments.map((d) => [d.modelId, d.modelName ?? d.name] as const)]),
      nodes: new Map(nodes.map((n) => [n.id, n.name])),
      clusters: new Map((clustersQuery.data ?? []).map((c) => [c.id, c.name])),
      layouts: new Map(layouts.map((l) => [l.id, l.name])),
    }),
    [deployments, modelsQuery.data, nodes, clustersQuery.data, layouts],
  );

  const effectiveTarget = useMemo<TargetSelector>(() => (type === 'time_schedule' ? {} : target), [type, target]);
  const matchedCount = deployments.filter((d) => matchesTarget(toMatchable(d, nodes), effectiveTarget)).length;

  const suggestedName = useMemo(() => {
    if (!type) return '';
    if (type === 'time_schedule') {
      const layoutName = typeof params.layout_id === 'string' ? refs.layouts.get(params.layout_id) : null;
      return `${layoutName ?? 'Layout'} at ${params.at ?? '00:00'}`;
    }
    const who = describeTarget(effectiveTarget, refs);
    return `${RULE_TYPE_META[type].label} — ${who}`.slice(0, 80);
  }, [type, params.layout_id, params.at, effectiveTarget, refs]);
  const effectiveName = nameTouched ? name : suggestedName;

  const paramErrors = useMemo(() => (paramsSchema ? validateParams(paramsSchema, params) : {}), [paramsSchema, params]);
  const otherErrors: Record<string, string> = {};
  if (!type) otherErrors.type = 'Pick a rule type';
  if (!effectiveName.trim()) otherErrors.name = 'Give the rule a name';
  if (effectiveName.length > 255) otherErrors.name = 'At most 255 characters';
  if (!Number.isInteger(cooldown) || cooldown < 0 || cooldown > 86_400) otherErrors.cooldown_seconds = 'Between 0 s and 24 h';
  if (!Number.isInteger(interval) || interval < 5 || interval > 3_600) otherErrors.evaluation_interval_seconds = 'Between 5 s and 1 h';
  if (!Number.isInteger(priority) || priority < 0 || priority > 1_000) otherErrors.priority = 'Between 0 and 1000';
  const allErrors = { ...paramErrors, ...otherErrors };
  const errorCount = Object.keys(allErrors).filter((k) => k !== '_form').length;
  const show = (key: string) => (submitted ? allErrors[key] : undefined);

  function setParam(key: string, value: unknown) {
    if (!type) return;
    setParamsByType((prev) => ({ ...prev, [type]: { ...params, [key]: value } }));
  }

  function jumpTo(field: string) {
    setActiveField(field);
    const id = field === 'target' ? 'section-target' : `field-${field}`;
    const el = document.getElementById(id);
    if (!el) {
      if (paramsSchema?.properties?.[field]) setShowMore(true);
      requestAnimationFrame(() => document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'center' }));
      return;
    }
    el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    (el.querySelector('input, button, textarea') as HTMLElement | null)?.focus({ preventScroll: true });
  }

  const sentence = type ? ruleSentence(type, params, effectiveTarget, refs) : [];
  const behaviour = type ? behaviourSentence({ dryRun, cooldownSeconds: cooldown, type }) : [];
  const scheduleRuns = type === 'time_schedule' && !allErrors.cron && !allErrors.timezone ? nextOccurrences(params as { at?: string; days?: string[]; cron?: string | null; timezone?: string }, now, 4) : [];
  const localTz = browserTimeZone();
  const busy = createRule.isPending || updateRule.isPending;

  async function submit(andDryRun: boolean) {
    setSubmitted(true);
    if (!type || errorCount > 0) {
      const first = Object.keys(allErrors).find((k) => k !== '_form');
      if (first) jumpTo(first === 'name' ? 'name' : first);
      return;
    }
    const body: RuleInput = {
      name: effectiveName.trim(),
      description: description.trim() || null,
      type,
      params: JSON.parse(JSON.stringify(pickKnown(paramsSchema, params))) as Record<string, unknown>,
      target: type === 'time_schedule' ? {} : compactTarget(target),
      enabled,
      dry_run: dryRun,
      cooldown_seconds: cooldown,
      evaluation_interval_seconds: interval,
      priority,
    };
    let saved: Rule | null;
    if (editing && rule) {
      const patch: Partial<RuleInput> = { ...body };
      delete patch.type; // the type of a rule is fixed (RuleUpdate has no `type`)
      saved = await updateRule.mutateAsync({ id: rule.id, body: patch }).catch(() => null);
    } else {
      saved = await createRule.mutateAsync(body).catch(() => null);
    }
    if (saved) router.push(`/rules/${saved.id}${andDryRun ? '?dryrun=1' : ''}`);
  }

  const layout = type ? FIELD_LAYOUT[type] : null;
  const allKeys = Object.keys(paramsSchema?.properties ?? {});
  const primary = layout ? layout.primary.filter((k) => allKeys.includes(k)) : [];
  const secondary = layout ? layout.secondary.filter((k) => allKeys.includes(k)) : [];
  const more = allKeys.filter((k) => !primary.includes(k) && !secondary.includes(k));
  const hasMoreErrors = submitted && more.some((k) => allErrors[k]);

  const field = (key: string) =>
    paramsSchema?.properties?.[key] ? (
      <SchemaField
        key={key}
        name={key}
        schema={paramsSchema.properties[key]}
        value={params[key]}
        onChange={(v) => setParam(key, v)}
        error={show(key)}
        required={paramsSchema.required?.includes(key)}
        active={activeField === key}
        onFocusField={setActiveField}
        context={{ layouts }}
      />
    ) : null;

  return (
    <div className="grid items-start gap-6 xl:grid-cols-[minmax(0,1fr)_400px]">
      <div className="min-w-0 space-y-5">
        <Section step={1} title="What should happen?" description={editing ? 'The type of an existing rule cannot change — create a new rule for a different type.' : 'Six kinds of rule. Each is a short form — nothing to write by hand.'}>
          <RuleTypePicker value={type} onChange={(t) => setType(t)} locked={editing} />
          {submitted && otherErrors.type && <p className="mt-3 text-xs font-medium text-destructive">{otherErrors.type}</p>}
        </Section>

        {type && (
          <>
            <Section step={2} title={type === 'time_schedule' ? 'When and where to switch' : 'When it fires'} description={RULE_TYPE_META[type].description}>
              {schemasLoading && !paramsSchema ? (
                <div className="space-y-3">
                  <Skeleton className="h-16" />
                  <Skeleton className="h-16" />
                </div>
              ) : (
                <div className="space-y-6">
                  {submitted && paramErrors._form && (
                    <p className="flex items-center gap-2 rounded-md bg-destructive/10 p-3 text-xs text-destructive">
                      <CircleAlert className="size-3.5" /> {paramErrors._form}
                    </p>
                  )}
                  <div className="grid gap-x-8 gap-y-3 md:grid-cols-2">{primary.map(field)}</div>
                  {secondary.length > 0 && <div className="grid gap-x-8 gap-y-3 border-t border-border pt-5 md:grid-cols-2">{secondary.map(field)}</div>}
                  {more.length > 0 && (
                    <div className="border-t border-border pt-4">
                      <button type="button" onClick={() => setShowMore((v) => !v)} aria-expanded={showMore || hasMoreErrors} className="inline-flex items-center gap-1.5 text-xs font-medium text-muted-foreground hover:text-foreground">
                        <ChevronRight className={cn('size-3.5 transition-transform', (showMore || hasMoreErrors) && 'rotate-90')} />
                        More options
                        <span className="text-[11px] font-normal">({more.length})</span>
                        {hasMoreErrors && <span className="size-1.5 rounded-full bg-destructive" />}
                      </button>
                      {(showMore || hasMoreErrors) && <div className="mt-3 grid gap-x-8 gap-y-3 md:grid-cols-2">{more.map(field)}</div>}
                    </div>
                  )}
                </div>
              )}
            </Section>

            <Section
              id="section-target"
              step={3}
              title="Which deployments"
              description={type === 'time_schedule' ? 'The layout decides which deployments move.' : 'All conditions must hold; inside one condition any value matches.'}
              aside={type !== 'time_schedule' ? <span className="whitespace-nowrap rounded-full border border-border px-2.5 py-1 text-[11px] font-medium tabular-nums text-muted-foreground">{matchedCount} matched</span> : undefined}
            >
              {deploymentsQuery.isLoading ? (
                <Skeleton className="h-24" />
              ) : (
                <TargetSelectorEditor
                  value={target}
                  onChange={setTarget}
                  deployments={deployments}
                  models={modelsQuery.data ?? []}
                  nodes={nodes}
                  clusters={clustersQuery.data ?? []}
                  ignored={type === 'time_schedule'}
                />
              )}
            </Section>

            <Section step={4} title="How it behaves" description="Name it, decide whether it may act, and how often.">
              <div className="space-y-5">
                <div
                  id="field-dry_run"
                  className={cn('scroll-mt-24 rounded-xl border p-4 transition-colors', dryRun ? 'border-info/30 bg-info/[0.06]' : 'border-border bg-muted/30', activeField === 'dry_run' && 'ring-1 ring-brand/30')}
                >
                  <div className="flex items-start justify-between gap-4">
                    <div className="flex items-start gap-3">
                      <span className={cn('flex size-8 shrink-0 items-center justify-center rounded-lg', dryRun ? 'bg-info/15 text-info-dark dark:text-info-light' : 'bg-success/15 text-success-dark dark:text-success-light')}>
                        {dryRun ? <FlaskConical className="size-4" /> : <Radio className="size-4" />}
                      </span>
                      <div>
                        <p className="text-sm font-semibold">{dryRun ? 'Dry run — watch only' : 'Live — acts on the fleet'}</p>
                        <p className="mt-0.5 max-w-prose text-xs text-muted-foreground">
                          {dryRun
                            ? 'Every evaluation is recorded with what the rule would have done; nothing is loaded, unloaded or moved. Recommended for a new rule — switch it live once the history looks right.'
                            : 'When the condition holds the rule drains, unloads, loads or moves instances through the swap manager, with rollback on failure.'}
                        </p>
                      </div>
                    </div>
                    <Switch checked={dryRun} onCheckedChange={(v) => { setDryRun(v); setActiveField('dry_run'); }} aria-label="Dry run" />
                  </div>
                </div>

                <div className="grid gap-x-8 gap-y-4 md:grid-cols-2">
                  <div id="field-name" className="md:col-span-2">
                    <label htmlFor="rule-name" className="mb-2 flex items-center justify-between text-[13px] font-medium">
                      Name
                      {!nameTouched && type && (
                        <span className="inline-flex items-center gap-1 text-[11px] font-normal text-muted-foreground">
                          <Sparkles className="size-3" /> suggested from the rule
                        </span>
                      )}
                    </label>
                    <Input
                      id="rule-name"
                      value={effectiveName}
                      onChange={(e) => {
                        setNameTouched(true);
                        setName(e.target.value);
                      }}
                      onFocus={() => setActiveField('name')}
                      aria-invalid={!!show('name')}
                      maxLength={255}
                      className="h-9"
                    />
                    {show('name') && <p className="mt-1.5 text-[11px] font-medium text-destructive">{show('name')}</p>}
                  </div>
                  <div className="md:col-span-2">
                    <label htmlFor="rule-description" className="mb-2 block text-[13px] font-medium">
                      Description <span className="text-[11px] font-normal text-muted-foreground">optional</span>
                    </label>
                    <Textarea id="rule-description" value={description} onChange={(e) => setDescription(e.target.value)} maxLength={2048} rows={2} placeholder="Why this rule exists — shown on the rules list." className="text-xs" />
                  </div>

                  {type !== 'time_schedule' && (
                    <div id="field-cooldown_seconds" className={cn('-mx-3 rounded-lg px-3 py-2.5', activeField === 'cooldown_seconds' && 'bg-brand/[0.035] ring-1 ring-inset ring-brand/25')}>
                      <label htmlFor="rule-cooldown" className="mb-2 block text-[13px] font-medium">Cooldown per deployment</label>
                      <div className="relative w-full sm:w-48">
                        <Input id="rule-cooldown" type="number" min={0} max={86400} value={cooldown} onFocus={() => setActiveField('cooldown_seconds')} onChange={(e) => setCooldown(Number(e.target.value))} className="h-9 pr-10 font-mono text-[13px] tabular-nums" aria-invalid={!!show('cooldown_seconds')} />
                        <span className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-[11px] text-muted-foreground">s</span>
                      </div>
                      <PresetRow values={COOLDOWN_PRESETS} current={cooldown} format={(v) => (v === 0 ? 'none' : formatSeconds(v))} onPick={(v) => { setCooldown(v); setActiveField('cooldown_seconds'); }} />
                      <p className="mt-1.5 text-[11px] text-muted-foreground">{show('cooldown_seconds') ?? 'After acting on a deployment, wait this long before acting on it again.'}</p>
                    </div>
                  )}
                  {type !== 'time_schedule' && (
                    <div className="-mx-3 rounded-lg px-3 py-2.5">
                      <label htmlFor="rule-interval" className="mb-2 block text-[13px] font-medium">Check every</label>
                      <div className="relative w-full sm:w-48">
                        <Input id="rule-interval" type="number" min={5} max={3600} value={interval} onChange={(e) => setIntervalSeconds(Number(e.target.value))} className="h-9 pr-10 font-mono text-[13px] tabular-nums" aria-invalid={!!show('evaluation_interval_seconds')} />
                        <span className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-[11px] text-muted-foreground">s</span>
                      </div>
                      <PresetRow values={INTERVAL_PRESETS} current={interval} format={formatSeconds} onPick={setIntervalSeconds} />
                      <p className="mt-1.5 text-[11px] text-muted-foreground">{show('evaluation_interval_seconds') ?? 'How often the evaluator looks at this rule (5 s – 1 h).'}</p>
                    </div>
                  )}
                  <div className="-mx-3 rounded-lg px-3 py-2.5">
                    <label htmlFor="rule-priority" className="mb-2 block text-[13px] font-medium">Evaluation order</label>
                    <Input id="rule-priority" type="number" min={0} max={1000} value={priority} onChange={(e) => setPriority(Number(e.target.value))} className="h-9 w-full font-mono text-[13px] tabular-nums sm:w-48" aria-invalid={!!show('priority')} />
                    <p className="mt-1.5 text-[11px] text-muted-foreground">{show('priority') ?? 'Lower runs first within a tick (0–1000). Heal rules usually go first.'}</p>
                  </div>
                  <div className="-mx-3 rounded-lg px-3 py-2.5">
                    <p className="mb-2 text-[13px] font-medium">Status</p>
                    <Segmented
                      value={enabled ? 'on' : 'off'}
                      onChange={(v) => setEnabled(v === 'on')}
                      options={[
                        { value: 'on', label: 'Enabled' },
                        { value: 'off', label: 'Disabled' },
                      ]}
                      aria-label="Status"
                    />
                    <p className="mt-1.5 text-[11px] text-muted-foreground">Disabled rules are never evaluated, but “Dry run now” still works.</p>
                  </div>
                </div>
              </div>
            </Section>
          </>
        )}
      </div>

      {/* ── Live preview ── */}
      <aside className="space-y-4 xl:sticky xl:top-4">
        <Card className="gap-0 overflow-hidden rounded-xl py-0">
          <div className="relative border-b border-border px-5 py-4">
            <div aria-hidden className="bg-dot-grid pointer-events-none absolute inset-0 [mask-image:linear-gradient(to_left,black,transparent_70%)]" />
            <div className="relative flex items-center justify-between gap-3">
              <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">Reads as</p>
              {type && (
                <span className={cn('inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[11px] font-medium', dryRun ? 'border-info/30 bg-info/10 text-info-dark dark:text-info-light' : 'border-success/30 bg-success/10 text-success-dark dark:text-success-light')}>
                  <span className={cn('size-1.5 rounded-full', dryRun ? 'bg-info' : 'bg-success')} />
                  {dryRun ? 'Dry run' : 'Live'}
                  {!enabled && ' · disabled'}
                </span>
              )}
            </div>
          </div>
          <CardContent className="space-y-4 px-5 py-5">
            {type ? (
              <>
                <div className="flex items-center gap-2">
                  {(() => {
                    const Icon = RULE_TYPE_META[type].icon;
                    return <Icon className="size-4 text-brand" />;
                  })()}
                  <p className="truncate text-sm font-semibold">{effectiveName || RULE_TYPE_META[type].label}</p>
                </div>
                <RuleSentence parts={sentence} size="lg" activeField={activeField} onFieldClick={jumpTo} className="text-foreground/70" />
                {behaviour.length > 0 && <RuleSentence parts={behaviour} size="sm" activeField={activeField} onFieldClick={jumpTo} />}

                <div className="space-y-2 border-t border-border pt-4 text-xs">
                  {type === 'time_schedule' ? (
                    <>
                      <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">Next switches</p>
                      {scheduleRuns.length === 0 ? (
                        <p className="text-muted-foreground">No upcoming runs — check the days or cron.</p>
                      ) : (
                        <ul className="space-y-1">
                          {scheduleRuns.map((t, i) => (
                            <li key={t} className="flex items-baseline justify-between gap-3 tabular-nums">
                              <span className={cn(i === 0 ? 'font-medium text-foreground' : 'text-muted-foreground')}>
                                {formatInZone(t, String(params.timezone ?? 'UTC'))} <span className="text-[11px] text-muted-foreground">{zoneAbbreviation(String(params.timezone ?? 'UTC'), t)}</span>
                              </span>
                              <span className="text-[11px] text-muted-foreground">
                                {params.timezone !== localTz ? `${formatInZone(t, localTz)} yours · ` : ''}
                                {formatCountdown(t - now)}
                              </span>
                            </li>
                          ))}
                        </ul>
                      )}
                    </>
                  ) : (
                    <div className="grid grid-cols-2 gap-2">
                      <div className="rounded-lg bg-muted/50 px-3 py-2">
                        <p className="text-[11px] text-muted-foreground">Watches</p>
                        <p className="font-semibold tabular-nums">
                          {matchedCount} deployment{matchedCount === 1 ? '' : 's'}
                        </p>
                      </div>
                      <div className="rounded-lg bg-muted/50 px-3 py-2">
                        <p className="text-[11px] text-muted-foreground">Checked</p>
                        <p className="font-semibold tabular-nums">every {formatSeconds(interval)}</p>
                      </div>
                    </div>
                  )}
                  {type !== 'time_schedule' && matchedCount === 0 && !deploymentsQuery.isLoading && (
                    <p className="flex items-center gap-1.5 text-warning-dark dark:text-warning-light">
                      <CircleAlert className="size-3.5" /> No deployment matches the target right now.
                    </p>
                  )}
                </div>
              </>
            ) : (
              <p className="text-[13px] leading-7 text-muted-foreground">Pick a rule type and this card will read your rule back as a sentence while you fill it in. Click any highlighted value to jump to its field.</p>
            )}
          </CardContent>
          <div className="space-y-2 border-t border-border bg-muted/20 px-5 py-4">
            {submitted && errorCount > 0 && (
              <p className="flex items-center gap-1.5 text-xs font-medium text-destructive">
                <CircleAlert className="size-3.5" />
                {errorCount === 1 ? '1 field needs attention' : `${errorCount} fields need attention`}
              </p>
            )}
            <div className="flex flex-wrap gap-2">
              <Button className="flex-1" disabled={busy} onClick={() => void submit(false)}>
                {busy ? <Loader2 className="size-4 animate-spin" /> : <Save className="size-4" />}
                {editing ? 'Save changes' : 'Create rule'}
              </Button>
              <Button variant="outline" className="flex-1" disabled={busy || !type} onClick={() => void submit(true)}>
                <FlaskConical className="size-4" />
                Save &amp; dry run now
              </Button>
            </div>
            <Link href={editing && rule ? `/rules/${rule.id}` : '/rules'} className="block text-center text-xs text-muted-foreground underline-offset-4 hover:text-foreground hover:underline">
              Cancel
            </Link>
          </div>
        </Card>
      </aside>
    </div>
  );
}
