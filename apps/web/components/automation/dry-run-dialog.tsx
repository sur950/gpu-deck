'use client';

import { useEffect, useRef } from 'react';
import Link from 'next/link';
import { FlaskConical, RefreshCw } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Skeleton } from '@/components/ui/skeleton';
import { useDryRunRule } from '@/hooks/api/automation';
import { DecisionView } from '@/components/automation/decision-view';

interface DryRunDialogProps {
  rule: { id: string; name: string } | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/**
 * "Dry run now": evaluates the rule immediately against live metrics
 * (works on disabled rules too), shows the decision, changes nothing.
 */
export function DryRunDialog({ rule, open, onOpenChange }: DryRunDialogProps) {
  const dryRun = useDryRunRule({ silent: true });
  const started = useRef<string | null>(null);
  const { mutate, reset } = dryRun;

  useEffect(() => {
    if (open && rule && started.current !== rule.id) {
      started.current = rule.id;
      mutate(rule.id);
    }
    if (!open) {
      started.current = null;
      reset();
    }
  }, [open, rule, mutate, reset]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 text-base">
            <FlaskConical className="size-4" />
            Dry run — {rule?.name}
          </DialogTitle>
          <DialogDescription>Evaluated now against live metrics. Nothing is loaded, unloaded or moved.</DialogDescription>
        </DialogHeader>
        {dryRun.isPending && (
          <div className="space-y-3" aria-busy>
            <div className="flex items-center gap-3">
              <Skeleton className="size-9 rounded-lg" />
              <div className="space-y-1.5">
                <Skeleton className="h-4 w-32" />
                <Skeleton className="h-3 w-72" />
              </div>
            </div>
            <div className="grid gap-1.5 sm:grid-cols-2">
              {Array.from({ length: 4 }).map((_, i) => (
                <Skeleton key={i} className="h-8 rounded-lg" />
              ))}
            </div>
            <Skeleton className="h-28 rounded-lg" />
          </div>
        )}
        {dryRun.isError && <p className="rounded-md bg-destructive/10 p-3 text-sm text-destructive">{dryRun.error instanceof Error ? dryRun.error.message : 'Dry run failed'}</p>}
        {dryRun.data && <DecisionView run={dryRun.data} />}
        <DialogFooter>
          {rule && (
            <Link href={`/rules/${rule.id}`} className="mr-auto self-center text-xs text-muted-foreground underline-offset-4 hover:text-foreground hover:underline" onClick={() => onOpenChange(false)}>
              Open run history
            </Link>
          )}
          <Button variant="outline" size="sm" disabled={!rule || dryRun.isPending} onClick={() => rule && mutate(rule.id)}>
            <RefreshCw className="size-3.5" /> Run again
          </Button>
          <Button size="sm" onClick={() => onOpenChange(false)}>
            Done
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
