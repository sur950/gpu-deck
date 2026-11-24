'use client';

import { useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { ArrowUpRight, CircleAlert, Clock, Download, Loader2, Play } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Switch } from '@/components/ui/switch';
import { Skeleton } from '@/components/ui/skeleton';
import { useApplyLayout, usePreviewLayout } from '@/hooks/api/automation';
import type { LayoutApplyResult } from '@/hooks/api/automation-api';
import { ServerDiffList } from '@/components/automation/layout-diff-list';
import { gib } from '@/components/automation/layout-model';
import { formatSeconds } from '@/components/automation/rule-meta';

interface ApplyLayoutDialogProps {
  layout: { id: string; name: string } | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/**
 * Confirm before applying: the server's diff against what runs now (with
 * protections, prefetch sizes and feasibility), drain timeout, and an
 * option to only prefetch weights.
 */
export function ApplyLayoutDialog({ layout, open, onOpenChange }: ApplyLayoutDialogProps) {
  const preview = usePreviewLayout();
  const apply = useApplyLayout();
  const [drain, setDrain] = useState(300);
  const [prefetchOnly, setPrefetchOnly] = useState(false);
  const [result, setResult] = useState<LayoutApplyResult | null>(null);
  const started = useRef<string | null>(null);
  const { mutate: runPreview, reset: resetPreview } = preview;

  useEffect(() => {
    if (open && layout && started.current !== layout.id) {
      started.current = layout.id;
      runPreview(layout.id);
    }
    if (!open) {
      started.current = null;
      resetPreview();
    }
  }, [open, layout, runPreview, resetPreview]);

  const diff = preview.data;
  const changes = diff?.entries.filter((e) => e.action !== 'noop') ?? [];
  const prefetchBytes = diff?.entries.reduce((s, e) => s + e.needsPrefetchBytes, 0) ?? 0;

  function close(o: boolean) {
    if (!o) {
      setResult(null);
      setPrefetchOnly(false);
    }
    onOpenChange(o);
  }

  return (
    <Dialog open={open} onOpenChange={close}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-xl">
        <DialogHeader>
          <DialogTitle className="text-base">Apply “{layout?.name}”</DialogTitle>
          <DialogDescription>Each change runs as a swap: drain → unload → load → health check, with rollback if a step fails. Unrelated deployments keep serving.</DialogDescription>
        </DialogHeader>

        {result ? (
          <div className="space-y-3 rounded-lg border border-success/30 bg-success/[0.06] p-4 text-sm">
            <p className="font-semibold">{prefetchOnly ? `Prefetch started — ${result.commandIds.length} command${result.commandIds.length === 1 ? '' : 's'}` : result.swapOperationIds.length ? `${result.swapOperationIds.length} swap${result.swapOperationIds.length === 1 ? '' : 's'} queued` : 'Already in place — nothing to do'}</p>
            {!prefetchOnly && result.swapOperationIds.length > 0 && (
              <Link href="/swaps?active=1" className="inline-flex items-center gap-1 text-xs font-medium underline-offset-4 hover:underline" onClick={() => close(false)}>
                Follow progress on Swaps <ArrowUpRight className="size-3" />
              </Link>
            )}
          </div>
        ) : preview.isPending || !diff ? (
          preview.isError ? (
            <p className="rounded-md bg-destructive/10 p-3 text-sm text-destructive">{preview.error instanceof Error ? preview.error.message : 'Preview failed'}</p>
          ) : (
            <div className="space-y-2" aria-busy>
              <Skeleton className="h-14 rounded-lg" />
              <Skeleton className="h-14 rounded-lg" />
              <Skeleton className="h-14 rounded-lg" />
            </div>
          )
        ) : (
          <div className="space-y-4">
            <div className="grid grid-cols-3 gap-2 text-xs">
              <div className="rounded-lg bg-muted/50 px-3 py-2">
                <p className="text-[11px] text-muted-foreground">Changes</p>
                <p className="text-sm font-semibold tabular-nums">{changes.length}</p>
              </div>
              <div className="rounded-lg bg-muted/50 px-3 py-2">
                <p className="flex items-center gap-1 text-[11px] text-muted-foreground">
                  <Clock className="size-3" /> Estimated
                </p>
                <p className="text-sm font-semibold tabular-nums">{diff.estimatedSeconds !== null ? `~${formatSeconds(diff.estimatedSeconds)}` : '—'}</p>
              </div>
              <div className="rounded-lg bg-muted/50 px-3 py-2">
                <p className="flex items-center gap-1 text-[11px] text-muted-foreground">
                  <Download className="size-3" /> Prefetch
                </p>
                <p className="text-sm font-semibold tabular-nums">{prefetchBytes ? gib(prefetchBytes) : 'none'}</p>
              </div>
            </div>
            {!diff.feasible && (
              <p className="flex items-start gap-2 rounded-md bg-destructive/10 p-3 text-xs text-destructive">
                <CircleAlert className="mt-px size-3.5 shrink-0" /> This layout does not fit on the GPUs as planned. Fix the placement before applying.
              </p>
            )}
            {diff.warnings.map((w) => (
              <p key={w} className="flex items-start gap-2 rounded-md border border-warning/30 bg-warning/10 p-3 text-xs text-warning-dark dark:text-warning-light">
                <CircleAlert className="mt-px size-3.5 shrink-0" /> {w}
              </p>
            ))}
            <ServerDiffList entries={diff.entries} />
            <div className="grid gap-3 border-t border-border pt-4 sm:grid-cols-2">
              <label className="space-y-1.5 text-xs font-medium">
                Drain timeout
                <div className="relative">
                  <Input type="number" min={0} max={3600} value={drain} onChange={(e) => setDrain(Math.max(0, Math.min(3600, Math.round(Number(e.target.value)) || 0)))} className="h-9 pr-8 font-mono text-xs" />
                  <span className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-[11px] text-muted-foreground">s</span>
                </div>
                <span className="block text-[11px] font-normal text-muted-foreground">Wait for in-flight requests before unloading; the swap rolls back on timeout.</span>
              </label>
              <div className="space-y-1.5 text-xs font-medium">
                <span className="flex items-center justify-between">
                  Only prefetch weights
                  <Switch checked={prefetchOnly} onCheckedChange={setPrefetchOnly} aria-label="Only prefetch weights" />
                </span>
                <span className="block text-[11px] font-normal text-muted-foreground">Download and pin the models now; switch later (or let the schedule do it).</span>
              </div>
            </div>
          </div>
        )}

        <DialogFooter>
          {result ? (
            <Button size="sm" onClick={() => close(false)}>
              Done
            </Button>
          ) : (
            <>
              <Button variant="outline" size="sm" onClick={() => close(false)}>
                Cancel
              </Button>
              <Button
                size="sm"
                disabled={!diff || !layout || apply.isPending || (!diff.feasible && !prefetchOnly) || (changes.length === 0 && !prefetchOnly)}
                onClick={() => layout && apply.mutate({ id: layout.id, body: { drain_timeout_seconds: drain, prefetch_only: prefetchOnly } }, { onSuccess: setResult })}
              >
                {apply.isPending ? <Loader2 className="size-4 animate-spin" /> : prefetchOnly ? <Download className="size-4" /> : <Play className="size-4" />}
                {prefetchOnly ? 'Start prefetch' : changes.length ? `Apply ${changes.length} change${changes.length === 1 ? '' : 's'}` : 'Nothing to apply'}
              </Button>
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
