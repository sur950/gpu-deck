'use client';

import { useMemo, useState } from 'react';
import { Loader2, Scale, Save } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Slider } from '@/components/ui/slider';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { CodeBlock } from '@/components/shared/code-block';
import { FormDialog, Field } from '@/components/shared/form-dialog';
import { usePlacementPreview, useScaleDeployment, useSetTrafficWeights } from '@/hooks/api/models-deployments';
import { useSystemInfo } from '@/hooks/api/models';
import { cn } from '@/lib/utils';
import { SERVING_FORMAT_LABELS, type Deployment, type Instance, type ModelType, type ServingFormat, type TrafficStrategy } from '@/types/models';

// ─── Scale ──────────────────────────────────────────────────────────────────

export function ScaleDialog({ deployment, open, onOpenChange }: { deployment: Deployment; open: boolean; onOpenChange: (open: boolean) => void }) {
  const scale = useScaleDeployment();
  const preview = usePlacementPreview();
  const [replicas, setReplicas] = useState(deployment.replicas);
  const [timeout, setTimeoutValue] = useState('120');
  const max = Math.max(deployment.max_replicas, 1);
  const current = deployment.instances?.length ?? deployment.ready_replicas ?? 0;
  const { mutate: runPreview } = preview;
  const adding = replicas > current;
  return (
    <FormDialog
      open={open}
      onOpenChange={onOpenChange}
      icon={<Scale className="size-4 text-muted-foreground" />}
      title={`Scale ${deployment.name}`}
      description={`Between ${deployment.min_replicas} and ${max} replicas (raise the maximum by editing the deployment). New replicas are placed by the scheduler; removed ones are drained first.`}
      submitLabel={replicas === deployment.replicas ? 'No change' : `Scale to ${replicas}`}
      isPending={scale.isPending}
      submitDisabled={replicas === deployment.replicas}
      footerNote={adding && preview.data ? (preview.data.feasible ? `Fits: ${preview.data.placements.map((p) => p.node_name).join(', ')}` : 'Not enough free VRAM for every new replica') : undefined}
      onSubmit={() => scale.mutate({ id: deployment.id, body: { replicas, drain_timeout_seconds: Number(timeout) || 120 } }, { onSuccess: () => onOpenChange(false) })}
    >
      <Field label="Replicas" aside={<span className="font-mono text-sm tabular-nums">{replicas}</span>}>
        <Slider
          min={deployment.min_replicas}
          max={max}
          step={1}
          value={[replicas]}
          onValueChange={(v) => {
            const n = Array.isArray(v) ? Number(v[0]) : Number(v);
            setReplicas(n);
            if (n > current) runPreview({ deployment_id: deployment.id, replicas: n });
          }}
          aria-label="Replicas"
        />
        <div className="flex justify-between text-[10.5px] text-muted-foreground tabular-nums">
          <span>min {deployment.min_replicas}</span>
          <span>now {current}</span>
          <span>max {max}</span>
        </div>
      </Field>
      <Field label="Drain timeout (s)" hint="For replicas being removed: wait this long for in-flight requests." htmlFor="scale-timeout">
        <Input id="scale-timeout" type="number" min={0} max={3600} value={timeout} onChange={(e) => setTimeoutValue(e.target.value)} className="w-28 font-mono text-sm" />
      </Field>
    </FormDialog>
  );
}

// ─── Traffic weights ────────────────────────────────────────────────────────

