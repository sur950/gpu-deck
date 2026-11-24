'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { ArrowLeft, ArrowRight, CheckCircle2, Loader2, Rocket, Save, ShieldCheck } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Switch } from '@/components/ui/switch';
import { Stepper, type StepDefinition } from '@/components/shared/stepper';
import { JsonView } from '@/components/shared/json-view';
import { DetailList } from '@/components/shared/detail-list';
import { recordToRows } from '@/components/shared/key-value-editor';
import { useClusters, useDevices, useNodes } from '@/hooks/api/fleet';
import { useAutoPickEngine, useCompatCheck, useEngines, useModelVersions, useModels } from '@/hooks/api/models';
import { useCreateDeployment, useDeployments, usePlacementPreview, useUpdateDeployment } from '@/hooks/api/models-deployments';
import { useDebounce } from '@/hooks/use-debounce';
import { formatBytes } from '@/utils/format';
import { ENGINE_LABELS, MODEL_PRIORITY_LABELS, PRESET_LABELS, type Deployment } from '@/types/models';
import { capsOf, effectiveGpuCount, toCreate, toSpec, toUpdate, type WizardDraft } from '@/components/models/wizard/draft';
import type { StepProps, WizardContext } from '@/components/models/wizard/context';
import { StepEngine, StepModel, engineStepError, modelStepError } from '@/components/models/wizard/step-model-engine';
import { StepPlacement, placementStepError } from '@/components/models/wizard/step-placement';
import { StepPerformance, StepRuntime } from '@/components/models/wizard/step-tuning';

const STEPS: StepDefinition[] = [
  { id: 'model', title: 'Model', description: 'What to serve' },
  { id: 'engine', title: 'Engine', description: 'Compatibility & auto-pick' },
  { id: 'placement', title: 'Placement', description: 'Replicas, GPUs, slices' },
  { id: 'performance', title: 'Performance', description: 'Preset, PD, spec, KV' },
  { id: 'runtime', title: 'Runtime', description: 'Flags, parser, vectors' },
  { id: 'review', title: 'Review', description: 'Validate & create' },
];

