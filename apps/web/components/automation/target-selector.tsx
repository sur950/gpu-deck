'use client';

import { useMemo, useState } from 'react';
import { Check, Filter, Info, Plus, Trash2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu';
import { KeyValueEditor, type KeyValueRow } from '@/components/shared/key-value-editor';
import { cn } from '@/lib/utils';
import type { EngineName, ModelPriority, ModelType, RefCluster, RefDeployment, RefModel, RefNode, TargetSelector } from '@/hooks/api/automation-api';
import { MultiPick, type PickOption } from '@/components/automation/multi-pick';
import { Segmented } from '@/components/automation/segmented';
import { compactTarget, matchesTarget, type MatchableDeployment } from '@/components/automation/target-match';
import { ENGINE_LABELS, MODEL_TYPE_LABELS, PRIORITY_LABELS } from '@/components/automation/rule-meta';

export const PRIORITY_DOT: Record<string, string> = {
  always_on: 'bg-success',
  normal: 'bg-chart-2',
  evictable: 'bg-muted-foreground/60',
};

export function toMatchable(d: RefDeployment, nodes: RefNode[]): MatchableDeployment {
  const nodeIds = [...new Set(d.instances.map((i) => i.nodeId))];
  return {
    id: d.id,
    name: d.name,
    modelId: d.modelId,
    clusterId: d.clusterId,
    modelType: d.modelType,
    priority: d.priority,
    engine: d.engineName,
    nodeIds,
    nodeLabels: nodeIds.map((id) => nodes.find((n) => n.id === id)?.labels ?? {}),
  };
}

type ConditionKey = keyof TargetSelector;

const CONDITIONS: { key: ConditionKey; label: string }[] = [
  { key: 'deployment_ids', label: 'Deployment is' },
  { key: 'priorities', label: 'Priority is' },
  { key: 'model_types', label: 'Model type is' },
  { key: 'engines', label: 'Engine is' },
  { key: 'model_ids', label: 'Model is' },
  { key: 'node_ids', label: 'Runs on node' },
  { key: 'cluster_ids', label: 'In cluster' },
  { key: 'labels', label: 'Node labels match' },
  { key: 'name_glob', label: 'Name matches' },
  { key: 'exclude_deployment_ids', label: 'Except' },
];

const ALL_PRIORITIES: ModelPriority[] = ['always_on', 'normal', 'evictable'];
const ALL_MODEL_TYPES = Object.keys(MODEL_TYPE_LABELS) as ModelType[];
const ALL_ENGINES = Object.keys(ENGINE_LABELS) as EngineName[];

interface TargetSelectorProps {
  value: TargetSelector;
  onChange: (value: TargetSelector) => void;
  deployments: RefDeployment[];
  models: RefModel[];
  nodes: RefNode[];
  clusters: RefCluster[];
  /** time_schedule ignores the selector (the layout defines targets). */
  ignored?: boolean;
}

/**
 * Which deployments a rule watches: "all" or a stack of ANDed conditions
 * (values inside one condition are ORed), with a live list of what matches.
 */
export function TargetSelectorEditor({ value, onChange, deployments, models, nodes, clusters, ignored }: TargetSelectorProps) {
  const [shown, setShown] = useState<Set<ConditionKey>>(() => new Set(Object.keys(compactTarget(value)) as ConditionKey[]));
  const [labelRows, setLabelRows] = useState<KeyValueRow[]>(() => Object.entries(value.labels ?? {}).map(([key, v]) => ({ key, value: v })));
  const mode = shown.size === 0 ? 'all' : 'filter';

  const matchables = useMemo(() => deployments.map((d) => ({ d, m: toMatchable(d, nodes) })), [deployments, nodes]);
  const matched = matchables.filter(({ m }) => matchesTarget(m, value));

  const deploymentOptions: PickOption[] = deployments.map((d) => ({ value: d.id, label: d.name, hint: [d.engineName ? ENGINE_LABELS[d.engineName] : null, d.modelName].filter(Boolean).join(' · '), dot: PRIORITY_DOT[d.priority] }));
  const modelOptions: PickOption[] = (models.length ? models.map((m) => ({ id: m.id, name: m.displayName ?? m.name, type: m.type as string | null })) : [...new Map(deployments.map((d) => [d.modelId, { id: d.modelId, name: d.modelName ?? d.modelId, type: d.modelType as string | null }])).values()]).map((m) => ({ value: m.id, label: m.name, hint: m.type ? MODEL_TYPE_LABELS[m.type] : undefined }));
  const nodeOptions: PickOption[] = nodes.map((n) => ({ value: n.id, label: n.name, hint: clusters.find((c) => c.id === n.clusterId)?.name, dot: n.status === 'online' ? 'bg-success' : 'bg-muted-foreground/50' }));
  const clusterOptions: PickOption[] = clusters.map((c) => ({ value: c.id, label: c.name, hint: c.type.replace('_', ' ') }));
  const typesInUse = new Set(deployments.map((d) => d.modelType).filter(Boolean));
  const enginesInUse = new Set(deployments.map((d) => d.engineName).filter(Boolean));

  function set<K extends ConditionKey>(key: K, v: TargetSelector[K] | undefined) {
    const next = { ...value, [key]: v };
    if (v === undefined) delete next[key];
    onChange(next);
  }

  function addCondition(key: ConditionKey) {
    setShown((s) => new Set(s).add(key));
  }

  function removeCondition(key: ConditionKey) {
    setShown((s) => {
      const next = new Set(s);
      next.delete(key);
      return next;
    });
    if (key === 'labels') setLabelRows([]);
    set(key, undefined);
  }

  function toggleList<T extends string>(key: ConditionKey, list: T[] | undefined, item: T) {
    const cur = new Set(list ?? []);
    if (cur.has(item)) cur.delete(item);
    else cur.add(item);
    set(key, [...cur] as never);
  }

  function toggles<T extends string>(key: ConditionKey, all: T[], inUse: Set<unknown>, labels: Record<string, string>, current: T[] | undefined) {
    return (
      <div className="flex flex-wrap gap-1.5">
        {all
          .filter((v) => inUse.has(v) || current?.includes(v) || inUse.size === 0)
          .concat(all.filter((v) => !(inUse.has(v) || current?.includes(v) || inUse.size === 0)))
          .map((v) => {
            const on = current?.includes(v) ?? false;
            const used = inUse.has(v) || inUse.size === 0;
            return (
              <button
                key={v}
                type="button"
                aria-pressed={on}
                onClick={() => toggleList(key, current, v)}
                className={cn(
                  'inline-flex h-7 items-center gap-1.5 rounded-full border px-3 text-xs font-medium outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring/30',
                  on ? 'border-foreground/30 bg-foreground/[0.06] text-foreground' : 'border-border text-muted-foreground hover:text-foreground',
                  !used && !on && 'opacity-55',
                )}
              >
                {key === 'priorities' && <span className={cn('size-1.5 rounded-full', PRIORITY_DOT[v])} />}
                {labels[v] ?? v}
              </button>
            );
          })}
      </div>
    );
  }

  function control(key: ConditionKey) {
    switch (key) {
      case 'deployment_ids':
      case 'exclude_deployment_ids':
        return <MultiPick options={deploymentOptions} value={(value[key] as string[] | undefined) ?? []} onChange={(v) => set(key, v)} placeholder="Choose deployments" searchPlaceholder="Search deployments…" aria-label="Deployments" />;
      case 'model_ids':
        return <MultiPick options={modelOptions} value={value.model_ids ?? []} onChange={(v) => set('model_ids', v)} placeholder="Choose models" searchPlaceholder="Search models…" aria-label="Models" />;
      case 'node_ids':
        return <MultiPick options={nodeOptions} value={value.node_ids ?? []} onChange={(v) => set('node_ids', v)} placeholder="Choose nodes" searchPlaceholder="Search nodes…" aria-label="Nodes" />;
      case 'cluster_ids':
        return <MultiPick options={clusterOptions} value={value.cluster_ids ?? []} onChange={(v) => set('cluster_ids', v)} placeholder="Choose clusters" searchPlaceholder="Search clusters…" aria-label="Clusters" />;
      case 'priorities':
        return toggles('priorities', ALL_PRIORITIES, new Set(), PRIORITY_LABELS, value.priorities);
      case 'model_types':
        return toggles('model_types', ALL_MODEL_TYPES, typesInUse, MODEL_TYPE_LABELS, value.model_types);
      case 'engines':
        return toggles('engines', ALL_ENGINES, enginesInUse, ENGINE_LABELS, value.engines);
      case 'labels':
        return (
          <KeyValueEditor
            rows={labelRows}
            mono
            keyPlaceholder="role"
            valuePlaceholder="serving"
            addLabel="Add label"
            onChange={(rows) => {
              setLabelRows(rows);
              const map = Object.fromEntries(rows.filter((r) => r.key.trim()).map((r) => [r.key.trim(), r.value]));
              set('labels', map);
            }}
          />
        );
      case 'name_glob':
        return <Input value={value.name_glob ?? ''} onChange={(e) => set('name_glob', e.target.value)} placeholder="chat-*" className="h-9 w-full font-mono text-xs sm:w-64" aria-label="Name pattern" />;
      default:
        return null;
    }
  }

  const available = CONDITIONS.filter((c) => !shown.has(c.key));
  const conditionList = CONDITIONS.filter((c) => shown.has(c.key));

  return (
    <div className={cn('space-y-4', ignored && 'pointer-events-none opacity-50')} aria-disabled={ignored}>
      <Segmented
        value={mode}
        onChange={(m) => {
          if (m === 'all') {
            setShown(new Set());
            setLabelRows([]);
            onChange({});
          } else {
            addCondition('priorities');
          }
        }}
        options={[
          { value: 'all', label: 'Every deployment' },
          { value: 'filter', label: 'Only some', icon: Filter },
        ]}
        aria-label="Target"
      />

      {mode === 'filter' && (
        <div className="space-y-0">
          {conditionList.map((c, i) => (
            <div key={c.key}>
              {i > 0 && (
                <div className="flex items-center gap-2 py-1.5 pl-3" aria-hidden>
                  <span className="h-3 w-px bg-border" />
                  <span className="text-[10px] font-semibold uppercase tracking-[0.08em] text-muted-foreground/80">and</span>
                </div>
              )}
              <div className="group/cond flex flex-col gap-2 rounded-lg border border-border bg-background/40 px-3 py-2.5 sm:flex-row sm:items-start">
                <span className="w-36 shrink-0 pt-1.5 text-xs font-medium text-muted-foreground">{c.label}</span>
                <div className="min-w-0 flex-1">{control(c.key)}</div>
                <Button type="button" variant="ghost" size="icon-xs" className="self-end text-muted-foreground opacity-60 group-hover/cond:opacity-100 sm:self-start" onClick={() => removeCondition(c.key)} aria-label={`Remove “${c.label}”`}>
                  <Trash2 />
                </Button>
              </div>
            </div>
          ))}
          {available.length > 0 && (
            <DropdownMenu>
              <DropdownMenuTrigger render={<Button type="button" variant="outline" size="xs" className="mt-3" />}>
                <Plus className="size-3" /> Add condition
              </DropdownMenuTrigger>
              <DropdownMenuContent align="start" className="w-52">
                {available.map((c) => (
                  <DropdownMenuItem key={c.key} onClick={() => addCondition(c.key)}>
                    {c.label}
                  </DropdownMenuItem>
                ))}
              </DropdownMenuContent>
            </DropdownMenu>
          )}
        </div>
      )}

      <div className="rounded-lg border border-dashed border-border px-3 py-2.5">
        <p className="mb-2 flex items-center gap-1.5 text-xs text-muted-foreground">
          <Info className="size-3.5" />
          {ignored ? (
            'Time schedules act on the deployments listed in the layout — this selector is not used.'
          ) : (
            <>
              Matches <span className="font-semibold tabular-nums text-foreground">{matched.length}</span> of {deployments.length} deployment{deployments.length === 1 ? '' : 's'} right now
            </>
          )}
        </p>
        {!ignored && (
          <ul className="flex flex-wrap gap-1.5">
            {matchables.map(({ d }) => {
              const on = matched.some((x) => x.d.id === d.id);
              return (
                <li
                  key={d.id}
                  className={cn('inline-flex h-6 items-center gap-1.5 rounded-[5px] px-2 text-[11px] font-medium', on ? 'bg-foreground/[0.06] text-foreground ring-1 ring-inset ring-foreground/10' : 'text-muted-foreground/70 line-through decoration-muted-foreground/40')}
                  title={`${d.name} · ${PRIORITY_LABELS[d.priority] ?? d.priority}${d.engineName ? ` · ${ENGINE_LABELS[d.engineName]}` : ''}`}
                >
                  {on ? <Check className="size-3 text-success" /> : null}
                  {d.name}
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </div>
  );
}