export function TrafficCard({ deployment, instances }: { deployment: Deployment; instances: Instance[] }) {
  const set = useSetTrafficWeights();
  const live = instances.filter((i) => i.state !== 'stopped');
  const initialStrategy = ((deployment.traffic_config ?? {}) as { strategy?: TrafficStrategy }).strategy ?? 'least_sessions';
  const [strategy, setStrategy] = useState<TrafficStrategy>(initialStrategy);
  const [weights, setWeights] = useState<Record<string, number>>(() => Object.fromEntries(live.map((i) => [i.id, i.traffic_weight])));
  const ids = live.map((i) => i.id).join(',');
  const merged = useMemo(() => Object.fromEntries(live.map((i) => [i.id, weights[i.id] ?? i.traffic_weight])), [ids, weights]); // eslint-disable-line react-hooks/exhaustive-deps
  const total = Object.values(merged).reduce((s, w) => s + w, 0);
  const dirty = strategy !== initialStrategy || live.some((i) => merged[i.id] !== i.traffic_weight);

  return (
    <Card className="rounded-md shadow-premium">
      <CardHeader className="flex flex-row items-start justify-between gap-3">
        <div>
          <CardTitle>Traffic</CardTitle>
          <CardDescription>How the API layer spreads requests. Weight 0 drains a replica from load balancing without unloading it.</CardDescription>
        </div>
        <Select value={strategy} onValueChange={(v) => setStrategy(v as TrafficStrategy)}>
          <SelectTrigger size="sm" className="w-40">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="least_sessions">least_sessions</SelectItem>
            <SelectItem value="weighted">weighted</SelectItem>
            <SelectItem value="round_robin">round_robin</SelectItem>
          </SelectContent>
        </Select>
      </CardHeader>
      <CardContent className="space-y-4">
        {live.length === 0 ? (
          <p className="py-4 text-center text-sm text-muted-foreground">No running replicas.</p>
        ) : (
          <>
            <div className="flex h-2.5 gap-[2px] overflow-hidden rounded-full bg-muted" aria-hidden>
              {live.map((i, n) => (
                <div key={i.id} className="h-full transition-[width] duration-300" style={{ width: `${total ? (merged[i.id] / total) * 100 : 0}%`, backgroundColor: `rgb(var(--chart-${(n % 4) + 2}))` }} />
              ))}
            </div>
            {live.map((i, n) => (
              <div key={i.id} className="grid grid-cols-[minmax(0,1fr)_120px_76px] items-center gap-3">
                <div className="min-w-0">
                  <div className="flex items-center gap-1.5 text-xs font-medium">
                    <span className="size-2 rounded-[2px]" style={{ backgroundColor: `rgb(var(--chart-${(n % 4) + 2}))` }} aria-hidden />
                    {i.node_name}
                    <span className="font-mono text-[10.5px] text-muted-foreground">{i.id.slice(0, 8)}</span>
                  </div>
                  <div className="text-[10.5px] text-muted-foreground">{i.state}</div>
                </div>
                <Slider min={0} max={100} step={5} value={[merged[i.id]]} onValueChange={(v) => setWeights((w) => ({ ...w, [i.id]: Array.isArray(v) ? Number(v[0]) : Number(v) }))} aria-label={`Weight for ${i.node_name}`} />
                <span className="text-right font-mono text-xs tabular-nums">
                  {merged[i.id]}
                  <span className="text-muted-foreground"> · {total ? Math.round((merged[i.id] / total) * 100) : 0}%</span>
                </span>
              </div>
            ))}
            {strategy !== 'weighted' && <p className="text-[11px] text-muted-foreground">Weights only shape traffic with the weighted strategy; other strategies still skip replicas at weight 0.</p>}
            <div className="flex justify-end">
              <Button size="sm" className="rounded-sm" disabled={!dirty || set.isPending} onClick={() => set.mutate({ id: deployment.id, body: { strategy, weights: merged } })}>
                {set.isPending ? <Loader2 className="size-4 animate-spin" /> : <Save className="size-4" />}
                Apply
              </Button>
            </div>
          </>
        )}
      </CardContent>
    </Card>
  );
}

// ─── Endpoints ──────────────────────────────────────────────────────────────

export interface ServedName {
  name: string;
  scope: 'deployment' | 'model';
  hint: string;
}

/**
 * Names the API layer answers to for a deployment: its own (unique) name addresses only this
 * deployment; the model name balances across every deployment of the model (e.g. an SGLang and a
 * TensorRT-LLM deployment of one model). A deployment named like its model has just the shared name.
 */
