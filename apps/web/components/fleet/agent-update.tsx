'use client';

/**
 * Agent self-update UI (agent protocol §14): the version badge of a node, the Update button with
 * its confirmation, the live steps of an update (staged → swapping → restarting → re-adopting
 * engines → health check → done or rolled back), the node card and the cluster card (rolling
 * update, policy: manual or automatic inside a maintenance window).
 */

import { useState } from 'react';
import Link from 'next/link';
import { AlertTriangle, ArrowUpCircle, Check, CircleDashed, Clock, Download, Loader2, RotateCcw, Settings2, Square, X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Checkbox } from '@/components/ui/checkbox';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { ConfirmationDialog } from '@/components/shared/ConfirmationDialog';
import { DetailList } from '@/components/shared/detail-list';
import { FormDialog } from '@/components/shared/form-dialog';
import { StatusBadge } from '@/components/shared/StatusBadge';
import {
  useAgentUpdateRuns,
  useCancelRollout,
  useClusterAgentUpdate,
  useNodeAgentUpdate,
  useSetAgentUpdatePolicy,
  useStageNodeAgentUpdate,
  useStartClusterRollout,
  useUpdateNodeAgent,
} from '@/hooks/api/agent-updates';
import { cn } from '@/lib/utils';
import { formatDateTime, formatRelativeTime } from '@/utils/format';
import {
  RUN_OUTCOME,
  UPDATE_MODE_LABELS,
  UPDATE_STATUS,
  UPDATE_STEPS,
  WEEKDAYS,
  WEEKDAY_LABELS,
  describeWindow,
  isRunActive,
  runStepIndex,
  stepTime,
  type AgentUpdatePolicy,
  type AgentUpdateRun,
  type NodeAgentUpdate,
  type Weekday,
} from '@/types/agent-updates';

// ─── Badge ────────────────────────────────────────────────────────────────────

export function AgentUpdateBadge({ info, className }: { info: NodeAgentUpdate | null | undefined; className?: string }) {
  if (!info || info.status === 'up_to_date' || info.status === 'disabled') return null;
  const s = UPDATE_STATUS[info.status];
  const version = info.status === 'update_available' || info.status === 'staged' || info.status === 'staging' ? info.target_version : info.active_run?.to_version;
  return (
    <StatusBadge variant={s.tone} className={cn('gap-1 px-1.5 py-0.5 text-[11px] whitespace-nowrap', className)}>
      {info.status === 'updating' ? <Loader2 className="size-3 animate-spin" /> : info.status === 'managed' ? null : <ArrowUpCircle className="size-3" />}
      <span title={info.reason ?? undefined}>
        {s.label}
        {version ? ` ${version}` : ''}
      </span>
    </StatusBadge>
  );
}

// ─── Steps ────────────────────────────────────────────────────────────────────

export function UpdateSteps({ run, className }: { run: AgentUpdateRun; className?: string }) {
  const current = runStepIndex(run);
  const outcome = RUN_OUTCOME[run.state];
  const stopped = run.state === 'rolled_back' || run.state === 'failed' || run.state === 'cancelled';
  const steps = UPDATE_STEPS.map((s) =>
    s.id === 'done' && stopped ? { ...s, title: outcome?.label ?? 'Stopped', description: run.error_message ?? '' } : s,
  );
  return (
    <ol className={cn('grid gap-2 sm:grid-cols-3 xl:grid-cols-6', className)} aria-label="Update progress">
      {steps.map((step, i) => {
        const done = run.state === 'succeeded' ? true : i < current;
        const active = !stopped && i === current && run.state !== 'succeeded';
        const last = i === steps.length - 1;
        const failedHere = stopped && last;
        const at = stepTime(run, step.id === 'done' && stopped ? run.state : step.id);
        return (
          <li
            key={step.id}
            className={cn(
              'flex min-w-0 items-start gap-2 rounded-md border px-2.5 py-2',
              done && 'border-border bg-muted/30',
              active && 'border-brand/40 bg-brand/5',
              failedHere && (run.state === 'rolled_back' ? 'border-warning/40 bg-warning/10' : 'border-error/40 bg-error/10'),
              !done && !active && !failedHere && 'border-dashed border-border',
            )}
            aria-current={active ? 'step' : undefined}
          >
            <span className="mt-0.5 shrink-0">
              {failedHere ? (
                run.state === 'rolled_back' ? <RotateCcw className="size-3.5 text-warning" /> : <X className="size-3.5 text-error" />
              ) : done ? (
                <Check className="size-3.5 text-success" />
              ) : active ? (
                <Loader2 className="size-3.5 animate-spin text-brand" />
              ) : (
                <CircleDashed className="size-3.5 text-muted-foreground" />
              )}
            </span>
            <span className="min-w-0">
              <span className={cn('block text-[12px] font-medium', !done && !active && !failedHere && 'text-muted-foreground')}>{step.title}</span>
              <span className="block truncate text-[11px] text-muted-foreground" title={step.description}>
                {at ? formatDateTime(at) : step.description}
              </span>
            </span>
          </li>
        );
      })}
    </ol>
  );
}