function StepReview({ draft, update, ctx }: StepProps) {
  const create = useCreateDeployment();
  const [validated, setValidated] = useState<string | null>(null);
  const spec = toSpec(draft);
  const flags = Object.entries(spec.launch_flags ?? {});
  const placed = ctx.preview?.placements ?? [];
  return (
    <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,1fr)_380px]">
      <div className="grid gap-4 sm:grid-cols-2">
        <Card className="rounded-md shadow-none">
          <CardContent className="space-y-2">
            <p className="text-[10.5px] font-medium tracking-wide text-muted-foreground uppercase">Model</p>
            <DetailList
              items={[
                { label: 'Serving name', value: spec.name, mono: true },
                { label: 'Model', value: ctx.model?.display_name ?? ctx.model?.name },
                { label: 'Version', value: ctx.version ? `${ctx.version.version}${draft.versionId ? ' (pinned)' : ' (default)'}` : '—' },
                { label: 'Engine', value: ctx.engineVersion ? `${ENGINE_LABELS[ctx.engineVersion.engine_name]} ${ctx.engineVersion.version}${draft.engineVersionId ? '' : ' (auto)'}` : 'auto' },
              ]}
            />
          </CardContent>
        </Card>
        <Card className="rounded-md shadow-none">
          <CardContent className="space-y-2">
            <p className="text-[10.5px] font-medium tracking-wide text-muted-foreground uppercase">Placement</p>
            <DetailList
              items={[
                { label: 'Replicas', value: `${spec.replicas} (min ${spec.min_replicas}, max ${spec.max_replicas})` },
                { label: 'GPUs / replica', value: `${spec.gpu_count} · TP ${spec.tp_size} · PP ${spec.pp_size}` },
                { label: 'Strategy', value: spec.placement_strategy },
                { label: 'Slice', value: spec.slice_vram_bytes ? `${formatBytes(spec.slice_vram_bytes)} · ${spec.slice_compute_pct}%` : 'scheduler estimate' },
              ]}
            />
          </CardContent>
        </Card>
        <Card className="rounded-md shadow-none">
          <CardContent className="space-y-2">
            <p className="text-[10.5px] font-medium tracking-wide text-muted-foreground uppercase">Performance</p>
            <DetailList
              items={[
                { label: 'Preset', value: spec.preset ? PRESET_LABELS[spec.preset] : 'custom' },
                { label: 'Context', value: spec.context_size ? `${spec.context_size.toLocaleString()} tokens` : 'model default' },
                { label: 'PD split', value: spec.pd_split ? `${draft.prefillReplicas}P / ${draft.decodeReplicas}D · ${draft.pdTransfer}` : 'off' },
                { label: 'Spec decoding', value: draft.specEnabled ? `${draft.specMethod} · ${draft.numDraftTokens} tokens` : 'off' },
                { label: 'KV sharing', value: spec.kv_sharing ? draft.kvBackend : 'off' },
              ]}
            />
          </CardContent>
        </Card>
        <Card className="rounded-md shadow-none">
          <CardContent className="space-y-2">
            <p className="text-[10.5px] font-medium tracking-wide text-muted-foreground uppercase">Runtime</p>
            <DetailList
              items={[
                { label: 'Tool parser', value: spec.tool_parser ?? `default (${ctx.model?.tool_parser_default ?? 'none'})` },
                { label: 'Priority', value: spec.priority_override ? MODEL_PRIORITY_LABELS[spec.priority_override] : 'inherit' },
                { label: 'Control vectors', value: String(spec.control_vectors?.length ?? 0) },
                { label: 'Env vars', value: String(Object.keys(spec.env_vars ?? {}).length) },
              ]}
            />
            <div className="flex flex-wrap gap-1 pt-1">
              {flags.length === 0 ? (
                <span className="text-[11px] text-muted-foreground">No launch flags</span>
              ) : (
                flags.map(([k, v]) => (
                  <code key={k} className="rounded-sm border border-border bg-muted/40 px-1.5 py-0.5 font-mono text-[10.5px]">
                    {v === true ? k : `${k} ${String(v)}`}
                  </code>
                ))
              )}
            </div>
          </CardContent>
        </Card>
        <div className="sm:col-span-2">
          <p className="mb-1.5 text-xs font-medium text-muted-foreground">Request body</p>
          <JsonView value={toCreate(draft)} maxHeight={260} />
        </div>
      </div>
      <div className="space-y-3 lg:sticky lg:top-4">
        <div className="rounded-lg border border-border p-4">
          <p className="text-sm font-semibold">{ctx.mode === 'edit' ? 'Apply changes' : 'Ready to deploy'}</p>
          <p className="mt-1 text-xs text-muted-foreground">
            {placed.length
              ? `${placed.length} replica(s) on ${[...new Set(placed.map((p) => p.node_name))].join(', ')}.`
              : 'No placement found with these settings.'}{' '}
            {ctx.mode === 'edit' ? 'Engine, model, slice or flag changes roll through the replicas one at a time.' : 'The scheduler places replicas, agents pull weights from their cache and load them.'}
          </p>
          {ctx.mode === 'create' && (
            <label className="mt-3 flex items-center justify-between gap-3 rounded-md bg-muted/40 px-3 py-2 text-xs">
              Start immediately
              <Switch checked={draft.start} onCheckedChange={(v) => update({ start: v })} aria-label="Start immediately" />
            </label>
          )}
          {ctx.mode === 'create' && (
            <Button
              variant="outline"
              size="sm"
              className="mt-3 w-full rounded-sm"
              disabled={create.isPending}
              onClick={() =>
                create.mutate(toCreate(draft, true), {
                  onSuccess: () => setValidated('Valid — the control plane accepted the spec (dry run, nothing created).'),
                  onError: (e) => setValidated(e instanceof Error ? e.message : 'Validation failed'),
                })
              }
            >
              {create.isPending ? <Loader2 className="size-4 animate-spin" /> : <ShieldCheck className="size-4" />}
              Validate (dry run)
            </Button>
          )}
          {validated && (
            <p className="mt-2 flex items-start gap-1.5 text-[11px] text-muted-foreground">
              <CheckCircle2 className="mt-px size-3.5 shrink-0 text-success" />
              {validated}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}

interface DeploymentWizardProps {
  initial: WizardDraft;
  current?: Deployment | null;
}

export function DeploymentWizard({ initial, current = null }: DeploymentWizardProps) {
  const router = useRouter();
  const mode = current ? 'edit' : 'create';
  const [draft, setDraft] = useState<WizardDraft>(initial);
  const [step, setStep] = useState(0);
  const [reached, setReached] = useState(current ? STEPS.length - 1 : 0);
  const update = (patch: Partial<WizardDraft>) => setDraft((d) => ({ ...d, ...patch }));

  const models = useModels({ page_size: 500 });
  const versions = useModelVersions(draft.modelId);
  const engines = useEngines();
  const nodes = useNodes({ page_size: 500 }, { live: false });
  const devices = useDevices({ page_size: 500 });
  const clusters = useClusters({ page_size: 100 });
  const deployments = useDeployments({ page_size: 500 }, { live: false });
  const autoPick = useAutoPickEngine();
  const compat = useCompatCheck();
  const preview = usePlacementPreview();
  const create = useCreateDeployment();
  const save = useUpdateDeployment();

  const model = models.data?.items.find((m) => m.id === draft.modelId);
  const versionList = versions.data?.items ?? [];
  const version = draft.versionId ? versionList.find((v) => v.id === draft.versionId) : (versionList.find((v) => v.is_default) ?? model?.default_version ?? undefined);
  const engineList = engines.data ?? [];
  const allEngineVersions = engineList.flatMap((e) => e.versions ?? []);
  const engineVersion = draft.engineVersionId ? allEngineVersions.find((v) => v.id === draft.engineVersionId) : (autoPick.data?.engine_version ?? undefined);
  const engine = engineList.find((e) => e.id === engineVersion?.engine_id);

  // Compatibility + auto-pick whenever the version changes.
  const versionKey = version?.id ?? '';
  const { mutate: runAutoPick } = autoPick;
  const { mutate: runCompat } = compat;
  // Device context for the compatibility check: without a node the backend cannot check VRAM fit
  // and answers "unknown". Use the pinned node, else the only online node; a single vendor
  // (picked, or the only one in the fleet) still rules out engines that cannot run on it.
  const onlineNodes = (nodes.data?.items ?? []).filter((n) => n.status === 'online');
  const compatNode = draft.pinNodeIds.length === 1 ? draft.pinNodeIds[0] : onlineNodes.length === 1 ? onlineNodes[0].id : null;
  const fleetVendors = [...new Set((devices.data?.items ?? []).map((d) => d.vendor))];
  const compatVendor = compatNode ? null : draft.vendors.length === 1 ? draft.vendors[0] : fleetVendors.length === 1 ? fleetVendors[0] : null;
  useEffect(() => {
    if (!versionKey) return;
    runAutoPick({ model_version_id: versionKey, gpu_count: 1, preset: draft.preset, context_size: draft.contextSize ? Number(draft.contextSize) : null, cluster_id: draft.clusterId || null });
    runCompat({ model_version_id: versionKey, gpu_count: 1, context_size: draft.contextSize ? Number(draft.contextSize) : null, node_id: compatNode, device_vendor: compatVendor });
    // Re-check only when the version (or the device context) changes; later field edits are covered by the placement preview.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [versionKey, compatNode, compatVendor, runAutoPick, runCompat]);

  // Prefill runtime defaults once per model / engine combination (create mode only).
  const prefilled = useRef('');
  useEffect(() => {
    if (mode === 'edit' || !model || !autoPick.data) return;
    const key = `${model.id}:${autoPick.data.engine_version?.id ?? ''}`;
    if (prefilled.current === key) return;
    prefilled.current = key;
    setDraft((d) => ({
      ...d,
      flags: d.flags.length ? d.flags : recordToRows({ ...(model.default_launch_flags ?? {}), ...(autoPick.data?.suggested_launch_flags ?? {}) }),
      toolParser: d.toolParser || autoPick.data?.suggested_tool_parser || '',
    }));
  }, [mode, model, autoPick.data]);

  // Placement preview, debounced on every spec change.
  const specKey = useDebounce(JSON.stringify(draft.modelId ? toSpec({ ...draft, name: draft.name || 'preview' }) : null), 400);
  const { mutate: runPreview } = preview;
  useEffect(() => {
    if (!specKey || specKey === 'null') return;
    const spec = JSON.parse(specKey) as ReturnType<typeof toSpec>;
    runPreview({ deployment: { ...spec, name: spec.name || 'preview' } });
  }, [specKey, runPreview]);

  const takenNames = useMemo(() => new Set((deployments.data?.items ?? []).filter((d) => d.id !== current?.id).map((d) => d.name)), [deployments.data, current]);

  // An empty serving name (model preset from the URL, or a freshly picked model) shows the first free name for the model.
  let suggestedName = model?.name ?? '';
  for (let i = 2; model && takenNames.has(suggestedName); i += 1) suggestedName = `${model.name}-${i}`;
  const view: WizardDraft = mode === 'create' && !draft.name && suggestedName ? { ...draft, name: suggestedName } : draft;

  const ctx: WizardContext = {
    mode,
    current,
    models: models.data?.items ?? [],
    model,
    versions: versionList,
    version,
    engines: engineList,
    engineVersion,
    engine,
    caps: capsOf(engine),
    autoPick: autoPick.data,
    autoPickLoading: autoPick.isPending,
    compat: compat.data?.items ?? [],
    compatLoading: compat.isPending,
    nodes: nodes.data?.items ?? [],
    devices: devices.data?.items ?? [],
    clusters: clusters.data?.items ?? [],
    preview: preview.data,
    previewLoading: preview.isPending,
    takenNames,
  };

  const errors = [modelStepError({ draft: view, ctx }), engineStepError({ draft: view, ctx }), placementStepError({ draft: view, ctx }), null, null, null];
  const firstError = errors.slice(0, STEPS.length - 1).findIndex((e) => e);
  const stepError = errors[step];
  const props: StepProps = { draft: view, update, ctx };

  function go(index: number) {
    setStep(index);
    setReached((r) => Math.max(r, index));
    document.querySelector('main')?.scrollTo({ top: 0, behavior: 'smooth' });
  }

  function submit() {
    if (current) {
      save.mutate({ id: current.id, body: toUpdate(view) }, { onSuccess: () => router.push(`/deployments/${current.id}`) });
    } else {
      create.mutate(toCreate(view), { onSuccess: (d) => router.push(`/deployments/${d.id}`) });
    }
  }

  const last = step === STEPS.length - 1;
  const pending = create.isPending || save.isPending;

  return (
    <div className="space-y-6">
      <Card className="rounded-md shadow-premium">
        <CardContent>
          <Stepper steps={STEPS} current={step} reached={reached} onSelect={go} />
        </CardContent>
      </Card>

      <div className="min-h-[420px]">
        {step === 0 && <StepModel {...props} />}
        {step === 1 && <StepEngine {...props} />}
        {step === 2 && <StepPlacement {...props} />}
        {step === 3 && <StepPerformance {...props} />}
        {step === 4 && <StepRuntime {...props} />}
        {step === 5 && <StepReview {...props} />}
      </div>

      <div className="sticky bottom-0 z-10 -mx-4 flex items-center justify-between gap-3 border-t border-border bg-background/90 px-4 py-3 backdrop-blur sm:-mx-6 sm:px-6 lg:-mx-8 lg:px-8">
        <Button variant="outline" size="sm" className="rounded-sm" onClick={() => (step === 0 ? router.back() : go(step - 1))}>
          <ArrowLeft className="size-4" />
          {step === 0 ? 'Cancel' : 'Back'}
        </Button>
        <div className="flex items-center gap-3">
          {stepError && <span className="hidden text-xs text-muted-foreground sm:inline">{stepError}</span>}
          {last ? (
            <Button size="sm" className="rounded-sm" disabled={pending || firstError >= 0} onClick={submit} title={firstError >= 0 ? `Fix step ${firstError + 1}: ${errors[firstError]}` : undefined}>
              {pending ? <Loader2 className="size-4 animate-spin" /> : mode === 'edit' ? <Save className="size-4" /> : <Rocket className="size-4" />}
              {mode === 'edit' ? 'Save changes' : view.start ? `Deploy ${effectiveGpuCount(view) > 1 ? `on ${effectiveGpuCount(view)} GPUs × ${view.replicas}` : `${view.replicas} replica${view.replicas === 1 ? '' : 's'}`}` : 'Create (stopped)'}
            </Button>
          ) : (
            <Button size="sm" className="rounded-sm" disabled={!!stepError && step <= 2} onClick={() => go(step + 1)}>
              Next: {STEPS[step + 1].title}
              <ArrowRight className="size-4" />
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}
