'use client';

import { useState } from 'react';
import { Loader2, Play, Save, X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Field } from '@/components/shared/form-dialog';
import { useDeployments } from '@/hooks/api/models-deployments';
import { cn } from '@/lib/utils';
import type { Benchmark, BenchmarkCreate } from '@/types/models';

const SWEEPS: { label: string; levels: number[] }[] = [
  { label: 'Interactive', levels: [1, 2, 4, 8, 12, 16] },
  { label: 'Broad', levels: [1, 2, 4, 8, 16, 32, 64] },
  { label: 'Batch', levels: [8, 16, 32, 48, 64, 96] },
];

function numOrNull(v: string): number | null {
  const n = Number(v);
  return v.trim() !== '' && Number.isFinite(n) ? n : null;
}

export interface BenchmarkFormValue {
  body: BenchmarkCreate;
  runNow: boolean;
}

/**
 * Benchmark definition: deployment, concurrency sweep, workload shape and
 * the latency SLO the auto-tune run optimises for.
 */
export function BenchmarkForm({ initial, presetDeployment, isPending, onSubmit, onCancel }: { initial?: Benchmark; presetDeployment?: string; isPending: boolean; onSubmit: (v: BenchmarkFormValue) => void; onCancel: () => void }) {
  const deployments = useDeployments({ page_size: 200, origin: 'managed' }, { live: false });
  const workload = (initial?.workload ?? {}) as Record<string, unknown>;
  const slo = (initial?.target_slo ?? {}) as Record<string, unknown>;
  const str = (v: unknown, d = '') => (v === undefined || v === null ? d : String(v));

  const [name, setName] = useState(initial?.name ?? '');
  const [description, setDescription] = useState(initial?.description ?? '');
  const [deploymentId, setDeploymentId] = useState(initial?.deployment_id ?? presetDeployment ?? '');
  const [levels, setLevels] = useState<number[]>(initial?.concurrency_levels ?? SWEEPS[0].levels);
  const [levelInput, setLevelInput] = useState('');
  const [duration, setDuration] = useState(str(initial?.duration_seconds, '60'));
  const [inputTokens, setInputTokens] = useState(str(workload.input_tokens, '512'));
  const [outputTokens, setOutputTokens] = useState(str(workload.output_tokens, '256'));
  const [requests, setRequests] = useState(str(workload.requests_per_level, '120'));
  const [stream, setStream] = useState(workload.stream !== false);
  const [dataset, setDataset] = useState(str(workload.dataset, 'synthetic'));
  const [ttft, setTtft] = useState(str(slo.ttft_ms_p95, '600'));
  const [e2e, setE2e] = useState(str(slo.e2e_ms_p95));
  const [tpot, setTpot] = useState(str(slo.tpot_ms_p95));
  const [minTps, setMinTps] = useState(str(slo.min_generation_tps));
  const [maxErr, setMaxErr] = useState(str(slo.max_error_rate_pct, '1'));
  const [autoTune, setAutoTune] = useState(initial?.auto_tune ?? true);
  const [runNow, setRunNow] = useState(!initial);
  const valid = !!name.trim() && !!deploymentId && levels.length > 0 && Number(duration) > 0;

  function addLevel() {
    const n = Math.round(Number(levelInput));
    if (n > 0 && n <= 1024 && !levels.includes(n)) setLevels([...levels, n].sort((a, b) => a - b));
    setLevelInput('');
  }

  function submit() {
    const target_slo = Object.fromEntries(
      Object.entries({ ttft_ms_p95: numOrNull(ttft), e2e_ms_p95: numOrNull(e2e), tpot_ms_p95: numOrNull(tpot), min_generation_tps: numOrNull(minTps), max_error_rate_pct: numOrNull(maxErr) }).filter(([, v]) => v !== null),
    );
    onSubmit({
      runNow,
      body: {
        name: name.trim(),
        description: description.trim() || null,
        deployment_id: deploymentId,
        concurrency_levels: levels,
        duration_seconds: Number(duration),
        workload: { input_tokens: Number(inputTokens) || 512, output_tokens: Number(outputTokens) || 0, requests_per_level: Number(requests) || 100, stream, dataset },
        target_slo,
        auto_tune: autoTune,
      },
    });
  }

  return (
    <div className="space-y-6">
      <Card className="rounded-md shadow-premium">
        <CardHeader>
          <CardTitle>Target</CardTitle>
          <CardDescription>The run picks one ready instance with no traffic (or drains one first) and drives it through the API layer, like a real client.</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4 sm:grid-cols-2">
          <Field label="Name" htmlFor="bm-name">
            <Input id="bm-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="qwen3 chat latency sweep" />
          </Field>
          <Field label="Deployment">
            <Select value={deploymentId || null} onValueChange={(v) => setDeploymentId(String(v ?? ''))} disabled={!!initial}>
              <SelectTrigger className="w-full">
                <SelectValue placeholder="Pick a deployment">{(v: unknown) => deployments.data?.items.find((d) => d.id === v)?.name ?? 'Pick a deployment'}</SelectValue>
              </SelectTrigger>
              <SelectContent>
                {(deployments.data?.items ?? []).map((d) => (
                  <SelectItem key={d.id} value={d.id} disabled={(d.ready_replicas ?? 0) === 0}>
                    {d.name} · {d.ready_replicas ?? 0} ready
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>
          <Field label="Description" className="sm:col-span-2" htmlFor="bm-desc">
            <Textarea id="bm-desc" rows={2} value={description} onChange={(e) => setDescription(e.target.value)} placeholder="What question this run answers" />
          </Field>
        </CardContent>
      </Card>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card className="rounded-md shadow-premium">
          <CardHeader>
            <CardTitle>Concurrency sweep</CardTitle>
            <CardDescription>Each level runs for the duration below with that many requests in flight.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex flex-wrap gap-1.5">
              {SWEEPS.map((s) => (
                <Button key={s.label} type="button" variant="outline" size="xs" onClick={() => setLevels(s.levels)}>
                  {s.label}
                </Button>
              ))}
            </div>
            <div className="flex flex-wrap items-center gap-1.5 rounded-lg border border-border p-2">
              {levels.map((l) => (
                <span key={l} className="inline-flex items-center gap-1 rounded-sm bg-muted px-2 py-1 font-mono text-xs tabular-nums">
                  {l}
                  <button type="button" onClick={() => setLevels(levels.filter((x) => x !== l))} className="text-muted-foreground hover:text-foreground" aria-label={`Remove ${l}`}>
                    <X className="size-3" />
                  </button>
                </span>
              ))}
              <Input
                value={levelInput}
                onChange={(e) => setLevelInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ',') {
                    e.preventDefault();
                    addLevel();
                  }
                }}
                onBlur={addLevel}
                placeholder="add level ↵"
                className="h-7 w-28 border-0 font-mono text-xs shadow-none focus-visible:ring-0"
                aria-label="Add concurrency level"
              />
            </div>
            <div className="grid gap-3 sm:grid-cols-3">
              <Field label="Seconds / level" htmlFor="bm-dur">
                <Input id="bm-dur" type="number" min={5} value={duration} onChange={(e) => setDuration(e.target.value)} className="font-mono text-sm" />
              </Field>
              <Field label="Requests / level" htmlFor="bm-req">
                <Input id="bm-req" type="number" min={1} value={requests} onChange={(e) => setRequests(e.target.value)} className="font-mono text-sm" />
              </Field>
              <Field label="Dataset">
                <Select value={dataset} onValueChange={(v) => setDataset(String(v))}>
                  <SelectTrigger className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="synthetic">synthetic</SelectItem>
                    <SelectItem value="sharegpt-short">sharegpt-short</SelectItem>
                    <SelectItem value="docs-chunks">docs-chunks</SelectItem>
                  </SelectContent>
                </Select>
              </Field>
              <Field label="Input tokens" htmlFor="bm-in">
                <Input id="bm-in" type="number" min={1} value={inputTokens} onChange={(e) => setInputTokens(e.target.value)} className="font-mono text-sm" />
              </Field>
              <Field label="Output tokens" htmlFor="bm-out">
                <Input id="bm-out" type="number" min={0} value={outputTokens} onChange={(e) => setOutputTokens(e.target.value)} className="font-mono text-sm" />
              </Field>
              <label className="flex items-end justify-between gap-2 pb-2 text-xs">
                Stream
                <Switch checked={stream} onCheckedChange={setStream} aria-label="Stream responses" />
              </label>
            </div>
          </CardContent>
        </Card>

        <Card className="rounded-md shadow-premium">
          <CardHeader>
            <CardTitle>Service level objective</CardTitle>
            <CardDescription>A level passes when every target holds. Leave a field empty to ignore it.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="TTFT p95 (ms)" htmlFor="slo-ttft">
                <Input id="slo-ttft" type="number" value={ttft} onChange={(e) => setTtft(e.target.value)} className="font-mono text-sm" />
              </Field>
              <Field label="End-to-end p95 (ms)" htmlFor="slo-e2e">
                <Input id="slo-e2e" type="number" value={e2e} onChange={(e) => setE2e(e.target.value)} className="font-mono text-sm" />
              </Field>
              <Field label="Time per output token p95 (ms)" htmlFor="slo-tpot">
                <Input id="slo-tpot" type="number" value={tpot} onChange={(e) => setTpot(e.target.value)} className="font-mono text-sm" />
              </Field>
              <Field label="Min generation tok/s" htmlFor="slo-tps">
                <Input id="slo-tps" type="number" value={minTps} onChange={(e) => setMinTps(e.target.value)} className="font-mono text-sm" />
              </Field>
              <Field label="Max error rate (%)" htmlFor="slo-err">
                <Input id="slo-err" type="number" step="0.1" value={maxErr} onChange={(e) => setMaxErr(e.target.value)} className="font-mono text-sm" />
              </Field>
            </div>
            <label className={cn('flex items-start justify-between gap-4 rounded-lg border p-3', autoTune ? 'border-foreground/40 bg-muted/30' : 'border-border')}>
              <span>
                <span className="block text-xs font-medium">Auto-tune</span>
                <span className="block text-[11px] text-muted-foreground">Also sweeps launch-flag variants (batch size, parallel slots) and recommends the highest concurrency that meets the SLO, ready to apply to the deployment.</span>
              </span>
              <Switch checked={autoTune} onCheckedChange={setAutoTune} aria-label="Auto-tune" />
            </label>
          </CardContent>
        </Card>
      </div>

      <div className="flex flex-wrap items-center justify-end gap-3">
        {!initial && (
          <label className="mr-auto flex items-center gap-2 text-xs text-muted-foreground">
            <Switch size="sm" checked={runNow} onCheckedChange={setRunNow} aria-label="Run now" />
            Start the first run right away
          </label>
        )}
        <Button variant="outline" size="sm" className="rounded-sm" onClick={onCancel} disabled={isPending}>
          Cancel
        </Button>
        <Button size="sm" className="rounded-sm" disabled={!valid || isPending} onClick={submit}>
          {isPending ? <Loader2 className="size-4 animate-spin" /> : runNow && !initial ? <Play className="size-4" /> : <Save className="size-4" />}
          {initial ? 'Save benchmark' : runNow ? 'Create and run' : 'Create benchmark'}
        </Button>
      </div>
    </div>
  );
}
