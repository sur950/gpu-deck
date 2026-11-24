import { Eye } from 'lucide-react';
import { cn } from '@/lib/utils';

/**
 * "External" marker for deployments / instances GPU Deck did not start and only observes
 * (agent [[observe]]). Neutral styling: it is a fact, not a state.
 */
export function ExternalBadge({
  className,
  compact = false,
}: {
  className?: string;
  compact?: boolean;
}) {
  return (
    <span
      className={cn(
        'inline-flex shrink-0 items-center gap-1 rounded-sm border border-border bg-muted px-1.5 font-medium text-muted-foreground',
        compact ? 'h-4 text-[10px]' : 'h-5 text-[11px]',
        className,
      )}
      title="Observed only: GPU Deck reads its metrics and logs but never starts, stops, scales, routes or benchmarks it"
    >
      <Eye className={compact ? 'size-2.5' : 'size-3'} aria-hidden />
      External
    </span>
  );
}
