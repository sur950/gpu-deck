'use client';

import { useState } from 'react';
import Link from 'next/link';
import { useParams, useRouter } from 'next/navigation';
import { ArrowUpRight, CircleAlert, Loader2, RotateCcw, ShieldCheck, X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { PageContainer } from '@/components/shared/PageContainer';
import { PageHeader } from '@/components/shared/PageHeader';
import { ErrorState } from '@/components/shared/ErrorState';
import { StatusBadge } from '@/components/shared/StatusBadge';
import { DetailList, SectionLabel } from '@/components/shared/detail-list';
import { JsonView } from '@/components/shared/json-view';
import { CopyButton } from '@/components/shared/copy-button';
import { ConfirmationDialog } from '@/components/shared/ConfirmationDialog';
import { DashboardSkeleton } from '@/components/charts/dashboard-skeleton';
import { useCancelSwap, useRefDeployments, useRefNodes, useRetrySwap, useRuleRun, useSwap } from '@/hooks/api/automation';
import { cn } from '@/lib/utils';
import { drainDetail, formatMs, isActive, KIND_META, STATE_META, STEP_LABELS, stepDurationMs, stepLabel, swapDurationMs, TRIGGER_LABELS } from '@/components/automation/swap-meta';
import { SwapPipeline, SwapTimingBar } from '@/components/automation/swap-pipeline';
import { humanize } from '@/components/automation/rule-meta';
import { useNow } from '@/components/automation/use-now';

export function SwapDetail() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const now = useNow(1000);
  const [pollActive, setPollActive] = useState(true);
  const { data: swap, isLoading, isError, refetch } = useSwap(id, { refetchInterval: pollActive ? 2_000 : false });
  const ruleRun = useRuleRun(swap?.ruleRunId ?? undefined);
  const deployments = useRefDeployments();
  const nodes = useRefNodes();
  const cancel = useCancelSwap();
  const retry = useRetrySwap();
  const [cancelOpen, setCancelOpen] = useState(false);

  if (swap && !isActive(swap.state) && pollActive) setPollActive(false);
  if (isLoading) return <DashboardSkeleton />;
  if (isError || !swap) {
    return (
      <PageContainer>
        <ErrorState title="Swap not found" description="It may have been pruned, or the control plane did not answer." onRetry={() => void refetch()} />
      </PageContainer>
    );
  }

  const meta = KIND_META[swap.kind];
  const Icon = meta.icon;
  const active = isActive(swap.state);
  const failedStep = swap.steps.find((s) => s.state === 'failed');
  const node = nodes.data?.find((n) => n.id === swap.nodeId);
  const deploymentExists = deployments.data?.some((d) => d.id === swap.deploymentId);
  const rollback = swap.rollback ?? {};
  const hasRollback = Object.keys(rollback).length > 0;

  return (
    <PageContainer>
      <PageHeader
        title={`${meta.label} ${swap.deploymentName ?? ''}`.trim()}
        description={String(swap.plan.reason ?? `${TRIGGER_LABELS[swap.trigger]} swap`)}
        actions={
          <>
            <StatusBadge variant={STATE_META[swap.state].badge}>
              {active && <Loader2 className="mr-1 size-3 animate-spin" />}
              {STATE_META[swap.state].label}
            </StatusBadge>
            {active && (
              <Button variant="outline" size="sm" className="rounded-sm" onClick={() => setCancelOpen(true)}>
                <X className="size-4" /> Cancel
              </Button>
            )}
            {['failed', 'rolled_back', 'cancelled'].includes(swap.state) && (
              <Button size="sm" className="rounded-sm" disabled={retry.isPending} onClick={() => retry.mutate(swap.id, { onSuccess: (next) => router.push(`/swaps/${next.id}`) })}>
                <RotateCcw className="size-4" /> Retry as a new swap
              </Button>
            )}
          </>
        }
      />

      <section className={cn('relative overflow-hidden rounded-2xl border bg-card px-5 py-6 shadow-premium sm:px-8', active ? 'border-info/30' : swap.state === 'completed' ? 'border-border' : 'border-error/30')}>
        <div aria-hidden className="bg-dot-grid pointer-events-none absolute inset-0 [mask-image:linear-gradient(to_left,black,transparent_60%)]" />
        <div className="relative mb-6 flex flex-wrap items-center gap-x-6 gap-y-2 text-xs">
          <span className="flex items-center gap-2">
            <span className="flex size-8 items-center justify-center rounded-lg border border-border bg-muted/60">
              <Icon className="size-4" />
            </span>
            <span>
              <span className="block text-[11px] text-muted-foreground">Operation</span>
              <span className="font-semibold">{meta.label}</span>
            </span>
          </span>
          <span>
            <span className="block text-[11px] text-muted-foreground">{active ? 'Running for' : 'Took'}</span>
            <span className="font-mono font-semibold tabular-nums">{formatMs(swapDurationMs(swap, now))}</span>
          </span>
          <span>
            <span className="block text-[11px] text-muted-foreground">Order</span>
            <span className="font-semibold">{swap.plan.order === 'load_first' ? 'Load new first, then drain old' : 'Drain first, then load'}</span>
          </span>
          <span>
            <span className="block text-[11px] text-muted-foreground">Drain timeout</span>
            <span className="font-semibold tabular-nums">{swap.drainTimeoutSeconds ?? '—'} s</span>
          </span>
          {swap.dryRun && <StatusBadge variant="dry_run">dry run — planned only</StatusBadge>}
        </div>
        <SwapPipeline steps={swap.steps} now={now} size="lg" className="relative" />
        {swap.errorMessage && (
          <p className="relative mt-6 flex items-start gap-2 rounded-md bg-destructive/10 p-3 text-xs text-destructive">
            <CircleAlert className="mt-px size-3.5 shrink-0" />
            <span>
              {failedStep ? <b className="font-semibold">{stepLabel(failedStep.name)} failed: </b> : null}
              {swap.errorMessage}
            </span>
          </p>
        )}
        {swap.state === 'rolled_back' && (
          <p className="relative mt-3 flex items-start gap-2 rounded-md border border-success/30 bg-success/[0.06] p-3 text-xs text-success-dark dark:text-success-light">
            <ShieldCheck className="mt-px size-3.5 shrink-0" />
            Rolled back: routing was restored to the instance that was serving before, so clients saw no outage.
          </p>
        )}
      </section>

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_380px]">
        <Card className="gap-0 rounded-xl py-0">
          <CardHeader className="border-b border-border px-5 py-4">
            <CardTitle className="text-sm font-semibold">Steps</CardTitle>
            <CardDescription>Each step is persisted, so a swap resumes after a control-plane restart.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-5 px-5 py-5">
            <SwapTimingBar steps={swap.steps} now={now} />
            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Step</TableHead>
                    <TableHead>State</TableHead>
                    <TableHead>Started</TableHead>
                    <TableHead className="text-right">Took</TableHead>
                    <TableHead>Agent command</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {swap.steps.map((step, i) => {
                    const detail = drainDetail(step);
                    return (
                      <TableRow key={`${step.name}-${i}`} className={cn(step.state === 'running' && 'bg-info/[0.05]', step.state === 'failed' && 'bg-error/[0.05]')}>
                        <TableCell>
                          <p className="text-xs font-semibold">{stepLabel(step.name)}</p>
                          <p className="max-w-[280px] text-[11px] text-muted-foreground">{step.error ?? detail ?? STEP_LABELS[step.name]?.hint ?? ''}</p>
                        </TableCell>
                        <TableCell>
                          <StatusBadge variant={step.state === 'completed' ? 'completed' : step.state === 'running' ? 'loading' : step.state === 'failed' ? 'failed' : 'pending'}>{humanize(step.state)}</StatusBadge>
                        </TableCell>
                        <TableCell className="font-mono text-[11px] tabular-nums text-muted-foreground">{step.startedAt ? new Date(step.startedAt).toLocaleTimeString(undefined, { hourCycle: 'h23' }) : '—'}</TableCell>
                        <TableCell className="text-right font-mono text-xs tabular-nums">{formatMs(stepDurationMs(step, now))}</TableCell>
                        <TableCell>
                          {step.commandId ? (
                            <span className="inline-flex items-center gap-1 font-mono text-[11px] text-muted-foreground">
                              {step.commandId.slice(0, 8)}…
                              <CopyButton value={step.commandId} successText="Command id copied" label={undefined} />
                            </span>
                          ) : (
                            <span className="text-[11px] text-muted-foreground">control plane</span>
                          )}
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </div>
          </CardContent>
        </Card>

        <div className="space-y-4">
          <Card className="rounded-xl">
            <CardContent className="space-y-4">
              <SectionLabel>Context</SectionLabel>
              <DetailList
                items={[
                  {
                    label: 'Deployment',
                    value: swap.deploymentId ? (
                      deploymentExists ? (
                        <Link href={`/deployments/${swap.deploymentId}`} className="hover:underline">
                          {swap.deploymentName ?? swap.deploymentId}
                        </Link>
                      ) : (
                        (swap.deploymentName ?? swap.deploymentId)
                      )
                    ) : null,
                  },
                  { label: 'Node', value: node ? <Link href={`/nodes/${node.id}`} className="hover:underline">{node.name}</Link> : (swap.nodeId?.slice(0, 8) ?? null) },
                  { label: 'Trigger', value: TRIGGER_LABELS[swap.trigger] },
                  {
                    label: 'Rule',
                    value: swap.ruleName ? (
                      ruleRun.data ? (
                        <Link href={`/rules/${ruleRun.data.ruleId}?run=${ruleRun.data.id}`} className="inline-flex items-center gap-0.5 hover:underline">
                          {swap.ruleName} <ArrowUpRight className="size-3" />
                        </Link>
                      ) : (
                        swap.ruleName
                      )
                    ) : null,
                  },
                  { label: 'Layout', value: swap.layoutId ? <Link href={`/layouts/${swap.layoutId}`} className="hover:underline">open layout</Link> : null },
                  { label: 'From instance', value: swap.fromInstanceId?.slice(0, 8) ?? null, mono: true },
                  { label: 'To instance', value: swap.toInstanceId?.slice(0, 8) ?? null, mono: true },
                  { label: 'Created', value: new Date(swap.createdAt).toLocaleString() },
                  { label: 'Finished', value: swap.finishedAt ? new Date(swap.finishedAt).toLocaleString() : active ? 'running…' : null },
                ]}
              />
            </CardContent>
          </Card>
          <Card className="rounded-xl">
            <CardContent className="space-y-3">
              <SectionLabel>Plan</SectionLabel>
              <JsonView value={swap.plan} maxHeight={200} />
              {hasRollback && (
                <>
                  <SectionLabel>Rollback</SectionLabel>
                  <JsonView value={rollback} maxHeight={160} />
                </>
              )}
            </CardContent>
          </Card>
        </div>
      </div>

      <ConfirmationDialog
        open={cancelOpen}
        onOpenChange={setCancelOpen}
        title="Cancel this swap?"
        description="The swap manager stops before the next step and restores routing to the instance that was serving."
        confirmText="Cancel swap"
        cancelText="Keep running"
        variant="destructive"
        onConfirm={() => cancel.mutate(swap.id)}
        isLoading={cancel.isPending}
      />
    </PageContainer>
  );
}