export function servedNames(d: Pick<Deployment, 'name' | 'model_name'>): ServedName[] {
  const model = d.model_name?.trim();
  if (model && model.toLowerCase() === d.name.toLowerCase()) {
    return [{ name: model, scope: 'model', hint: 'Model name: balanced across every deployment of this model' }];
  }
  const out: ServedName[] = [{ name: d.name, scope: 'deployment', hint: 'This deployment only' }];
  if (model) out.push({ name: model, scope: 'model', hint: 'Balanced across every deployment of this model' });
  return out;
}

function snippets(base: string, prefixes: Record<ServingFormat, string>, model: string, type: ModelType | undefined): Partial<Record<ServingFormat, { title: string; code: string }[]>> {
  const key = '$GPUDECK_API_KEY';
  const openai = `${base}${prefixes.openai}`;
  const anthropic = `${base}${prefixes.anthropic}`;
  const ollama = `${base}${prefixes.ollama}`;
  const chat = !type || ['chat', 'thinking', 'vision'].includes(type);
  const out: Partial<Record<ServingFormat, { title: string; code: string }[]>> = {};
  if (chat) {
    out.openai = [
      {
        title: 'Chat completions (streaming)',
        code: `curl ${openai}/chat/completions \\\n  -H "Authorization: Bearer ${key}" \\\n  -H "Content-Type: application/json" \\\n  -d '{\n    "model": "${model}",\n    "stream": true,\n    "messages": [{"role": "user", "content": "Say hello in one sentence."}]\n  }'`,
      },
      { title: 'Python (openai SDK)', code: `import os\nfrom openai import OpenAI\n\nclient = OpenAI(base_url="${openai}", api_key=os.environ["GPUDECK_API_KEY"])\nresp = client.chat.completions.create(\n    model="${model}",\n    messages=[{"role": "user", "content": "Say hello."}],\n)\nprint(resp.choices[0].message.content)` },
    ];
    out.anthropic = [
      {
        title: 'Messages',
        code: `curl ${anthropic}/messages \\\n  -H "x-api-key: ${key}" \\\n  -H "anthropic-version: 2023-06-01" \\\n  -H "Content-Type: application/json" \\\n  -d '{\n    "model": "${model}",\n    "max_tokens": 512,\n    "messages": [{"role": "user", "content": "Say hello in one sentence."}]\n  }'`,
      },
    ];
    out.ollama = [
      { title: 'Chat', code: `curl ${ollama}/chat \\\n  -H "Authorization: Bearer ${key}" \\\n  -d '{\n    "model": "${model}",\n    "messages": [{"role": "user", "content": "Say hello."}],\n    "stream": false\n  }'` },
    ];
  } else if (type === 'embedding') {
    out.openai = [{ title: 'Embeddings', code: `curl ${openai}/embeddings \\\n  -H "Authorization: Bearer ${key}" \\\n  -H "Content-Type: application/json" \\\n  -d '{"model": "${model}", "input": ["GPU Deck routes this to the embedding engine."]}'` }];
    out.ollama = [{ title: 'Embed', code: `curl ${ollama}/embed \\\n  -H "Authorization: Bearer ${key}" \\\n  -d '{"model": "${model}", "input": "hello world"}'` }];
  } else if (type === 'rerank') {
    out.openai = [{ title: 'Rerank', code: `curl ${openai}/rerank \\\n  -H "Authorization: Bearer ${key}" \\\n  -H "Content-Type: application/json" \\\n  -d '{"model": "${model}", "query": "gpu", "documents": ["GPUs", "CPUs"]}'` }];
  } else if (type === 'stt') {
    out.openai = [{ title: 'Transcription', code: `curl ${openai}/audio/transcriptions \\\n  -H "Authorization: Bearer ${key}" \\\n  -F model=${model} -F file=@meeting.wav` }];
  } else if (type === 'tts') {
    out.openai = [{ title: 'Speech', code: `curl ${openai}/audio/speech \\\n  -H "Authorization: Bearer ${key}" \\\n  -H "Content-Type: application/json" \\\n  -d '{"model": "${model}", "input": "Hello from GPU Deck", "voice": "alloy"}' --output hello.mp3` }];
  } else if (type === 'image') {
    out.openai = [{ title: 'Image generation', code: `curl ${openai}/images/generations \\\n  -H "Authorization: Bearer ${key}" \\\n  -H "Content-Type: application/json" \\\n  -d '{"model": "${model}", "prompt": "a GPU rack at dawn", "size": "1024x1024"}'` }];
  } else if (type === 'video') {
    out.openai = [{ title: 'Video (async)', code: `curl ${openai}/videos \\\n  -H "Authorization: Bearer ${key}" \\\n  -H "Content-Type: application/json" \\\n  -d '{"model": "${model}", "prompt": "a slow pan over a server room"}'` }];
  }
  return out;
}