// ─── Update dialog ────────────────────────────────────────────────────────────

export function UpdateAgentDialog({
  nodeId,
  nodeName,
  info,
  open,
  onOpenChange,
}: {
  nodeId: string;
  nodeName: string;
  info: NodeAgentUpdate | null | undefined;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const update = useUpdateNodeAgent();
  const staged = info?.status === 'staged';
  return (
    <ConfirmationDialog
      open={open}
      onOpenChange={onOpenChange}
      title={`Update ${nodeName} to ${info?.target_version ?? 'the latest agent'}?`}
      description={`${staged ? 'The new agent is already on the node and verified.' : 'The node first downloads and verifies the new agent from the control plane.'} The agent swaps its binary (keeping ${info?.current_version ?? 'the current one'} for rollback) and restarts; running models keep serving and are re-adopted. If the new agent does not reconnect and report healthy, the node rolls back by itself.`}
      confirmText="Update agent"
      isLoading={update.isPending}
      onConfirm={() => update.mutate({ nodeId }, { onSettled: () => onOpenChange(false) })}
    />
  );
}

// ─── Node card ────────────────────────────────────────────────────────────────

function ManualInstructions({ info }: { info: NodeAgentUpdate }) {
  if (!info.instructions) return null;
  return (
    <div className="rounded-md border border-warning/30 bg-warning/10 px-3 py-2.5 text-[13px]">
      <div className="mb-1 flex items-center gap-1.5 font-medium">
        <AlertTriangle className="size-3.5 text-warning" />
        {info.status === 'managed' ? 'Managed by Helm' : 'Update manually'}
      </div>
      <p className="text-muted-foreground">{info.instructions}</p>
      {info.status !== 'managed' && (
        <p className="mt-1.5 text-muted-foreground">
          The install command is on{' '}
          <Link href="/nodes/join" className="underline underline-offset-2 hover:text-foreground">
            Nodes, Add node
          </Link>
          .
        </p>
      )}
    </div>
  );
}

export function NodeAgentUpdateCard({ nodeId, nodeName, className }: { nodeId: string; nodeName: string; className?: string }) {
  const { data: info, isLoading } = useNodeAgentUpdate(nodeId);
  const runs = useAgentUpdateRuns({ node_id: nodeId, page_size: 5 }, !!info);
  const stage = useStageNodeAgentUpdate();
  const [confirm, setConfirm] = useState(false);
  if (isLoading || !info) return null;
  const run = info.active_run ?? info.last_run ?? null;
  const history = (runs.data?.items ?? []).filter((r) => r.id !== run?.id);
  const canStage = info.can_update && info.status === 'update_available';
  return (
    <Card className={cn('rounded-md shadow-premium', className)}>
      <CardHeader className="flex flex-row flex-wrap items-start justify-between gap-3">
        <div className="space-y-1">
          <CardTitle className="flex items-center gap-2">
            Agent updates
            {info.status !== 'disabled' && <StatusBadge variant={UPDATE_STATUS[info.status].tone}>{UPDATE_STATUS[info.status].label}</StatusBadge>}
          </CardTitle>
          <CardDescription>
            {info.status === 'disabled'
              ? (info.reason ?? 'Agent updates are off on this control plane.')
              : 'Signed releases are downloaded to the node ahead of time; the update itself downloads nothing and keeps models serving.'}
          </CardDescription>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {canStage && (
            <Button variant="outline" size="sm" className="rounded-sm" disabled={stage.isPending} onClick={() => stage.mutate({ nodeId })}>
              <Download className="size-4" />
              Download now
            </Button>
          )}
          {info.can_update && (
            <Button size="sm" className="rounded-sm" onClick={() => setConfirm(true)}>
              <ArrowUpCircle className="size-4" />
              Update to {info.target_version}
            </Button>
          )}
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <DetailList
          items={[
            { label: 'Running', value: info.current_version, mono: true },
            { label: 'Target', value: info.target_version ?? '—', mono: true },
            { label: 'Staged', value: info.staged_version ? `${info.staged_version}${info.staged_at ? ` · ${formatRelativeTime(info.staged_at)}` : ''}` : info.state === 'staging' ? 'downloading…' : '—', mono: !!info.staged_version },
            { label: 'Kept for rollback', value: info.previous_version ?? '—', mono: !!info.previous_version },
            { label: 'How it updates', value: UPDATE_MODE_LABELS[info.mode] + (info.reason && info.status !== 'disabled' ? ` · ${info.reason}` : '') },
          ]}
        />
        <ManualInstructions info={info} />
        {info.status === 'updating' && (
          <div className="rounded-md border border-info/30 bg-info/10 px-3 py-2 text-[13px] text-muted-foreground">
            <span className="font-medium text-foreground">Updating.</span> Metrics pause while the agent restarts and resume when it reconnects; offline alerts for this node are held back until the update ends. Engines keep serving.
          </div>
        )}
        {run && (
          <div className="space-y-2">
            <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
              <span className="font-medium">
                {isRunActive(run.state) ? 'Update in progress' : 'Last update'} · <span className="font-mono">{run.from_version ?? '?'}</span> → <span className="font-mono">{run.to_version}</span>
              </span>
              <span className="text-muted-foreground">
                {run.trigger === 'rollout' ? 'rolling update' : run.trigger} · started {formatRelativeTime(run.started_at)}
                {RUN_OUTCOME[run.state] && <> · {RUN_OUTCOME[run.state]?.label.toLowerCase()}</>}
              </span>
            </div>
            <UpdateSteps run={run} />
            {run.error_message && !isRunActive(run.state) && <p className="text-xs text-muted-foreground">{run.error_message}</p>}
          </div>
        )}
        {history.length > 0 && (
          <div className="space-y-1 border-t border-border pt-3">
            <div className="text-[11px] font-medium tracking-wide text-muted-foreground uppercase">Earlier updates</div>
            {history.map((r) => (
              <div key={r.id} className="flex flex-wrap items-center gap-x-3 text-xs">
                <span className="font-mono">
                  {r.from_version ?? '?'} → {r.to_version}
                </span>
                <StatusBadge variant={RUN_OUTCOME[r.state]?.tone ?? 'default'} className="px-1.5 py-0 text-[10.5px]">
                  {RUN_OUTCOME[r.state]?.label ?? r.state.replace(/_/g, ' ')}
                </StatusBadge>
                <span className="text-muted-foreground">{formatDateTime(r.started_at)}</span>
              </div>
            ))}
          </div>
        )}
      </CardContent>
      <UpdateAgentDialog nodeId={nodeId} nodeName={nodeName} info={info} open={confirm} onOpenChange={setConfirm} />
    </Card>
  );
}

// ─── Cluster card ─────────────────────────────────────────────────────────────

const DEFAULT_WINDOW = { days: ['sat', 'sun'] as Weekday[], start: '02:00', end: '05:00', timezone: 'UTC' };

function PolicyDialog({ clusterId, policy, open, onOpenChange }: { clusterId: string; policy: AgentUpdatePolicy; open: boolean; onOpenChange: (o: boolean) => void }) {
  const save = useSetAgentUpdatePolicy();
  const [mode, setMode] = useState<'manual' | 'auto'>(policy.mode ?? 'manual');
  const [days, setDays] = useState<Weekday[]>((policy.window?.days as Weekday[]) ?? DEFAULT_WINDOW.days);
  const [start, setStart] = useState(policy.window?.start ?? DEFAULT_WINDOW.start);
  const [end, setEnd] = useState(policy.window?.end ?? DEFAULT_WINDOW.end);
  const [timezone, setTimezone] = useState(policy.window?.timezone ?? DEFAULT_WINDOW.timezone);
  const valid = mode === 'manual' || (days.length > 0 && /^\d{2}:\d{2}$/.test(start) && /^\d{2}:\d{2}$/.test(end) && start !== end && timezone.trim() !== '');
  return (
    <FormDialog
      open={open}
      onOpenChange={onOpenChange}
      title="Agent update policy"
      description="Manual: nodes update when you click Update or start a rolling update. Automatic: inside the maintenance window the control plane updates nodes that have the new agent staged, one at a time, and stops at the first node that rolls back."
      icon={<Settings2 className="size-4" />}
      submitLabel="Save policy"
      isPending={save.isPending}
      submitDisabled={!valid}
      onSubmit={() =>
        save.mutate(
          { clusterId, policy: mode === 'manual' ? { mode: 'manual', window: policy.window ?? null } : { mode: 'auto', window: { days, start, end, timezone: timezone.trim() } } },
          { onSuccess: () => onOpenChange(false) },
        )
      }
    >
      <div className="space-y-1.5">
        <Label>Mode</Label>
        <Select value={mode} onValueChange={(v) => setMode(v === 'auto' ? 'auto' : 'manual')}>
          <SelectTrigger className="w-full">
            <SelectValue>{(v: unknown) => (v === 'auto' ? 'Automatic in a maintenance window' : 'Manual')}</SelectValue>
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="manual">Manual</SelectItem>
            <SelectItem value="auto">Automatic in a maintenance window</SelectItem>
          </SelectContent>
        </Select>
      </div>
      {mode === 'auto' && (
        <>
          <div className="space-y-1.5">
            <Label>Days the window opens</Label>
            <div className="flex flex-wrap gap-3">
              {WEEKDAYS.map((d) => (
                <label key={d} className="flex cursor-pointer items-center gap-1.5 text-[13px]">
                  <Checkbox checked={days.includes(d)} onCheckedChange={(c) => setDays((cur) => (c ? [...cur, d] : cur.filter((x) => x !== d)))} />
                  {WEEKDAY_LABELS[d]}
                </label>
              ))}
            </div>
          </div>
          <div className="grid grid-cols-3 gap-3">
            <div className="space-y-1.5">
              <Label htmlFor="win-start">From</Label>
              <Input id="win-start" type="time" value={start} onChange={(e) => setStart(e.target.value)} />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="win-end">Until</Label>
              <Input id="win-end" type="time" value={end} onChange={(e) => setEnd(e.target.value)} />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="win-tz">Time zone</Label>
              <Input id="win-tz" value={timezone} onChange={(e) => setTimezone(e.target.value)} placeholder="UTC" />
            </div>
          </div>
          <p className="text-xs text-muted-foreground">An end before the start runs past midnight. {valid ? describeWindow({ days, start, end, timezone }) : ''}</p>
        </>
      )}
    </FormDialog>
  );
}

export function ClusterAgentUpdateCard({ clusterId, nodeNames, className }: { clusterId: string; nodeNames?: Record<string, string>; className?: string }) {
  const { data, isLoading } = useClusterAgentUpdate(clusterId);
  const start = useStartClusterRollout();
  const cancel = useCancelRollout();
  const [confirm, setConfirm] = useState(false);
  const [policyOpen, setPolicyOpen] = useState(false);
  if (isLoading || !data) return null;
  const c = data.counts;
  const rollout = data.rollout;
  const running = rollout?.state === 'running';
  const policy = data.policy;
  const name = (id?: string | null) => (id ? (nodeNames?.[id] ?? id.slice(0, 8)) : '—');
  const pending = c.update_available + c.staged;
  return (
    <Card className={cn('rounded-md shadow-premium', className)}>
      <CardHeader className="flex flex-row flex-wrap items-start justify-between gap-3">
        <div className="space-y-1">
          <CardTitle>Agent updates</CardTitle>
          <CardDescription>
            {data.target_version ? (
              <>
                Target <span className="font-mono">{data.target_version}</span> · {policy.mode === 'auto' ? `automatic, ${describeWindow(policy.window)}${data.in_window ? ' (open now)' : data.next_window_at ? ` · next ${formatRelativeTime(data.next_window_at)}` : ''}` : 'manual'}
              </>
            ) : (
              'Agent updates are off on this control plane (no release signing key or signed release).'
            )}
          </CardDescription>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="outline" size="sm" className="rounded-sm" onClick={() => setPolicyOpen(true)}>
            <Clock className="size-4" />
            Policy
          </Button>
          {running ? (
            <Button variant="outline" size="sm" className="rounded-sm" disabled={cancel.isPending} onClick={() => rollout && cancel.mutate({ rolloutId: rollout.id })}>
              <Square className="size-4" />
              Stop rolling update
            </Button>
          ) : (
            <Button size="sm" className="rounded-sm" disabled={!data.target_version || pending === 0} onClick={() => setConfirm(true)}>
              <ArrowUpCircle className="size-4" />
              Rolling update
            </Button>
          )}
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid grid-cols-2 gap-2 text-xs sm:grid-cols-4 lg:grid-cols-8">
          {(
            [
              ['Up to date', c.up_to_date, 'success'],
              ['Update available', c.update_available, 'info'],
              ['Staged', c.staged, 'info'],
              ['Updating', c.updating, 'warning'],
              ['Update manually', c.manual + c.unsupported, 'warning'],
              ['Managed by Helm', c.managed, 'default'],
              ['Offline', c.offline, 'error'],
              ['Nodes', c.total, 'default'],
            ] as const
          ).map(([label, value, tone]) => (
            <div key={label} className="rounded-md border border-border px-2.5 py-2">
              <div className="text-[11px] text-muted-foreground">{label}</div>
              <div className={cn('text-base font-semibold tabular-nums', value > 0 && tone === 'warning' && 'text-warning', value > 0 && tone === 'error' && 'text-error')}>{value ?? 0}</div>
            </div>
          ))}
        </div>
        {rollout && (
          <div className="space-y-1.5 rounded-md border border-border px-3 py-2.5 text-[13px]">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-medium">{running ? 'Rolling update in progress' : 'Last rolling update'}</span>
              <StatusBadge variant={rollout.state === 'succeeded' ? 'success' : rollout.state === 'failed' ? 'warning' : rollout.state === 'running' ? 'info' : 'default'} className="px-1.5 py-0 text-[11px]">
                {rollout.state}
              </StatusBadge>
              <span className="text-xs text-muted-foreground">
                to <span className="font-mono">{rollout.target_version}</span> · {rollout.trigger} · {formatRelativeTime(rollout.started_at)}
              </span>
            </div>
            <div className="text-xs text-muted-foreground">
              {rollout.completed_count} of {rollout.node_ids.length} updated
              {running && rollout.current_node_id && (
                <>
                  {' '}· now{' '}
                  <Link href={`/nodes/${rollout.current_node_id}`} className="underline underline-offset-2 hover:text-foreground">
                    {name(rollout.current_node_id)}
                  </Link>
                </>
              )}
              {rollout.failed_node_id && <> · stopped at {name(rollout.failed_node_id)}</>}
            </div>
            {rollout.message && <div className="text-xs text-muted-foreground">{rollout.message}</div>}
            {rollout.skipped.length > 0 && (
              <ul className="space-y-0.5 text-xs text-muted-foreground">
                {rollout.skipped.map((s) => (
                  <li key={s.node_id}>
                    Skipped {s.node_name ?? name(s.node_id)}: {s.reason}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </CardContent>
      <ConfirmationDialog
        open={confirm}
        onOpenChange={setConfirm}
        title={`Rolling update to ${data.target_version ?? ''}?`}
        description={`Nodes are updated one at a time; each keeps its models serving, restarts on the new agent and must reconnect healthy before the next one starts. The rolling update stops at the first node that rolls back. ${c.manual + c.unsupported > 0 ? `${c.manual + c.unsupported} node(s) need a manual update and are skipped. ` : ''}${c.managed > 0 ? `${c.managed} Kubernetes node(s) are managed by Helm and skipped.` : ''}`}
        confirmText="Start rolling update"
        isLoading={start.isPending}
        onConfirm={() => start.mutate({ clusterId }, { onSettled: () => setConfirm(false) })}
      />
      {policyOpen && <PolicyDialog clusterId={clusterId} policy={policy} open onOpenChange={setPolicyOpen} />}
    </Card>
  );
}