export function EndpointsCard({ deployment }: { deployment: Deployment }) {
  const info = useSystemInfo();
  const base = (info.data?.api_layer_url ?? 'https://<api-layer>').replace(/\/$/, '');
  const prefixes: Record<ServingFormat, string> = {
    openai: info.data?.serving?.openai_prefix ?? '/openai/v1',
    anthropic: info.data?.serving?.anthropic_prefix ?? '/anthropic/v1',
    ollama: info.data?.serving?.ollama_prefix ?? '/ollama/api',
  };
  const names = servedNames(deployment);
  const [served, setServed] = useState(names[0].name);
  const all = snippets(base, prefixes, served, deployment.model_type);
  const formats = (Object.keys(SERVING_FORMAT_LABELS) as ServingFormat[]).filter((f) => all[f]);
  const [tab, setTab] = useState<ServingFormat>(formats[0] ?? 'openai');
  return (
    <Card className="rounded-md shadow-premium">
      <CardHeader>
        <CardTitle>Call it</CardTitle>
        <CardDescription>
          Through the API layer with an API key (<span className="font-mono">gd-…</span>). The bare <span className="font-mono">/v1</span> paths also work.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="space-y-1.5">
          <p className="text-xs font-medium text-muted-foreground">Model name in requests</p>
          <div className="flex flex-wrap gap-1.5" role="radiogroup" aria-label="Model name in requests">
            {names.map((n) => (
              <button
                key={n.name}
                type="button"
                role="radio"
                aria-checked={served === n.name}
                onClick={() => setServed(n.name)}
                title={n.hint}
                className={cn(
                  'flex max-w-full flex-col items-start rounded-sm border px-2.5 py-1.5 text-left transition-colors',
                  served === n.name ? 'border-primary/60 bg-primary/5' : 'border-border hover:bg-muted',
                )}
              >
                <span className="max-w-full truncate font-mono text-xs font-medium">{n.name}</span>
                <span className="text-[10.5px] text-muted-foreground">{n.hint}</span>
              </button>
            ))}
          </div>
        </div>
        <Tabs value={tab} onValueChange={(v) => setTab(v as ServingFormat)}>
          <TabsList className="bg-muted">
            {(Object.keys(SERVING_FORMAT_LABELS) as ServingFormat[]).map((f) => (
              <TabsTrigger key={f} value={f} disabled={!all[f]} className={cn('px-3 text-xs')}>
                {SERVING_FORMAT_LABELS[f]}
              </TabsTrigger>
            ))}
          </TabsList>
          {formats.map((f) => (
            <TabsContent key={f} value={f} className="mt-3 space-y-3">
              {all[f]?.map((s) => (
                <div key={s.title} className="space-y-1.5">
                  <p className="text-xs font-medium text-muted-foreground">{s.title}</p>
                  <CodeBlock code={s.code} language={s.title.startsWith('Python') ? 'python' : 'shell'} />
                </div>
              ))}
            </TabsContent>
          ))}
        </Tabs>
      </CardContent>
    </Card>
  );
}
